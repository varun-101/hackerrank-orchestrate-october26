"""Keyword-rule intent classifier for request_text (PLAN section 5.3).

The standard milestone 3 path uses the LLM as primary and these frozen rules as its cross-check.
Rules alone decide only in the explicit keyword-only diagnostic mode.
They are built for precision, not coverage: they authorize only clear deletion requests and send
anything mixed or unusual to UNSURE, which stops the pipeline for review. Rules are general
phrases in English, Spanish, French, Portuguese, Japanese and Korean, never case ids or whole
request texts.

How a text is decided. The text is split into clauses (sentences, then contrast words such as
"but", "pero", "mais", "mas", "しかし", "지만"). Each clause gets the first kind that matches:
1. POLICY references ("deletion policy", "politique de suppression") are cut out first; they never
   authorize anything.
2. STATE phrases, done or not ("had been deleted", "has not been deleted", "削除されていません"),
   are cut out before the refusal check, so a complaint that deletion did not happen is not read
   as a refusal.
3. refusal:     a negation of the deletion verb itself. Only helper words ("want", "you", "to",
   "que", adverbs such as "permanently") or a comma-enclosed aside may stand between them: "do
   not, under any circumstances, delete" is a refusal, "no uso mi cuenta y quiero eliminarla" is
   not. Japanese and Korean negate after the verb ("삭제를 원하지 않습니다"). Also a
   marketing-only scope limit.
4. cancel:      cancelling or withdrawing a deletion ("cancel my deletion request").
5. question:    asking for information about deletion ("explain whether you delete accounts",
   "How do I delete my account?", "show me how to delete my account"), unless it asks about a
   deletion state ("tell me whether my account was deleted" is an audit).
6. negation:    a negation shortly before the deletion wording that is not a clear refusal ("I
   don't think you should delete my account"); its scope is unknown, so the text is UNSURE.
7. conditional: deletion under a condition ("If my account is still active, delete it").
8. audit:       asking to verify an earlier deletion ("verify that my account was deleted").
9. request:     an actual deletion request: polite or imperative form, a stated wish, a request
   noun, or a first-person declaration ("please delete", "I'm deleting my account", "supprimez").
   Audit and request count only when the clause names what is deleted: the account, profile,
   personal data or records ("delete my account"), or a pronoun or "the deletion" pointing back
   to one in this or the previous clause ("I no longer use my account. Please delete it."). A
   field removed from the account is not the account: "delete the old email from my profile" and
   "I have old emails in my account. Please delete them." are not requests.
10. keyword:    deletion wording with none of the above (a narrative such as "I deleted the app").
OTHER rules (access, export, correction, marketing opt-out, login help, keeping the account) run on
the whole text. A residual-data complaint counts as an audit of the earlier deletion: a clause
saying the account, profile or data was deleted ("I closed my account", "the account had been
deleted") plus "still", "todavía", "まだ" and so on, with no refusal or other intent.

    request or audit, nothing blocking                         -> DELETE_AUTHORIZED
    request or audit plus refusal, cancel, negation,
        conditional or other                                   -> UNSURE (mixed)
    no request or audit, a bare keyword, negation or condition -> UNSURE (unresolved)
    only refusal, cancel, question, policy or other intent     -> NOT_APPLICABLE
    nothing matched                                            -> UNSURE

UNSURE is never guessed: the pipeline must stop before publishing predictions and list the
unresolved requests for review.

Report from the repository root:  python code/intent.py --report
"""

import argparse
import csv
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

DATASET = Path(__file__).resolve().parent.parent / "dataset"

# Deletion wording per language. Spanish and Portuguese share "elimin".
EN = r"\b(?:delet\w*|eras(?:e|ed|ing|ure)|remov\w*|clos(?:e|ed|ing|ure)|purg\w*|wip(?:e|ed|ing))\b"
ES = r"\b(?:elim[ií]n|borr|suprim)\w*"
FR = r"\b(?:supprim|suppression|effac|clôtur)\w*"
PT = r"\b(?:exclu|apag|elim[ií]n|remov)\w*"
JA = r"(?:削除|消去|退会|抹消)"
KO = r"(?:삭제|탈퇴|파기)"


def negated(negation, helpers, deletion):
    """Negation that governs the deletion verb: only helper words or a ", aside," in between."""
    return negation + r"(?:\s*(?:" + helpers + r")\b|\s*,[^,.;]{1,40},)*\s*" + deletion


