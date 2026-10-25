"""Tests for the keyword intent classifier. Run from the repository root: python -m unittest discover -s code/tests"""

import csv
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import intent  # noqa: E402

DATASET = Path(__file__).resolve().parent.parent.parent / "dataset"


class IntentTests(unittest.TestCase):
    def assertDecision(self, text, decision, language=None):
        result = intent.classify(text)
        self.assertEqual(result["decision"], decision, f"{text} -> {result}")
        if language:
            self.assertEqual(result["language"], language, text)

    def test_all_samples_are_deletion_requests(self):
        with open(DATASET / "sample_deletion_requests.csv", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                self.assertDecision(row["request_text"], "DELETE_AUTHORIZED")

    def test_deletion_in_each_language(self):
        self.assertDecision("Please erase my profile and all personal data.", "DELETE_AUTHORIZED", "en")
        self.assertDecision("Quiero borrar mi cuenta y todos mis datos, por favor.", "DELETE_AUTHORIZED", "es")
        self.assertDecision("Merci de supprimer mon compte et mes données.", "DELETE_AUTHORIZED", "fr")
        self.assertDecision("Por favor, apaguem a minha conta e os meus dados.", "DELETE_AUTHORIZED", "pt")
        self.assertDecision("退会して個人情報を消去してください。", "DELETE_AUTHORIZED", "ja")
        self.assertDecision("회원 탈퇴와 개인정보 삭제를 원합니다.", "DELETE_AUTHORIZED", "ko")

    def test_verification_of_earlier_deletion_is_a_deletion_audit(self):
        self.assertDecision("Can you confirm that my old account was removed last month?", "DELETE_AUTHORIZED")
        self.assertDecision("Please check that my profile is gone.", "DELETE_AUTHORIZED")
        self.assertDecision("Can you verify that my account was deleted?", "DELETE_AUTHORIZED")

    def test_close_account_without_delete_word(self):
        self.assertDecision("Please close my account for good.", "DELETE_AUTHORIZED")

    def test_non_deletion_requests(self):
        self.assertDecision("Please send me a copy of my order history.", "NOT_APPLICABLE")
        self.assertDecision("My phone number is out of date, please update it.", "NOT_APPLICABLE")
        self.assertDecision("Unsubscribe me from the newsletter.", "NOT_APPLICABLE")
        self.assertDecision("I forgot my password and cannot log in.", "NOT_APPLICABLE")

    def test_negated_and_scope_limited_deletion(self):
        self.assertDecision("Fix my address, but don't delete anything.", "NOT_APPLICABLE")
        self.assertDecision("This is a question, not a request to delete my account.", "NOT_APPLICABLE")
        self.assertDecision("Please remove my email from marketing lists only.", "NOT_APPLICABLE")
        self.assertDecision("No quiero eliminar mi cuenta, solo cambiar el correo.", "NOT_APPLICABLE", "es")
        self.assertDecision("Je ne veux pas supprimer mon compte.", "NOT_APPLICABLE", "fr")
        self.assertDecision("Não quero excluir a minha conta.", "NOT_APPLICABLE", "pt")
        self.assertDecision("アカウントは削除しないでください。", "NOT_APPLICABLE", "ja")
        self.assertDecision("계정은 삭제하지 마세요.", "NOT_APPLICABLE", "ko")

    def test_negation_stays_in_its_clause(self):
        self.assertDecision("Do not delete my invoices, but delete my account.", "UNSURE")
        self.assertDecision("I haven't used this account in years, please delete it.", "DELETE_AUTHORIZED")

    def test_refusals_are_not_authorization_in_each_language(self):
        self.assertDecision("I do not want you to delete my account.", "NOT_APPLICABLE", "en")
        self.assertDecision("Please don't remove anything, I still use the account.", "NOT_APPLICABLE", "en")
        self.assertDecision("No quiero que eliminen mi cuenta.", "NOT_APPLICABLE", "es")
        self.assertDecision("Por favor, no borren mis datos.", "NOT_APPLICABLE", "es")
        self.assertDecision("Je ne souhaite pas que vous supprimiez mon compte.", "NOT_APPLICABLE", "fr")
        self.assertDecision("Merci de ne pas supprimer mon compte.", "NOT_APPLICABLE", "fr")
        self.assertDecision("Não quero que excluam minha conta.", "NOT_APPLICABLE", "pt")
        self.assertDecision("Por favor, não apaguem meus dados.", "NOT_APPLICABLE", "pt")
        self.assertDecision("アカウントの削除は希望しません。", "NOT_APPLICABLE", "ja")
        self.assertDecision("アカウントを削除しないでください。", "NOT_APPLICABLE", "ja")
        self.assertDecision("계정 삭제를 원하지 않습니다.", "NOT_APPLICABLE", "ko")
        self.assertDecision("계정을 삭제하지 말아 주세요.", "NOT_APPLICABLE", "ko")

    def test_policy_questions_are_not_authorization_in_each_language(self):
        self.assertDecision("What is your deletion policy?", "NOT_APPLICABLE", "en")
        self.assertDecision("How do I delete my account?", "NOT_APPLICABLE", "en")
        self.assertDecision("¿Cuál es su política de eliminación de datos?", "NOT_APPLICABLE", "es")
        self.assertDecision("Quelle est votre politique de suppression des données ?", "NOT_APPLICABLE", "fr")
        self.assertDecision("Qual é a política de exclusão de dados?", "NOT_APPLICABLE", "pt")
        self.assertDecision("削除ポリシーについて教えてください。", "NOT_APPLICABLE", "ja")
        self.assertDecision("개인정보 삭제 정책이 궁금합니다.", "NOT_APPLICABLE", "ko")

    def test_failed_deletion_complaint_with_request_is_authorized_in_each_language(self):
        # A negated deletion state ("has not been deleted") is a complaint, not a refusal.
        self.assertDecision("My account has not been deleted yet, please delete it.", "DELETE_AUTHORIZED", "en")
        self.assertDecision("Mi cuenta todavía no ha sido eliminada. Elimínenla, por favor.", "DELETE_AUTHORIZED", "es")
        self.assertDecision("Mon compte n'a toujours pas été supprimé, merci de le supprimer.", "DELETE_AUTHORIZED", "fr")
        self.assertDecision("Minha conta ainda não foi excluída. Por favor, excluam todos os meus dados.",
                            "DELETE_AUTHORIZED", "pt")
        self.assertDecision("アカウントがまだ削除されていません。削除してください。", "DELETE_AUTHORIZED", "ja")
        self.assertDecision("계정이 아직 삭제되지 않았습니다. 삭제해 주세요.", "DELETE_AUTHORIZED", "ko")

    def test_unrelated_negation_does_not_block_a_request_in_each_language(self):
        self.assertDecision("I want my account deleted.", "DELETE_AUTHORIZED", "en")
        self.assertDecision("No he usado la cuenta en años; por favor, eliminen mis datos.", "DELETE_AUTHORIZED", "es")
        self.assertDecision("Supprimez mon compte, s'il vous plaît.", "DELETE_AUTHORIZED", "fr")
        self.assertDecision("Não uso mais esta conta. Por favor, apaguem meus dados.", "DELETE_AUTHORIZED", "pt")
        self.assertDecision("アカウントを削除していただけませんか。", "DELETE_AUTHORIZED", "ja")
        self.assertDecision("더 이상 사용하지 않으니 계정을 삭제해 주세요.", "DELETE_AUTHORIZED", "ko")

    def test_declaration_and_residual_data_complaint(self):
        self.assertDecision("I'm deleting my account.", "DELETE_AUTHORIZED")
        self.assertDecision("I was told the account had been deleted, but I can still find it in search.",
                            "DELETE_AUTHORIZED")

    def test_deletion_keyword_without_a_request_is_unsure(self):
        self.assertDecision("I deleted the app yesterday.", "UNSURE")

    def test_mixed_request_is_unsure(self):
        self.assertDecision("Send me a copy of my data, then delete my account.", "UNSURE")
        self.assertDecision("Delete my account but do not delete my invoices.", "UNSURE")

    def test_refusal_with_an_aside_is_not_authorization(self):
        self.assertDecision("Please do not, under any circumstances, delete my account.", "NOT_APPLICABLE", "en")
        self.assertDecision("I don't want my account deleted.", "NOT_APPLICABLE", "en")

    def test_cancellation_is_not_authorization_in_each_language(self):
        self.assertDecision("Please cancel my deletion request.", "NOT_APPLICABLE", "en")
        self.assertDecision("Por favor, cancela mi solicitud de eliminación.", "NOT_APPLICABLE", "es")
        self.assertDecision("Veuillez annuler ma demande de suppression.", "NOT_APPLICABLE", "fr")
        self.assertDecision("Por favor, cancele meu pedido de exclusão.", "NOT_APPLICABLE", "pt")
        self.assertDecision("アカウントの削除申請を取り消してください。", "NOT_APPLICABLE", "ja")
        self.assertDecision("계정 삭제 요청을 취소해 주세요.", "NOT_APPLICABLE", "ko")
        self.assertDecision("I no longer want my account deleted.", "NOT_APPLICABLE", "en")
        self.assertDecision("Delete my account. Actually, cancel my deletion request.", "UNSURE", "en")

    def test_declaration_needs_the_account_as_object(self):
        self.assertDecision("I'm deleting old emails, can you export my data?", "UNSURE")
        self.assertDecision("I'm deleting old emails.", "UNSURE")
        self.assertDecision("I have old emails. Please delete them.", "UNSURE")

    def test_app_deletion_is_not_a_residual_data_complaint_in_each_language(self):
        for text, language in [
            ("I deleted the app but still receive invoices.", "en"),
            ("Eliminé la aplicación, pero todavía recibo facturas.", "es"),
            ("J'ai supprimé l'application, mais je reçois toujours des factures.", "fr"),
            ("Excluí o aplicativo, mas ainda recebo os boletos dos pedidos.", "pt"),
            ("アプリを削除しましたが、まだ請求書が届きます。", "ja"),
            ("앱을 삭제했지만 여전히 청구서를 받고 있습니다.", "ko"),
        ]:
            self.assertDecision(text, "UNSURE", language)

    def test_information_requests_are_not_authorization_in_each_language(self):
        self.assertDecision("Please explain whether you delete my account after inactivity.", "NOT_APPLICABLE", "en")
        self.assertDecision("Por favor, expliquen si eliminan mi cuenta después de un periodo de inactividad.",
                            "NOT_APPLICABLE", "es")
        self.assertDecision("Veuillez expliquer si vous supprimez mon compte après une période d'inactivité.",
                            "NOT_APPLICABLE", "fr")
        self.assertDecision("Por favor, expliquem se vocês excluem minha conta após um período de inatividade.",
                            "NOT_APPLICABLE", "pt")
        self.assertDecision("一定期間利用しない場合にアカウントを削除するかどうか教えてください。", "NOT_APPLICABLE", "ja")
        self.assertDecision("장기간 사용하지 않으면 계정이 삭제되는지 알려 주세요.", "NOT_APPLICABLE", "ko")
        self.assertDecision("Do you delete inactive accounts?", "NOT_APPLICABLE", "en")
        # Asking about the state of an earlier deletion is an audit, not an information request.
        self.assertDecision("Can you tell me whether my account was deleted?", "DELETE_AUTHORIZED", "en")

    def test_negation_of_another_verb_does_not_block_a_request(self):
        self.assertDecision("No uso mi cuenta y quiero eliminarla.", "DELETE_AUTHORIZED", "es")
        self.assertDecision("Não uso minha conta e quero excluí-la.", "DELETE_AUTHORIZED", "pt")
        self.assertDecision("Je n'utilise plus mon compte et je veux le supprimer.", "DELETE_AUTHORIZED", "fr")
        self.assertDecision("Please delete my account when you can?", "DELETE_AUTHORIZED", "en")
        self.assertDecision("Please delete my account and let me know when it is done.", "DELETE_AUTHORIZED", "en")

    def test_conditional_deletion_is_unsure(self):
        self.assertDecision("If my account is still active, please delete it.", "UNSURE", "en")
        self.assertDecision("Si mi cuenta sigue activa, elimínenla.", "UNSURE", "es")

    def test_adverb_between_negation_and_deletion_is_a_refusal(self):
        self.assertDecision("Please do not permanently delete my account.", "NOT_APPLICABLE", "en")
        self.assertDecision("Please don't automatically delete my account.", "NOT_APPLICABLE", "en")
        self.assertDecision("No eliminen definitivamente mi cuenta.", "NOT_APPLICABLE", "es")
        self.assertDecision("Ne supprimez pas définitivement mon compte.", "NOT_APPLICABLE", "fr")
        self.assertDecision("Não excluam permanentemente minha conta.", "NOT_APPLICABLE", "pt")

    def test_negation_with_unclear_scope_is_unsure(self):
        self.assertDecision("I don't think you should delete my account.", "UNSURE")
        self.assertDecision("I'm not sure you should delete my account.", "UNSURE")

    def test_how_to_delete_is_informational_in_each_language(self):
        self.assertDecision("Please show me how to delete my account.", "NOT_APPLICABLE", "en")
        self.assertDecision("Please help me understand how to delete my account.", "NOT_APPLICABLE", "en")
        self.assertDecision("Can you send me instructions to close my account?", "NOT_APPLICABLE", "en")
        self.assertDecision("Por favor, muéstrenme cómo eliminar mi cuenta.", "NOT_APPLICABLE", "es")
        self.assertDecision("Merci de m'expliquer comment supprimer mon compte.", "NOT_APPLICABLE", "fr")
        self.assertDecision("Por favor, mostre-me como excluir minha conta.", "NOT_APPLICABLE", "pt")
        self.assertDecision("アカウントを削除する方法を教えてください。", "NOT_APPLICABLE", "ja")
        self.assertDecision("계정을 삭제하는 방법을 알려 주세요.", "NOT_APPLICABLE", "ko")

    def test_removing_a_field_from_the_profile_is_not_authorization(self):
        self.assertDecision("Please delete the old email address from my profile and add the new one.", "UNSURE", "en")
        self.assertDecision("Please remove my phone number from my account.", "UNSURE", "en")
        self.assertDecision("Please remove my profile picture.", "UNSURE", "en")
        self.assertDecision("Por favor, eliminen el correo antiguo de mi perfil y agreguen el nuevo.", "UNSURE", "es")
        self.assertDecision("Merci de supprimer l'ancienne adresse e-mail de mon profil.", "UNSURE", "fr")
        self.assertDecision("Por favor, removam o e-mail antigo do meu perfil.", "UNSURE", "pt")
        # The account itself is still the object when it is named as such.
        self.assertDecision("Please delete my account and remove the old email from my profile.", "DELETE_AUTHORIZED")

    def test_pronoun_resolves_to_the_nearest_noun_phrase(self):
        self.assertDecision("I have old emails in my account. Please delete them.", "UNSURE")
        self.assertDecision("I have an old photo in my account. Please delete it.", "UNSURE")
        self.assertDecision("My account has old photos. Please delete them.", "UNSURE")
        self.assertDecision("I no longer use my account. Please delete it.", "DELETE_AUTHORIZED")
        self.assertDecision("You still hold my personal data. Please delete it.", "DELETE_AUTHORIZED")

    def test_nothing_matched_is_unsure(self):
        self.assertDecision("Hello, I have a question about my parcel.", "UNSURE")


if __name__ == "__main__":
    unittest.main()
