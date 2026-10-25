"""Offline auth boundary tests; all tokens and identities are synthetic."""

import base64
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import ProxyHandler, build_opener
import uuid

from cryptography.hazmat.primitives.asymmetric import rsa
import jwt

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import llm_auth as auth


class AuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.jwk = dict(jwt.algorithms.RSAAlgorithm.to_jwk(cls.key.public_key(), as_dict=True),
                       kid="synthetic-key", use="sig", alg="RS256")

    def setUp(self):
        self.jwks = mock.patch.object(jwt.PyJWKClient, "fetch_data", return_value={"keys": [self.jwk]})
        self.jwks.start()
        self.addCleanup(self.jwks.stop)
        self.state = {"ext_agent_host_id": uuid.uuid4().urn}
        self.client_id = "oaiapp_synthetic"
        self.nonce = "synthetic-nonce"

    def token(self, updates=None, omit=(), key=None):
        claims = {"iss": auth.ISSUER, "aud": self.client_id, "sub": "synthetic-subject",
                  "iat": int(time.time()) - 10, "exp": int(time.time()) + 3600, "nonce": self.nonce}
        claims.update(updates or {})
        for field in omit:
            claims.pop(field)
        return jwt.encode(claims, key or self.key, algorithm="RS256", headers={"kid": "synthetic-key"})

    def response(self, **updates):
        response = {"id_token": self.token(), "access_token": "synthetic-access",
                    "refresh_token": "synthetic-refresh", "expires_in": 3600,
                    "token_type": "Bearer", "scope": auth.SCOPES}
        response.update(updates)
        return response

    def saved(self, expires=0):
        credentials = auth.credentials_from_response(self.response(), self.client_id, self.nonce)
        credentials["expires_at"] = expires
        return dict(self.state, client_id=self.client_id, subject="synthetic-subject", credentials=credentials)

    def test_authorization_has_fresh_pkce_state_nonce_and_stable_host(self):
        redirect = "http://127.0.0.1:12345/auth/callback"
        pending, url = auth.authorization(self.state, redirect)
        next_pending, _ = auth.authorization(self.state, redirect)
        query = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        self.assertEqual(urlsplit(url)._replace(query="").geturl(), auth.AUTHORIZE_URL)
        self.assertEqual(query["client_id"], "dynamic_agent_client")
        self.assertEqual(query["agent_name_hint"], "Ghost Data Still Exists")
        self.assertEqual(query["ext_agent_host_id"], self.state["ext_agent_host_id"])
        self.assertEqual(query["redirect_uri"], redirect)
        self.assertEqual(query["scope"], auth.SCOPES)
        self.assertEqual(query["resource"], auth.RESOURCE)
        self.assertEqual(query["response_type"], "code")
        self.assertEqual(query["code_challenge_method"], "S256")
        challenge = base64.urlsafe_b64encode(hashlib.sha256(pending["verifier"].encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(query["code_challenge"], challenge)
        self.assertTrue(43 <= len(pending["verifier"]) <= 128)
        for field in ("state", "nonce", "verifier"):
            self.assertNotEqual(pending[field], next_pending[field])

    def test_returning_authorization_reuses_client_and_hint(self):
        state = self.saved()
        pending, url = auth.authorization(state, "http://127.0.0.1:54321/auth/callback")
        query = parse_qs(urlsplit(url).query)
        self.assertEqual(query["client_id"], [self.client_id])
        self.assertEqual(query["id_token_hint"], [state["credentials"]["id_token"]])
        self.assertNotIn("agent_name_hint", query)
        self.assertNotIn("login_hint", query)
        self.assertNotIn("prompt", query)
        self.assertNotIn("force_reconsent", query)
        target = "/auth/callback?" + urlencode({"state": pending["state"], "code": "synthetic-code"})
        self.assertEqual(auth.parse_callback(target, pending), ("synthetic-code", self.client_id))

    def test_missing_saved_grant_retries_consent_without_changing_registration(self):
        state = self.saved(expires=int(time.time()) + 3600)
        state["credentials"]["scopes"] = ["openid", "profile", "email"]
        _, url = auth.authorization(state, "http://127.0.0.1:54321/auth/callback")
        query = parse_qs(urlsplit(url).query)
        self.assertEqual(query["prompt"], ["consent"])
        self.assertNotIn("force_reconsent", query)
        self.assertEqual(query["client_id"], [state["client_id"]])
        self.assertEqual(query["ext_agent_host_id"], [state["ext_agent_host_id"]])
        self.assertEqual(query["id_token_hint"], [state["credentials"]["id_token"]])
        self.assertIn(auth.DIRECT_SCOPE, query["scope"][0].split())
        with self.assertRaises(auth.AuthError), mock.patch.object(auth, "token_request") as post:
            auth.current_access_token(Path("not-read-synthetic"), state)
        post.assert_not_called()

    def test_callback_rejects_state_errors_duplicates_paths_and_wrong_client(self):
        pending, _ = auth.authorization(self.state, "http://127.0.0.1:12345/auth/callback")
        good = {"state": pending["state"], "code": "synthetic-code", "client_id": self.client_id}
        invalid = [dict(good, state="wrong"), dict(good, state="non-ascii-\u2603"),
                   dict(good, state=""), dict(good, error="access_denied"),
                   dict(good, code=""), dict(good, client_id="dynamic_agent_client")]
        for fields in invalid:
            with self.subTest(fields=list(fields)), self.assertRaises(auth.AuthError):
                auth.parse_callback("/auth/callback?" + urlencode(fields), pending)
        for target in ["/callback?", "https://other.invalid/auth/callback?", "/auth/callback?state=x&"]:
            with self.assertRaises(auth.AuthError):
                auth.parse_callback(target + urlencode(good), pending)
        with self.assertRaises(auth.AuthError):
            auth.parse_callback("/auth/callback?" + urlencode({"state": pending["state"], "code": "x"}), pending)
        pending["client_id"] = "oaiapp_other"
        with self.assertRaises(auth.AuthError):
            auth.parse_callback("/auth/callback?" + urlencode(good), pending)

    def test_real_loopback_listener_is_bound_before_browser_and_does_not_log_urls(self):
        threads, received = [], []

        def open_browser(url):
            query = parse_qs(urlsplit(url).query)
            redirect = query["redirect_uri"][0]
            self.assertEqual(urlsplit(redirect).hostname, "127.0.0.1")
            self.assertEqual(urlsplit(redirect).path, "/auth/callback")
            callback = redirect + "?" + urlencode({
                "state": query["state"][0], "code": "synthetic-code", "client_id": self.client_id,
            })

            def deliver():
                with build_opener(ProxyHandler({})).open(callback, timeout=5) as response:
                    received.append((response.status, response.headers, response.read()))

            thread = threading.Thread(target=deliver)
            threads.append(thread)
            thread.start()
            return True

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(auth.webbrowser, "open", side_effect=open_browser), redirect_stdout(out), redirect_stderr(err):
            pending, code, client_id = auth.browser_callback(self.state, timeout=5)
            for thread in threads:
                thread.join(6)
        self.assertEqual((code, client_id), ("synthetic-code", self.client_id))
        self.assertEqual(received[0][0], 200)
        self.assertEqual(received[0][1]["Cache-Control"], "no-store")
        self.assertEqual(received[0][1]["Referrer-Policy"], "no-referrer")
        combined = out.getvalue() + err.getvalue() + received[0][2].decode()
        for secret in (code, pending["state"], pending["nonce"], pending["redirect_uri"]):
            self.assertNotIn(secret, combined)

    def test_valid_signature_and_claims_are_required(self):
        verified = auth.validate_id_token(self.token(), self.client_id, self.nonce)
        self.assertEqual(verified["sub"], "synthetic-subject")
        invalid = [self.token(key=self.other_key), self.token({"iss": "https://other.invalid"}),
                   self.token({"aud": "oaiapp_other"}), self.token({"exp": int(time.time()) - 20}),
                   self.token({"iat": int(time.time()) + 100}), self.token({"nbf": int(time.time()) + 100}),
                   self.token({"nonce": "wrong"}), self.token({"sub": ""}),
                   self.token(omit=("nonce",)), self.token(omit=("exp",)), self.token(omit=("iat",)),
                   self.token(omit=("sub",)), jwt.encode({"sub": "fake"}, "synthetic-key" * 3, algorithm="HS256",
                                                        headers={"kid": "synthetic-key"})]
        for index, token in enumerate(invalid):
            with self.subTest(index=index), self.assertRaises(auth.AuthError):
                auth.validate_id_token(token, self.client_id, self.nonce)
        with self.assertRaises(auth.AuthError):
            auth.validate_id_token(self.token(), self.client_id, self.nonce, expected_subject="other-subject")

    def test_refresh_identity_still_validated_and_nonce_checked_when_present(self):
        previous = self.saved()["credentials"]
        response = self.response(id_token=self.token(omit=("nonce",)))
        updated = auth.credentials_from_response(response, self.client_id, self.nonce, "synthetic-subject", previous)
        self.assertEqual(updated["subject"], previous["subject"])
        for token in (self.token({"sub": "other"}), self.token({"nonce": "other"}), self.token(key=self.other_key)):
            with self.assertRaises(auth.AuthError):
                auth.credentials_from_response(self.response(id_token=token), self.client_id, self.nonce,
                                               "synthetic-subject", previous)

    def test_bad_token_responses_and_missing_grant_do_not_authorize_use(self):
        for updates in ({"scope": None}, {"scope": []}, {"access_token": ""}, {"refresh_token": ""},
                        {"expires_in": -1}, {"expires_in": True}, {"token_type": "MAC"}, {"id_token": "bad"}):
            with self.subTest(fields=list(updates)), self.assertRaises(auth.AuthError):
                auth.credentials_from_response(self.response(**updates), self.client_id, self.nonce)
        for scope in ("openid", "openid chatgpt.tokens.use.direct.extra", ""):
            credentials = auth.credentials_from_response(self.response(scope=scope), self.client_id, self.nonce)
            with self.assertRaises(auth.AuthError):
                auth.require_permission(credentials)

    def test_sign_in_exact_exchange_and_registration_survives_expired_code(self):
        pending = {"verifier": "synthetic-verifier", "nonce": self.nonce,
                   "redirect_uri": "http://127.0.0.1:54321/auth/callback"}
        with mock.patch.object(auth, "browser_callback", return_value=(pending, "synthetic-code", self.client_id)), \
                mock.patch.object(auth, "save_state") as save, \
                mock.patch.object(auth, "token_request", side_effect=auth.TokenError(True)) as exchange:
            with self.assertRaises(auth.TokenError):
                auth.sign_in(Path("unused"), self.state)
        self.assertEqual(exchange.call_args.args[0], {
            "grant_type": "authorization_code", "client_id": self.client_id, "code": "synthetic-code",
            "code_verifier": pending["verifier"], "redirect_uri": pending["redirect_uri"], "resource": auth.RESOURCE,
        })
        self.assertEqual(save.call_args.args[1]["client_id"], self.client_id)
        self.assertNotIn("credentials", save.call_args.args[1])

    def test_returning_subject_mismatch_does_not_save(self):
        pending = {"verifier": "synthetic-verifier", "nonce": self.nonce,
                   "redirect_uri": "http://127.0.0.1:54321/auth/callback"}
        with mock.patch.object(auth, "browser_callback", return_value=(pending, "synthetic-code", self.client_id)), \
                mock.patch.object(auth, "save_state") as save, \
                mock.patch.object(auth, "token_request", return_value=self.response(id_token=self.token({"sub": "other"}))):
            with self.assertRaises(auth.AuthError):
                auth.sign_in(Path("unused"), self.saved())
        save.assert_not_called()

    def test_invalid_callback_cannot_reach_token_exchange(self):
        with mock.patch.object(auth, "browser_callback", side_effect=auth.AuthError("State did not match")), \
                mock.patch.object(auth, "token_request") as exchange, mock.patch.object(auth, "save_state") as save:
            with self.assertRaises(auth.AuthError):
                auth.sign_in(Path("unused"), self.state)
        exchange.assert_not_called()
        save.assert_not_called()

    def test_sign_in_saves_validated_credentials_and_does_not_display_identity(self):
        pending = {"verifier": "synthetic-verifier", "nonce": self.nonce,
                   "redirect_uri": "http://127.0.0.1:54321/auth/callback"}
        out = io.StringIO()
        with mock.patch.object(auth, "browser_callback", return_value=(pending, "synthetic-code", self.client_id)), \
                mock.patch.object(auth, "token_request", return_value=self.response()), \
                mock.patch.object(auth, "save_state") as save, redirect_stdout(out):
            auth.sign_in(Path("unused"), self.state)
        self.assertEqual(save.call_args.args[1]["subject"], "synthetic-subject")
        self.assertEqual(save.call_args.args[1]["credentials"]["access_token"], "synthetic-access")
        for value in ("synthetic-subject", "synthetic-access", "synthetic-code"):
            self.assertNotIn(value, out.getvalue())

    def test_refresh_rotates_atomically_without_scope_and_can_omit_id_token(self):
        state = self.saved()
        response = self.response(access_token="synthetic-access-next", refresh_token="synthetic-refresh-next")
        del response["id_token"], response["scope"]
        with mock.patch.object(auth, "token_request", return_value=response) as exchange, \
                mock.patch.object(auth, "save_state") as save:
            token = auth.current_access_token(Path("unused"), state)
        self.assertEqual(token, "synthetic-access-next")
        self.assertEqual(exchange.call_args.args[0], {"grant_type": "refresh_token", "client_id": self.client_id,
                         "refresh_token": "synthetic-refresh", "resource": auth.RESOURCE})
        saved = save.call_args.args[1]["credentials"]
        self.assertEqual(saved["refresh_token"], "synthetic-refresh-next")
        self.assertGreater(saved["expires_at"], time.time() + 3500)
        self.assertEqual(saved["scopes"], state["credentials"]["scopes"])
        self.assertEqual(saved["id_token"], state["credentials"]["id_token"])
        save.assert_called_once()

    def test_refresh_denied_scope_is_saved_and_never_returned(self):
        with mock.patch.object(auth, "token_request", return_value=self.response(scope="openid")), \
                mock.patch.object(auth, "save_state") as save:
            with self.assertRaises(auth.AuthError):
                auth.current_access_token(Path("unused"), self.saved())
        self.assertEqual(save.call_args.args[1]["credentials"]["scopes"], ["openid"])

    def test_invalid_refresh_response_does_not_replace_saved_credentials(self):
        for response in (self.response(id_token=self.token(key=self.other_key)), self.response(scope=None),
                         self.response(id_token=None), self.response(refresh_token="")):
            with mock.patch.object(auth, "token_request", return_value=response), \
                    mock.patch.object(auth, "save_state") as save:
                with self.assertRaises(auth.AuthError):
                    auth.current_access_token(Path("unused"), self.saved())
            save.assert_not_called()

    def test_unusable_refresh_clears_tokens_but_keeps_registration_and_subject(self):
        for invalid in (False, True):
            with mock.patch.object(auth, "token_request", side_effect=auth.TokenError(invalid)), \
                    mock.patch.object(auth, "save_state") as save:
                with self.assertRaises(auth.TokenError):
                    auth.current_access_token(Path("unused"), self.saved())
            if invalid:
                state = save.call_args.args[1]
                self.assertNotIn("credentials", state)
                self.assertEqual(state["client_id"], self.client_id)
                self.assertEqual(state["subject"], "synthetic-subject")
            else:
                save.assert_not_called()

    def test_unexpired_token_does_not_refresh(self):
        with mock.patch.object(auth, "token_request") as exchange:
            self.assertEqual(auth.current_access_token(Path("unused"), self.saved(time.time() + 3600)), "synthetic-access")
        exchange.assert_not_called()

    def test_token_endpoint_form_encoding_and_safe_error(self):
        opener = mock.Mock()
        opener.open.return_value = io.BytesIO(json.dumps(self.response()).encode())
        with mock.patch.object(auth, "build_opener", return_value=opener):
            auth.token_request({"code": "synthetic-code +/", "client_id": self.client_id})
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, auth.TOKEN_URL)
        self.assertEqual(parse_qs(request.data.decode())["code"], ["synthetic-code +/"])
        self.assertEqual(request.get_method(), "POST")
        error = HTTPError(auth.TOKEN_URL, 400, "synthetic-secret", {},
                          io.BytesIO(b'{"error":"invalid_grant","error_description":"synthetic-secret"}'))
        opener.open.side_effect = error
        with mock.patch.object(auth, "build_opener", return_value=opener), self.assertRaises(auth.TokenError) as caught:
            auth.token_request({"code": "synthetic-secret"})
        self.assertNotIn("synthetic-secret", str(caught.exception))
        self.assertTrue(caught.exception.invalid_session)
        with self.assertRaises(auth.AuthError):
            auth.NoRedirect().redirect_request(request, None, 307, "redirect", {}, "https://other.invalid")

    def test_storage_is_private_stable_atomic_and_locked(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "auth"
            with auth.locked_store(directory) as path:
                state = auth.load_state(path)
                self.assertEqual(auth.load_state(path), state)
                self.assertEqual(uuid.UUID(state["ext_agent_host_id"]).version, 4)
                with self.assertRaises(auth.AuthError):
                    with auth.locked_store(directory):
                        self.fail("Concurrent writer acquired the credential lock")
                before = path.read_bytes()
                with mock.patch.object(auth.os, "replace", side_effect=OSError("synthetic-failure")):
                    with self.assertRaises(OSError):
                        auth.save_state(path, {"synthetic-secret": True})
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(sorted(p.name for p in directory.iterdir()), ["auth.lock", "credentials.json"])
                auth.save_state(path, self.saved())
                self.assertEqual(auth.load_state(path)["credentials"]["access_token"], "synthetic-access")
                if os.name != "nt":
                    self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                    self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
                else:
                    script = r"""
$sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$paths = @($env:GHOST_AUTH_TEST_DIR, (Join-Path $env:GHOST_AUTH_TEST_DIR 'credentials.json'))
foreach ($p in $paths) {
    $acl = Get-Acl -LiteralPath $p
    if (-not $acl.AreAccessRulesProtected -or $acl.Access.Count -ne 1) { exit 1 }
    if ($acl.Access[0].IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value -ne $sid) { exit 2 }
}
"""
                    checked = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                                             env=dict(os.environ, GHOST_AUTH_TEST_DIR=str(directory)), capture_output=True)
                    self.assertEqual(checked.returncode, 0)
            with auth.locked_store(directory) as path:
                self.assertEqual(auth.load_state(path)["client_id"], self.client_id)

    def test_storage_rejects_repository_and_does_not_write_on_protection_failure(self):
        with self.assertRaises(auth.AuthError):
            with auth.locked_store(auth.REPO_ROOT / "synthetic-auth-must-not-exist"):
                self.fail("Repository credential directory accepted")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "credentials.json"
            with mock.patch.object(auth, "protect", side_effect=auth.AuthError("Protection failed")):
                with self.assertRaises(auth.AuthError):
                    auth.save_state(path, {"synthetic-secret": True})
            self.assertFalse(path.exists())
            self.assertEqual(list(Path(temp).iterdir()), [])

    def test_run_only_passes_access_token_to_child_environment_and_preserves_exit(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "auth"
            with auth.locked_store(directory) as path:
                auth.save_state(path, self.saved(time.time() + 3600))
            real_run = subprocess.run
            calls = []

            def run(command, **kwargs):
                if command[0] == "synthetic-pipeline":
                    calls.append((command, kwargs))
                    return subprocess.CompletedProcess(command, 7)
                return real_run(command, **kwargs)

            out, err = io.StringIO(), io.StringIO()
            original_token = os.environ.get("ACCESS_TOKEN")
            with mock.patch.object(auth, "storage_directory", return_value=directory), \
                    mock.patch.object(auth.subprocess, "run", side_effect=run), \
                    mock.patch.object(auth.webbrowser, "open") as browser, \
                    redirect_stdout(out), redirect_stderr(err):
                self.assertEqual(auth.main(["--run", "synthetic-pipeline", "--samples"]), 7)
            self.assertEqual(calls[0][0], ["synthetic-pipeline", "--samples"])
            self.assertEqual(calls[0][1]["env"]["ACCESS_TOKEN"], "synthetic-access")
            self.assertFalse(calls[0][1]["shell"])
            self.assertEqual(os.environ.get("ACCESS_TOKEN"), original_token)
            self.assertNotIn("synthetic-access", out.getvalue() + err.getvalue())
            browser.assert_not_called()

    def test_run_fails_without_session_and_suppresses_untrusted_exceptions(self):
        for error in (auth.AuthError("No saved sign-in."), RuntimeError("synthetic-secret")):
            with mock.patch.object(auth, "locked_store", side_effect=error), \
                    mock.patch.object(auth.subprocess, "run") as run, redirect_stderr(io.StringIO()) as err:
                self.assertEqual(auth.main(["--run", "synthetic-pipeline"]), 1)
            self.assertNotIn("synthetic-secret", err.getvalue())
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