# Adverbs ("permanently", "automáticamente", "définitivement") are helpers too: "do not permanently delete".
EN_HELPERS = (r"want|wish|need|like|ask|asking|request|requesting|intend|mean|plan|expect|you|to|a|an|the|me|"
              r"my|it|them|this|that|for|please|ever|actually|really|account|profile|data|anything|any|of|be|get|"
              r"just|yet|\w+ly")
ES_HELPERS = (r"quiero|quisiera|deseo|pido|solicito|que|me|se|lo|la|los|las|le|les|mi|mis|su|sus|cuenta|datos|"
              r"perfil|por|favor|nada|de|usted|ustedes|\w+mente")
FR_HELPERS = (r"veux|voudrais|souhaite|désire|demande|pas|plus|jamais|que|vous|de|d'|le|la|les|l'|me|m'|mon|ma|"
              r"mes|compte|données|profil|rien|merci|\w+ment")
PT_HELPERS = (r"quero|queria|gostaria|desejo|peço|solicito|que|me|se|o|a|os|as|meu|minha|meus|minhas|conta|"
              r"dados|perfil|por|favor|nada|de|você|vocês|\w+mente")


def unscoped(negation, boundaries, deletion):
    """A negation followed within six words by deletion wording, with no clause boundary word in between."""
    return negation + r"\s*(?:(?!(?:" + boundaries + r")\b)[\w'-]+\s+){0,6}?" + deletion

POLICY_RULES = [
    ("en.policy", r"\b(?:deletion|retention|privacy|erasure|data[- ]protection)\b(?:\s+[\w-]+){0,2}?\s+"
                  r"(?:polic(?:y|ies)|procedures?|requirements|rules|practices)\b"),
    ("es.policy", r"\bpolíticas?\s+de\s+(?:\w+\s+)?(?:eliminación|borrado|supresión|privacidad|retención|conservación)"),
    ("fr.policy", r"\bpolitiques?\s+de\s+(?:\w+\s+)?(?:suppression|confidentialité|conservation)"),
    ("pt.policy", r"\bpolíticas?\s+de\s+(?:\w+\s+)?(?:exclusão|privacidade|retenção)"),
    ("ja.policy", r"(?:削除|消去|退会|個人情報|プライバシー)(?:の|に関する)?(?:ポリシー|方針|規定|規約|方法)"),
    ("ko.policy", r"(?:삭제|탈퇴|개인정보)\s*(?:처리\s*)?(?:정책|방침|방법)"),
]

STATE_RULES = [
    ("en.state", r"\b(?:is|are|was|were|has|have|had|been|did)(?:n't)?\s+(?:(?:you|they|we|it|not|never|yet|still|"
                    r"been|fully|completely|actually|really|already)\s+){0,4}"
                    r"(?:deleted|erased|removed|closed|gone|delete|erase|remove)\b"),
    ("es.state", r"\bno\s+(?:se\s+)?(?:(?:ha|han|había|habían|fue|fueron|está|están)\s+)?(?:sido\s+)?"
                    r"(?:eliminad|borrad|suprimid|elimin[oó]|borr[oó]|suprimi[oó])\w*"),
    ("fr.state", r"(?:\bn'|\bne\s+)(?:a|ont|est|sont)\s+(?:toujours\s+)?(?:pas|jamais)\s+(?:encore\s+)?(?:été\s+)?"
                    r"(?:supprim|effac|clôtur)\w*"),
    ("pt.state", r"\bnão\s+(?:foi|foram|está|estão|tem|têm|teve)\b[^.?!]{0,20}?(?:excluíd|apagad|eliminad|removid)\w*"),
    ("ja.state", JA + r"(?:が|は)?(?:され|完了して|完了し|でき)(?:てい|てお|い)?(?:ない|ません|なかった)"),
    ("ko.state", KO + r"(?:가|는|이)?\s*(?:되지|안\s*됐|안\s*되|완료되지)"),
]

REFUSAL_RULES = [
    ("en.negated", negated(r"(?:\bnot\b(?!\s+(?:only|just)\b)|n't\b|\bnever\b|\bwithout\b|\bdont\b|\bno\b(?!\s+longer\b))",
                           EN_HELPERS, EN)),
    ("en.marketing_only", r"\b(?:marketing|mailing|newsletter|promotional)\b.*\bonly\b"
                          r"|\bonly\b.*\b(?:marketing|mailing|newsletter|promotional)\b"),
    ("es.negated", negated(r"\b(?:no|nunca|sin|ni|tampoco)\b", ES_HELPERS, ES)),
    ("fr.negated", negated(r"(?:\bne\b|\bn'|\bsans\b|\bpas\b|\bjamais\b)", FR_HELPERS, FR)),
    ("pt.negated", negated(r"\b(?:não|nunca|sem|nem)(?!\w)", PT_HELPERS, PT)),
    ("ja.negated", JA + r"[^。]{0,8}?(?:ない(?!か|でしょうか)|ません(?!か)|せず|不要|結構です|やめて)"),
    ("ko.negated", KO + r"[^.?!]{0,10}?(?:하지\s*마|하지\s*말|않|안\s*(?:해|할|하)|원치|싫)"),
]

