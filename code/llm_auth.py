"""Local SIWC sign-in; only this helper reads ~/.ghost-data-audit/credentials.json.

Sign in: python code/llm_auth.py
Run with a current token: python code/llm_auth.py --run python code/main.py --samples
Official contract: https://developers.openai.com/siwc/token-sharing-open-source/sign-in
"""

import argparse
import base64
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import uuid
import webbrowser

import jwt


ISSUER = "https://auth.openai.com"
AUTHORIZE_URL = ISSUER + "/api/accounts/authorize"
TOKEN_URL = ISSUER + "/api/accounts/oauth/token"
JWKS_URL = ISSUER + "/.well-known/jwks.json"
RESOURCE = "https://api.openai.com/v1"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
DIRECT_SCOPE = "chatgpt.tokens.use.direct"
REPO_ROOT = Path(__file__).resolve().parent.parent
REAUTH = "Run python code/llm_auth.py to sign in again."
UNUSABLE_REFRESH = {
    "invalid_grant", "invalid_refresh_token", "token_expired", "refresh_token_expired",
    "refresh_token_invalidated", "refresh_token_reused",
}


class AuthError(Exception):
    """Messages are fixed local strings, never provider responses or credential values."""


class TokenError(AuthError):
    def __init__(self, invalid_session=False):
        super().__init__("Token exchange failed. " + REAUTH)
        self.invalid_session = invalid_session


def protect(path):
    """Restrict files/directories to the current owner; fail closed on ACL errors."""
    info = path.lstat()
    if (stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            or (not stat.S_ISDIR(info.st_mode)
                and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1))):
        raise AuthError("Credential storage must use ordinary private files and directories.")
    if os.name != "nt":
        if info.st_uid != os.getuid():
            raise AuthError("Credential storage must belong to the current user.")
        path.chmod(0o700 if path.is_dir() else 0o600)
        return
    # chmod(0600) does not restrict Windows ACLs. Install a fresh protected DACL.
    script = r"""
$ErrorActionPreference = 'Stop'
$p = $env:GHOST_AUTH_ACL_PATH
$sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
$old = Get-Acl -LiteralPath $p
if ($old.GetOwner([System.Security.Principal.SecurityIdentifier]).Value -ne $sid.Value) {
    throw 'Unexpected credential owner'
}
if ((Get-Item -LiteralPath $p).PSIsContainer) {
    $acl = New-Object System.Security.AccessControl.DirectorySecurity
    $rule = New-Object System.Security.AccessControl.FileSystemAccessRule($sid, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
} else {
    $acl = New-Object System.Security.AccessControl.FileSecurity
    $rule = New-Object System.Security.AccessControl.FileSystemAccessRule($sid, 'FullControl', 'Allow')
}
$acl.SetOwner($sid)
$acl.SetAccessRuleProtection($true, $false)
$acl.AddAccessRule($rule)
if ((Get-Item -LiteralPath $p).PSIsContainer) {
    [System.IO.Directory]::SetAccessControl($p, $acl)
} else {
    [System.IO.File]::SetAccessControl($p, $acl)
}
$check = Get-Acl -LiteralPath $p
if (-not $check.AreAccessRulesProtected -or $check.Access.Count -ne 1) { throw 'ACL verification failed' }
if ($check.Access[0].IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value -ne $sid.Value) { throw 'ACL verification failed' }
"""
    env = dict(os.environ, GHOST_AUTH_ACL_PATH=str(path))
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        env=env, capture_output=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode:
        raise AuthError("Could not restrict credential storage to the current Windows user.")


def storage_directory():
    return Path.home() / ".ghost-data-audit"


@contextmanager
def locked_store(directory):
    """Serialize sign-in/refresh, including the read and atomic replacement."""
    if directory.resolve().is_relative_to(REPO_ROOT):
        raise AuthError("Credentials must be stored outside the repository.")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    protect(directory)
    lock_path = directory / "auth.lock"
    if not lock_path.exists():
        lock_path.touch(mode=0o600, exist_ok=True)
    protect(lock_path)
    with lock_path.open("r+b") as lock:
        if lock_path.stat().st_size == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise AuthError("Another sign-in or refresh is running; retry when it finishes.") from None
        try:
            yield directory / "credentials.json"
        finally:
            lock.seek(0)
            if os.name == "nt":
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)