# A negation near the deletion wording that is not a clear refusal: its scope is unknown, so the clause
# cannot authorize. "and", "y", "et", "e" end the scope: "no uso mi cuenta y quiero eliminarla" is fine.
# Japanese and Korean negate after the verb, which the refusal rules already cover.
NEGATION_RULES = [
    ("en.unscoped", unscoped(r"(?:\bnot\b(?!\s+(?:only|just)\b)|n't\b|\bnever\b|\bwithout\b|\bdont\b|\bno\b(?!\s+longer\b))",
                             r"and|or|so|then|please|because|since", EN)),
    ("es.unscoped", unscoped(r"\b(?:no|nunca|sin|ni|tampoco)\b", r"y|e|o|así|entonces|porque|ya|por\s+favor", ES)),
    ("fr.unscoped", unscoped(r"(?:\bne\b|\bn'|\bsans\b|\bjamais\b)", r"et|ou|donc|alors|car|puis|merci", FR)),
    ("pt.unscoped", unscoped(r"\b(?:não|nunca|sem|nem)(?!\w)", r"e|ou|então|porque|já|por\s+favor", PT)),
]

CANCEL_RULES = [
    ("en.cancel", r"\b(?:cancel|withdraw|revoke|retract|rescind|undo|stop|halt|abort)\w*\s+(?:\w+\s+){0,3}?"
                  r"(?:deletion|erasure|removal|closure|request)\b|\bno longer (?:want|wish|need)\w*\b.*" + EN),
    ("es.cancel", r"\b(?:cancel|anul|retir|revoc|desist)\w*\s+(?:\w+\s+){0,3}?"
                  r"(?:solicitud|petición|pedido|eliminación|borrado|supresión)"),
    ("fr.cancel", r"\b(?:annul|retir|révoqu|arrêt)\w*\s+(?:\w+\s+){0,3}?(?:demande|requête|suppression|effacement)"),
    ("pt.cancel", r"\b(?:cancel|anul|retir|revog|desist)\w*\s+(?:\w+\s+){0,3}?"
                  r"(?:pedido|solicitação|exclusão|eliminação|remoção)"),
    ("ja.cancel", JA + r"[^。]{0,10}?(?:取り消|取消|キャンセル|撤回|中止|取り下げ|取りやめ)"),
    ("ko.cancel", KO + r"[^.?!]{0,10}?(?:취소|철회|중단|중지)"),
]

# Asking for information about deletion. "explain" style markers need the deletion wording after them,
# so "delete my account and let me know when it is done" stays a request. Direct questions need a "?".
QUESTION_RULES = [
    # Asking how to delete ("show me how to delete", "instructions to close my account") is informational.
    ("en.how_to", r"\bhow\s+(?:to|(?:do|can|could|should|would|might)\s+(?:i|we|one)|i\s+(?:can|could|should))\s+"
                  r"(?:\w+\s+){0,2}?(?:delet|eras|remov|clos|purg|wip)\w*"
                  r"|\b(?:instructions?|step-by-step|walk me through|guide me)\b.*" + EN),
    ("es.how_to", r"\bc[oó]mo\s+(?:puedo\s+|se\s+puede\s+|hago\s+para\s+|debo\s+)?(?:eliminar|borrar|suprimir)\b"
                  r"|\b(?:instrucciones|pasos para)\b.*" + ES),
    ("fr.how_to", r"\bcomment\s+(?:puis-je\s+|faire\s+pour\s+|je\s+peux\s+|peut-on\s+)?(?:supprimer|effacer|clôturer)\b"
                  r"|\b(?:instructions?|étapes? pour|marche à suivre)\b.*" + FR),
    ("pt.how_to", r"\bcomo\s+(?:posso\s+|faço\s+para\s+|devo\s+)?(?:excluir|apagar|eliminar|remover)\b"
                  r"|\b(?:instruções|passos para|passo a passo)\b.*" + PT),
    ("ja.how_to", JA + r"[^。]{0,6}?(?:方法|手順|やり方|仕方)"),
    ("ko.how_to", KO + r"[^.?!]{0,6}?(?:방법|절차)"),
    ("en.explain", r"\b(?:explain|tell me|let me know|inform me|clarify|wonder\w*|curious|know)\s+(?:\w+\s+){0,2}?"
                   r"(?:whether|if|how|what|why|when|which|where)\b.*" + EN),
    ("en.direct", r"^\W*(?:(?:hi|hello|hey)\W+)?(?:how|what|why|when|which|where|do you|does)\b.*[?？]"),
    ("es.explain", r"\b(?:expli\w+|d[ií]ga\w*|decir\w*|saber|inform\w*|aclar\w+)\s+(?:\w+\s+){0,2}?"
                   r"(?:si|cómo|qué|cuándo|por qué)\b.*" + ES),
    ("es.direct", r"^\W*(?:cómo|qué|cuál|cuándo|por qué|dónde)\b.*[?？]"),
    ("fr.explain", r"\b(?:expliqu\w*|dites-moi|indique\w*|savoir|précise\w*)\s+(?:\w+\s+){0,2}?"
                   r"(?:si|s'il|comment|quand|pourquoi|quel\w*)\b.*" + FR),
    ("fr.direct", r"^\W*(?:comment|quand|pourquoi|quel\w*|est-ce que)\b.*[?？]"),
    ("pt.explain", r"\b(?:expli\w+|diga\w*|dizer|saber|inform\w*|esclare\w+)\s+(?:\w+\s+){0,2}?"
                   r"(?:se|como|quando|por que|qual)\b.*" + PT),
    ("pt.direct", r"^\W*(?:como|quando|por que|qual|o que)\b.*[?？]"),
    ("ja.explain", JA + r"[^。]{0,6}?かどうか|" + JA + r"[^。]{0,6}?か(?:を|について)?\s*(?:教えて|説明|知りたい)"),
    ("ja.direct", r"(?:どう|どのよう|なぜ|いつ|何)[^。]*(?:か|[?？])\W*$"),
    ("ko.explain", KO + r"[^.?!]{0,8}?(?:는지|은지|인지|될지|할지)\s*(?:알려|설명|궁금)"),
    ("ko.direct", r"(?:어떻게|무엇|왜|언제|뭐)[^.?!]*(?:까|\?)\W*$"),
]

CONDITIONAL_RULES = [
    ("en.condition", r"^\W*(?:if|unless|in case)\b|\b(?:only if|unless|provided that|as long as)\b"),
    ("es.condition", r"^\W*(?:si|a menos que|en caso de que)\b|\b(?:solo si|sólo si|siempre que|a menos que)\b"),
    ("fr.condition", r"^\W*(?:si|s'il|à moins que|au cas où)\b|\b(?:seulement si|à condition que|à moins que)\b"),
    ("pt.condition", r"^\W*(?:se|caso|a menos que)\b|\b(?:somente se|só se|desde que|a menos que)\b"),
    ("ja.condition", r"もし|場合は|のであれば|ならば"),
    ("ko.condition", r"만약|경우에?는"),
]

AUDIT_RULES = [
    ("en.verify", r"\b(?:verif\w*|confirm\w*|check\w*|audit\w*|inspect\w*|investigat\w*|make sure|ensure|"
                  r"tell me (?:whether|if))\b.*\b(?:delet\w*|eras\w*|remov\w*|gone|left behind|still exists?|clos(?:ed|ure))\b"),
    ("es.verify", r"\b(?:comprob|verific|confirm|revis|audit)\w*.*\b(?:eliminad|borrad|suprimid|eliminación|borrado|supresión)\w*"),
    ("fr.verify", r"\b(?:vérifi|confirm|contrôl)\w*.*\b(?:supprimé|effacé|suppression|effacement)\w*"),
    ("pt.verify", r"\b(?:verifi|confirm|checa|audit)\w*.*\b(?:excluíd|apagad|eliminad|removid|exclusão|eliminação)\w*"),
    ("ja.verify", JA + r"[^。]*?(?:確認|検証|監査|調査)(?:して|を(?:お願い|依頼))"),
    ("ko.verify", KO + r"[^.?!]*?(?:확인|검증|감사)\s*(?:해\s*주|부탁|요청|바랍)"),
]