def save_state(path, state):
    """The empty temporary file is protected before any credentials are written."""
    if path.exists():
        protect(path)
    fd, name = tempfile.mkstemp(prefix=".credentials-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            protect(temporary)
            json.dump(state, handle, separators=(",", ":"), allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_state(path):
    if not path.exists():
        state = {"ext_agent_host_id": uuid.uuid4().urn}
        save_state(path, state)
        return state
    protect(path)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        host = uuid.UUID(state["ext_agent_host_id"])
        if host.version != 4 or host.urn != state["ext_agent_host_id"]:
            raise ValueError
        if "client_id" in state:
            issued_client(state["client_id"])
        credentials = state.get("credentials")
        if credentials is not None:
            if (credentials["client_id"] != state["client_id"]
                    or credentials["subject"] != state["subject"]
                    or credentials["issuer"] != ISSUER):
                raise ValueError
        return state
    except (ValueError, KeyError, TypeError, AttributeError):
        raise AuthError("Saved sign-in data is invalid; credentials were not used.") from None


def issued_client(value):
    if not isinstance(value, str) or not value or value == "dynamic_agent_client":
        raise AuthError("Registration did not return an issued client ID.")
    return value


def authorization(state, redirect_uri):
    pending = {
        "state": secrets.token_urlsafe(32), "nonce": secrets.token_urlsafe(32),
        "verifier": secrets.token_urlsafe(64), "redirect_uri": redirect_uri,
        "client_id": state.get("client_id"),
    }
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(pending["verifier"].encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    params = {
        "client_id": pending["client_id"] or "dynamic_agent_client",
        "ext_agent_host_id": state["ext_agent_host_id"], "response_type": "code",
        "redirect_uri": redirect_uri, "scope": SCOPES, "resource": RESOURCE,
        "state": pending["state"], "nonce": pending["nonce"],
        "code_challenge_method": "S256", "code_challenge": challenge,
    }
    if pending["client_id"]:
        if state.get("credentials", {}).get("id_token"):
            params["id_token_hint"] = state["credentials"]["id_token"]
        if state.get("credentials") and DIRECT_SCOPE not in state["credentials"]["scopes"]:
            params["prompt"] = "consent"
    else:
        params["agent_name_hint"] = "Ghost Data Still Exists"
    return pending, AUTHORIZE_URL + "?" + urlencode(params)


def parse_callback(target, pending):
    parsed = urlsplit(target)
    if parsed.path != "/auth/callback" or parsed.scheme or parsed.netloc or parsed.fragment:
        raise AuthError("Unexpected sign-in callback.")
    try:
        query = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=20)
    except ValueError:
        raise AuthError("Invalid sign-in callback.") from None
    if any(len(values) != 1 for values in query.values()):
        raise AuthError("Ambiguous sign-in callback.")
    values = {key: items[0] for key, items in query.items()}
    if not secrets.compare_digest(values.get("state", "").encode("utf-8"), pending["state"].encode("utf-8")):
        raise AuthError("Sign-in state did not match; no code was exchanged.")
    if "error" in values:
        raise AuthError("Sign-in was declined or failed; no code was exchanged.")
    client_id = issued_client(values.get("client_id", pending["client_id"]))
    if pending["client_id"] and client_id != pending["client_id"]:
        raise AuthError("Sign-in returned a different client registration.")
    if not values.get("code"):
        raise AuthError("Sign-in did not return an authorization code.")
    return values["code"], client_id


def browser_callback(state, timeout=300):
    result = []
    pending = None

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # BaseHTTPRequestHandler otherwise logs codes in callback URLs.

        def setup(self):
            self.request.settimeout(5)
            super().setup()

        def do_GET(self):
            if urlsplit(self.path).path != "/auth/callback":
                self.send_error(404, "Not found")
                return
            try:
                result.append(parse_callback(self.path, pending))
            except AuthError as error:
                result.append(error)
            self.send_response(400 if isinstance(result[0], AuthError) else 200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(b"Return to the terminal for the sign-in result. You may close this tab.")

    class Server(HTTPServer):
        def handle_error(self, request, client_address):
            pass  # No request diagnostics may contain the callback URL.

    with Server(("127.0.0.1", 0), Handler) as server:
        redirect = f"http://127.0.0.1:{server.server_port}/auth/callback"
        pending, url = authorization(state, redirect)
        print("Continue with ChatGPT in your browser. Waiting up to five minutes.")
        if not webbrowser.open(url):
            raise AuthError("Could not open the system browser. Configure a browser and retry.")
        deadline = time.monotonic() + timeout
        server.timeout = 0.5
        while not result and time.monotonic() < deadline:
            server.handle_request()
    if not result:
        raise AuthError("Sign-in timed out. " + REAUTH)
    if isinstance(result[0], AuthError):
        raise result[0]
    return pending, *result[0]


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise AuthError("Unexpected redirect from the token endpoint.")


def token_request(fields):
    request = Request(TOKEN_URL, data=urlencode(fields).encode("ascii"), headers={
        "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json",
    })
    try:
        with build_opener(NoRedirect()).open(request, timeout=30) as response:
            data = json.load(response)
        if not isinstance(data, dict) or "error" in data:
            raise AuthError("Invalid token response.")
        return data
    except HTTPError as error:
        try:
            code = json.loads(error.read(65536)).get("error")
        except (ValueError, AttributeError):
            code = None
        raise TokenError(isinstance(code, str) and code in UNUSABLE_REFRESH) from None
    except (URLError, OSError, ValueError):
        raise AuthError("Could not obtain tokens; check connectivity and retry.") from None


def validate_id_token(token, client_id, nonce, expected_subject=None, refresh=False):
    try:
        signing_key = jwt.PyJWKClient(JWKS_URL, timeout=15).get_signing_key_from_jwt(token)
        claims = jwt.decode(token, signing_key.key, algorithms=["RS256"],
                            issuer=ISSUER, audience=client_id, leeway=5,
                            options={"require": ["iss", "aud", "sub", "exp", "iat"]})
        if not isinstance(claims["sub"], str) or not claims["sub"]:
            raise ValueError
        # A refreshed ID token may omit nonce; if present it must match the original.
        if not refresh or "nonce" in claims:
            if not isinstance(claims.get("nonce"), str) or not secrets.compare_digest(claims["nonce"], nonce):
                raise ValueError
        if expected_subject is not None and claims["sub"] != expected_subject:
            raise ValueError
        return claims
    except (jwt.PyJWTError, ValueError, TypeError, KeyError):
        raise AuthError("ID-token validation failed; saved credentials were not replaced.") from None


def credentials_from_response(response, client_id, nonce, expected_subject=None, previous=None):
    try:
        for field in ("access_token", "refresh_token"):
            if not isinstance(response.get(field), str) or not response[field]:
                raise ValueError
        if response.get("token_type", "").lower() != "bearer":
            raise ValueError
        expiry = response["expires_in"]
        if isinstance(expiry, bool) or not isinstance(expiry, int) or expiry <= 0:
            raise ValueError
        scope = response.get("scope")
        if "scope" not in response and previous:
            scopes = previous["scopes"]
        elif isinstance(scope, str):
            scopes = scope.split()
        else:
            raise ValueError
        id_token = response.get("id_token")
        if "id_token" in response or previous is None:
            claims = validate_id_token(id_token, client_id, nonce, expected_subject, refresh=previous is not None)
            subject = claims["sub"]
        else:
            id_token, subject = previous["id_token"], previous["subject"]
        now = time.time()
        return {
            "issuer": ISSUER, "client_id": client_id, "subject": subject,
            "id_token": id_token, "access_token": response["access_token"],
            "refresh_token": response["refresh_token"], "token_type": "Bearer",
            "scopes": scopes, "saved_at": now, "expires_at": now + expiry,
            "nonce": nonce, "earliest_refresh_at": response.get("earliest_refresh_at"),
        }
    except (ValueError, TypeError, KeyError, AttributeError):
        raise AuthError("Invalid token response; saved credentials were not replaced.") from None


def require_permission(credentials):
    if DIRECT_SCOPE not in credentials["scopes"]:
        raise AuthError("ChatGPT plan usage was not granted. " + REAUTH)


def sign_in(path, state):
    pending, code, client_id = browser_callback(state)
    if not state.get("client_id"):
        # Preserve registration even if its one-use code expires; it is not an active account.
        state["client_id"] = client_id
        save_state(path, state)
    response = token_request({
        "grant_type": "authorization_code", "client_id": client_id, "code": code,
        "code_verifier": pending["verifier"], "redirect_uri": pending["redirect_uri"],
        "resource": RESOURCE,
    })
    credentials = credentials_from_response(response, client_id, pending["nonce"], state.get("subject"))
    state = dict(state, subject=credentials["subject"], credentials=credentials)
    save_state(path, state)
    require_permission(credentials)
    print("Sign-in saved securely. ChatGPT plan usage is enabled.")


def current_access_token(path, state):
    credentials = state.get("credentials")
    if not credentials:
        raise AuthError("No saved sign-in. " + REAUTH)
    require_permission(credentials)
    if credentials["expires_at"] <= time.time() + 60:
        try:
            response = token_request({
                "grant_type": "refresh_token", "client_id": state["client_id"],
                "refresh_token": credentials["refresh_token"], "resource": RESOURCE,
            })
        except TokenError as error:
            if error.invalid_session:
                state = {key: value for key, value in state.items() if key != "credentials"}
                save_state(path, state)
            raise
        credentials = credentials_from_response(
            response, state["client_id"], credentials["nonce"], state["subject"], previous=credentials,
        )
        save_state(path, dict(state, credentials=credentials))
        require_permission(credentials)
    return credentials["access_token"]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Sign in with ChatGPT, or launch a command with ACCESS_TOKEN.")
    parser.add_argument("--run", nargs=argparse.REMAINDER, metavar="COMMAND",
                        help="Refresh if needed, then run COMMAND without exposing tokens on the command line.")
    args = parser.parse_args(argv)
    if args.run == []:
        parser.error("--run needs a command")
    try:
        with locked_store(storage_directory()) as path:
            state = load_state(path)
            if args.run is None:
                sign_in(path, state)
                return 0
            token = current_access_token(path, state)
        env = dict(os.environ, ACCESS_TOKEN=token)
        return subprocess.run(args.run, env=env, shell=False).returncode
    except AuthError as error:
        print(str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Sign-in or command cancelled.", file=sys.stderr)
        return 130
    except Exception:
        # Library/OS errors can embed tokens, callback URLs, account names, or command args.
        print("Sign-in helper failed; no credentials were displayed. Check local storage and connectivity.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