REQUEST_RULES = [
    ("en.polite", r"\b(?:please|kindly|can you|could you|would you|will you|asking you to|ask you to|"
                  r"like you to|want you to|need you to)\b.*\b(?:delete|erase|remove|purge|wipe|"
                  r"close\s+(?:\w+\s+){0,3}?(?:account|profile))\b"),
    ("en.imperative", r"(?:^\W*|,\s*|\b(?:then|and|also)\s+)(?:please\s+)?(?:delete|erase|remove|purge|wipe|"
                      r"close\s+(?:\w+\s+){0,3}?(?:account|profile))\b"),
    ("en.wish", r"\b(?:want|would like|'d like|wish|need|prefer)\b.*(?:" + EN + r"|\bgone\b)"),
    ("en.declare", r"\b(?:i'm|i am|we're|we are)\s+(?:now\s+)?(?:delet|eras|clos|remov)ing\s+"
                   r"(?:my|our|this|the)\s+(?:\w+\s+)?(?:account|profile|personal data|data)\b"),
    ("en.request_noun", r"\b(?:request\w*|ask\w*|submit\w*)\b.*\b(?:deletion|erasure|removal|closure|to delete|"
                        r"to erase|to remove)\b|\b(?:deletion|erasure|removal)\s+request\b"),
    ("en.process_deletion", r"\b(?:begin|start|process|complete|finish|proceed with|carry out|go ahead with|initiate|perform)\b"
                            r".*\b(?:deletion|erasure|removal)\b"),
    ("es.request", r"\b(?:por favor|favor de|quiero|quisiera|deseo|solicito|pido|necesito|pueden|podrían|puede|podría|"
                   r"me gustaría)\b.*" + ES + r"|\b(?:elimin|elimín|borr|suprim)(?:en|e|ad)(?:la|lo|las|los)?\b"),
    ("fr.request", r"(?:\bveuillez|\bmerci de|\bpouvez-vous|\bpourriez-vous|\bje (?:veux|voudrais|souhaite|demande)|"
                   r"\bje vous (?:prie|demande)|\bs'il vous plaît|\bsvp)\b.*" + FR + r"|\b(?:supprimez|effacez|clôturez)\b"),
    ("pt.request", r"\b(?:por favor|solicito|peço|quero|gostaria|desejo|preciso|podem|poderiam|pode|poderia)\b.*" + PT +
                   r"|\b(?:excluam|exclua|apaguem|apague|eliminem|elimine|removam|remova)\b"),
    ("ja.request", JA + r"[^。]*?(?:してください|して下さい|お願い|依頼|希望|求め|したい|してほしい|して欲しい|申請|要求|"
                   r"いただけ|もらえ|くれませんか)"),
    ("ko.request", KO + r"[^.?!]*?(?:해\s*주|하고\s*싶|원합니다|원해|요청|신청|바랍니다|부탁)"),
]

KEYWORD_RULES = [("en", EN), ("es", ES), ("fr", FR), ("pt", PT), ("ja", JA), ("ko", KO)]

# What a deletion request must be about. 退会 / 탈퇴 (leaving the service) name the account themselves.
# "account" or "profile" as a modifier of a field ("profile picture", "account email") names the field.
FIELD_NOUNS = (r"e-?mails?|(?:e-?mail\s+)?address(?:es)?|phone|(?:phone\s+)?numbers?|names?|nicknames?|usernames?"
               r"|pictures?|photos?|images?|avatar|bio|settings|preferences|passwords?")
OBJECT_WORDS = (r"\b(?:account|profile)(?!\s+(?:" + FIELD_NOUNS + r")\b)|\bpersonal[- ](?:data|information|info|details)"
                r"|\b(?:my|our|all|its|the|any)\s+(?:\w+\s+)?(?:data|information|details|records)\b"
                r"|\brecords?\b|\bidentifiers?\b|\bcontact details\b|\bmy name\b|\bcop(?:y|ies)\b|\btraces?\b"
                r"|\b(?:cuenta|perfil|datos?|información personal|registros)\b"
                r"|\b(?:compte|profil|données|informations personnelles)\b"
                r"|\b(?:conta|dados|informações pessoais|registros)\b"
                r"|アカウント|個人情報|データ|プロフィール|会員|登録情報|退会|계정|개인정보|데이터|프로필|회원|탈퇴")
# Words that point back to an object named elsewhere in the text: pronouns, clitics, "the deletion".
# Japanese and Korean drop the object, so a deletion verb without を / 을 / 를 before it inherits one.
REFERENCE_WORDS = (r"\b(?:it|them|this|everything|anything|nothing|the rest)\b"
                   r"|\b(?:the|my|this)\s+(?:\w+\s+)?(?:deletion|erasure|removal|closure)\b"
                   r"|\b(?:elimin|elimín|borr|suprim)\w*(?:la|lo)s?\b|\b(?:todo|nada)\b"
                   r"|\b(?:le|la|les|l')\s*(?=supprim|effac)|\b(?:tout|rien)\b"
                   r"|\b(?:exclu|apag|elimin|remov)\w*-(?:la|lo)s?\b|\b(?:tudo|nada)\b"
                   r"|(?:^|[^を])" + JA + r"|(?:^|[^을를\s])\s?" + KO)
# Removing a field from the account or profile ("delete the old email address from my profile") is a
# correction: the account named after "from" is where the field lives, not what is deleted. The whole
# span is cut out before the object check.
FIELD_REMOVAL_WORDS = (
    EN + r"(?:\s+[\w'-]+){0,4}?\s+(?:" + FIELD_NOUNS + r")\s+(?:from|in|on|out of|off)\s+(?:my|the|our|this)\s+"
    r"(?:\w+\s+)?(?:account|profile)\b"
    r"|" + ES + r"(?:\s+\w+){0,4}?\s+(?:correo|teléfono|número|dirección|nombre|foto)\w*(?:\s+\w+){0,2}?\s+"
    r"(?:de|del|en)\s+(?:mi|su)\s+(?:cuenta|perfil)\b"
    r"|" + FR + r"(?:\s+[\w'-]+){0,4}?\s+(?:adresse|e-?mail|courriel|téléphone|numéro|nom|photo)\w*(?:\s+[\w'-]+){0,2}?\s+"
    r"(?:de|du|dans)\s+(?:mon|ma)\s+(?:compte|profil)\b"
    r"|" + PT + r"(?:\s+\w+){0,4}?\s+(?:e-?mail|telefone|número|endereço|nome|foto)\w*(?:\s+\w+){0,2}?\s+"
    r"(?:da|do|de|na|no)\s+(?:minha|meu)\s+(?:conta|perfil)\b")
# English pronouns and the plural object words they may point back to ("delete them" needs "records").
EN_PRONOUN = re.compile(r"\b(?:it|this|them|these|those)\b", re.IGNORECASE)
PLURAL_OBJECT = re.compile(r"data|records|details|identifiers|copies|traces|information", re.IGNORECASE)
# A determiner opens a noun phrase; one after the object word may be the pronoun's real antecedent.
NOUN_PHRASE = re.compile(r"\b(?:a|an|the|some|my|our|your|his|her|their|these|those|old|new)\s+\w+", re.IGNORECASE)
# An object word after one of these is where something is ("an old photo in my account"), not the antecedent.
LOCATION = re.compile(r"\b(?:in|on|from|within|inside|under)\s+(?:my|the|our|this)\s+(?:\w+\s+)?$", re.IGNORECASE)
# A deletion that already happened (past tense), for the residual-data complaint.
PAST_DELETION_WORDS = (r"\b(?:deleted|erased|removed|closed|wiped|purged)\b"
                       r"|\b(?:eliminé|borré|suprimí|eliminó|borró|eliminad[ao]s?|borrad[ao]s?|suprimid[ao]s?)\b"
                       r"|\b(?:supprimée?s?|effacée?s?|clôturée?s?)\b"
                       r"|\b(?:excluí|apaguei|eliminei|removi|excluíd[ao]s?|apagad[ao]s?|removid[ao]s?)\b"
                       r"|" + JA + r"(?:し(?:まし)?た|済み|された)|" + KO + r"(?:했|됐|되었)")

# Non-deletion intents, matched on the whole text. Any of these next to a deletion request makes it UNSURE.
OTHER_RULES = [
    ("access", r"\bwhat (?:personal )?(?:information|data) you hold\b|\binformation request\b|\baccess to my (?:data|information)\b"),
    ("export", r"\b(?:send|give|need|want|get|request)\w*\s+(?:me\s+)?a copy\b|\bdownload my\b|\bportable\b"
               r"|\bexport\s+(?:my|all|our|a copy)\b|\b(?:can|could|would|please|to)\s+(?:you\s+)?export\b"
               r"|\bcopia de\b|\bcopie de\b|\bcópia\b|コピー|開示|사본|열람"),
    ("correction", r"\b(?:correct|update|replace|fix|change)\s+(?:it|my|the|this)\b|\bmisspelled\b|\bout of date\b"
                   r"|\b(?:add|use|put|replace\w*|with)\s+(?:it\s+with\s+)?(?:the|my|a)\s+new\b"
                   r"|\b(?:corregir|actualizar|cambiar|corriger|modifier|corrigir|atualizar|alterar)\b|訂正|변경|정정"),
    ("marketing_opt_out", r"\b(?:marketing|promotional|newsletter|unsubscribe|opt[- ]out|mailing list)\b|\bpublicidad\b|配信停止|수신 거부"),
    ("login_help", r"\breset\w*\b[^.?!]*\b(?:login|password)\b|\bsign in\b|\blog ?in\b"),
    ("keep_account", r"\b(?:keep|leave)\s+(?:the|my)\s+(?:account|profile)\b|\bstill (?:need|use|want)\b"
                     r"|\bcontinue using\b|\b(?:reactivate|reopen)\b"),
]


def _compile(rules):
    return [(name, re.compile(p, re.IGNORECASE)) for name, p in rules]


POLICY, STATE, REFUSAL, CANCEL = _compile(POLICY_RULES), _compile(STATE_RULES), _compile(REFUSAL_RULES), _compile(CANCEL_RULES)
NEGATION, QUESTION, CONDITIONAL = _compile(NEGATION_RULES), _compile(QUESTION_RULES), _compile(CONDITIONAL_RULES)
AUDIT, REQUEST, OTHER = _compile(AUDIT_RULES), _compile(REQUEST_RULES), _compile(OTHER_RULES)
OBJECT = re.compile(OBJECT_WORDS, re.IGNORECASE)
REFERENCE = re.compile(REFERENCE_WORDS, re.IGNORECASE)
FIELD_REMOVAL = re.compile(FIELD_REMOVAL_WORDS, re.IGNORECASE)
PAST_DELETION = re.compile(PAST_DELETION_WORDS, re.IGNORECASE)
STILL = re.compile(r"\bstill\b|\btodavía\b|\baún\b|\bsigue\w*|\btoujours\b|\bencore\b|\bainda\b|\bcontinua\w*"
                   r"|まだ|残って|依然|아직|여전히|남아", re.IGNORECASE)
DELETION_WORD = re.compile("|".join(p for _, p in KEYWORD_RULES), re.IGNORECASE)
CLAUSE_SPLIT = re.compile(r"(?<=[.?!;。？！])\s*|,?\s+\b(?:but|however|pero|mais|mas)\b\s*"
                          r"|が、|けど|けれど|しかし|하지만|그러나|(?<=지만)\s*", re.IGNORECASE)

LANGUAGE_WORDS = {
    "es": {"la", "el", "cuenta", "correo", "pueden", "por", "favor", "de", "mi", "que", "si", "los"},
    "fr": {"je", "le", "la", "compte", "veuillez", "merci", "de", "du", "les", "votre", "une", "adresse"},
    "pt": {"a", "da", "conta", "por", "favor", "de", "os", "dos", "não", "meu", "minha", "quando"},
    "en": {"the", "my", "account", "please", "to", "and", "this", "i", "of", "is", "you", "it"},
}


def guess_language(text):
    if re.search(r"[぀-ヿ]", text):
        return "ja"
    if re.search(r"[가-힯]", text):
        return "ko"
    if re.search(r"[一-鿿]", text):
        return "ja"  # kanji without kana: the only CJK language in the dataset besides Korean
    words = re.findall(r"\w+", text.lower())
    scores = {lang: sum(w in vocab for w in words) for lang, vocab in LANGUAGE_WORDS.items()}
    best = max(scores, key=lambda lang: (scores[lang], lang == "en"))
    return best if scores[best] else "unknown"


def _hits(table, text):
    return [name for name, pattern in table if pattern.search(text)]


def names_object(clause, previous):
    """Whether the clause names what is deleted, itself or by a reference to an object just before it.

    A reference resolves only within this clause and the previous one, to the nearest object word. An
    English pronoun must also agree in number and have no other noun phrase between it and the object:
    "I have old emails in my account. Please delete them." does not name the account.
    """
    target = FIELD_REMOVAL.sub(" ", clause)
    if OBJECT.search(target):
        return True
    reference = REFERENCE.search(target)
    if not reference:
        return False
    before = previous + " " + target[:reference.start()]
    objects = list(OBJECT.finditer(before))
    if not objects:
        return False
    nearest = objects[-1]
    pronoun = EN_PRONOUN.fullmatch(reference.group())
    if pronoun:
        if NOUN_PHRASE.search(before[nearest.end():]) or LOCATION.search(before[:nearest.start()]):
            return False
        plural_object = bool(PLURAL_OBJECT.search(nearest.group()))
        plural_pronoun = pronoun.group().lower() in ("them", "these", "those")
        if plural_pronoun and not plural_object:
            return False
    return True


def classify_clause(clause, previous=""):
    """Return (kind, rules) for one clause; `previous` is the clause before it. Kind is None when nothing matched."""
    rules = []
    for name, pattern in POLICY:
        if pattern.search(clause):
            rules.append("policy:" + name)
            clause = pattern.sub(" ", clause)
    stripped = clause
    for name, pattern in STATE:
        if pattern.search(stripped):
            rules.append("state:" + name)
            stripped = pattern.sub(" ", stripped)
    has_state = stripped != clause
    mentions_deletion = bool(DELETION_WORD.search(clause))

    checks = [("refusal", REFUSAL, stripped), ("cancel", CANCEL, stripped)]
    if mentions_deletion and not has_state:
        checks.append(("question", QUESTION, stripped))
    checks.append(("negation", NEGATION, stripped))
    if mentions_deletion:
        checks.append(("conditional", CONDITIONAL, stripped))
    for kind, table, part in checks:
        names = _hits(table, part)
        if names:
            return kind, rules + [kind + ":" + name for name in names]

    # Audit and request read the clause before STATE removal: "check it was not deleted" is an audit.
    clause_names_object = names_object(clause, previous)
    for kind, table in (("audit", AUDIT), ("request", REQUEST)):
        names = _hits(table, clause)
        if names and clause_names_object:
            return kind, rules + [kind + ":" + name for name in names]
        rules += ["no_object:" + name for name in names]
    if mentions_deletion or has_state:
        if OBJECT.search(clause) and (has_state or PAST_DELETION.search(clause)):
            rules.append("past_deletion")
        return "keyword", rules + ["keyword:" + name for name, p in KEYWORD_RULES if re.search(p, stripped, re.I)]
    return ("policy" if any(r.startswith("policy:") for r in rules) else None), rules


def classify(text):
    """Return {"decision", "rules", "language"} for one request_text."""
    normalized = text.replace("’", "'").replace("‘", "'")
    kinds, rules, previous = set(), [], ""
    for clause in CLAUSE_SPLIT.split(normalized):
        if clause and clause.strip():
            kind, clause_rules = classify_clause(clause, previous)
            kinds.add(kind)
            rules += clause_rules
            previous = clause
    other = _hits(OTHER, normalized)
    rules += ["other:" + name for name in other]

    blocked = bool(kinds & {"refusal", "cancel", "conditional", "negation"} or other)
    # Residual-data complaint: the account or data was deleted, yet something "still" remains.
    if not blocked and not kinds & {"request", "audit"} and "past_deletion" in rules and STILL.search(normalized):
        kinds.add("audit")
        rules.append("audit:residual_data")
    if kinds & {"request", "audit"}:
        decision = "UNSURE" if blocked else "DELETE_AUTHORIZED"
    elif kinds & {"keyword", "conditional", "negation"}:
        decision = "UNSURE"
    elif blocked or kinds & {"question", "policy"}:
        decision = "NOT_APPLICABLE"
    else:
        decision = "UNSURE"
    return {"decision": decision, "rules": rules, "language": guess_language(text)}


def report():
    requests = []
    for name in ("sample_deletion_requests.csv", "deletion_requests.csv"):
        with open(DATASET / name, encoding="utf-8", newline="") as f:
            requests += list(csv.DictReader(f))
    cases_by_text = defaultdict(list)
    for request in requests:
        cases_by_text[request["request_text"]].append(request["case_id"])
    results = {text: classify(text) for text in cases_by_text}

    case_counts = Counter(results[r["request_text"]]["decision"] for r in requests)
    text_counts = Counter(result["decision"] for result in results.values())
    languages = Counter(results[r["request_text"]]["language"] for r in requests)
    print(f"{len(requests)} requests, {len(results)} unique texts")
    for decision in ("DELETE_AUTHORIZED", "NOT_APPLICABLE", "UNSURE"):
        print(f"  {decision:<18} {case_counts[decision]:>4} requests  {text_counts[decision]:>4} unique texts")
    print("  languages: " + ", ".join(f"{lang} {n}" for lang, n in sorted(languages.items())))
    for decision in ("NOT_APPLICABLE", "UNSURE"):
        print(f"\n{decision}:")
        for text, result in sorted(results.items(), key=lambda item: cases_by_text[item[0]][0]):
            if result["decision"] == decision:
                print(f"  {', '.join(cases_by_text[text])} [{result['language']}] {'; '.join(result['rules'])}")
                print(f"    {text}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Keyword-rule intent classifier.")
    parser.add_argument("--report", action="store_true", help="classify every request and list non-deletion texts")
    parser.add_argument("text", nargs="?", help="classify one text")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    if args.report:
        report()
    elif args.text:
        print(classify(args.text))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
