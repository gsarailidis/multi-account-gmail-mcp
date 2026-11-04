"""Command line: run the server, connect an account, or check the setup.

    multi-account-gmail-mcp                 run the MCP server (stdio)
    multi-account-gmail-mcp auth ALIAS      sign in to a Gmail account, print its refresh token
    multi-account-gmail-mcp check           test every configured account
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import getpass
import hashlib
import http.server
import secrets
import sys
import threading
import urllib.parse
import webbrowser
from pathlib import Path

import httpx

from . import __version__
from .config import DEFAULT_ENV_FILE, TOKEN_PREFIX, ConfigError, load_config, load_environment, normalize_alias

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
# Full Gmail access (needed for permanent delete); narrower scopes cannot delete.
SCOPE = "https://mail.google.com/"


# --------------------------------------------------------------------- auth
def _client_credentials() -> tuple[str, str]:
    env = load_environment()
    client_id = env.get("GMAIL_CLIENT_ID") or input("OAuth client ID: ").strip()
    client_secret = env.get("GMAIL_CLIENT_SECRET") or getpass.getpass("OAuth client secret (hidden): ").strip()
    if not client_id or not client_secret:
        sys.exit("A client ID and client secret are required (see SETUP.md, step 1).")
    return client_id, client_secret


def _wait_for_code(server: http.server.HTTPServer, result: dict) -> None:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "code" not in params and "error" not in params:
                self.send_response(404)
                self.end_headers()
                return
            result.update({k: v[0] for k, v in params.items()})
            ok = "code" in params
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            msg = "Signed in. You can close this tab and go back to the terminal." if ok else f"Sign-in failed: {params.get('error', ['?'])[0]}"
            self.wfile.write(f"<html><body style='font-family:sans-serif;padding:2em'><h2>{msg}</h2></body></html>".encode())

        def log_message(self, *args):
            pass

    server.RequestHandlerClass = Handler
    while not result:
        server.handle_request()


def cmd_auth(args: argparse.Namespace) -> int:
    alias = normalize_alias(args.alias)
    client_id, client_secret = _client_credentials()

    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = secrets.token_urlsafe(16)

    server = http.server.HTTPServer(("127.0.0.1", args.port), http.server.BaseHTTPRequestHandler)
    redirect_uri = f"http://127.0.0.1:{server.server_port}"
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if args.email:
        params["login_hint"] = args.email
    url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"

    print(f"\nConnecting account '{alias}'.")
    print("Sign in with the Gmail account you want to use for this alias.\n")
    result: dict = {}
    if args.no_browser:
        server.server_close()
        print("1. Open this URL in a browser on any device:\n")
        print(url)
        print(
            "\n2. After allowing access the browser will show an error page "
            "(that's expected). Copy the full address from the address bar and paste it here."
        )
        pasted = input("\nRedirected URL: ").strip()
        result = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(pasted).query).items()}
    else:
        thread = threading.Thread(target=_wait_for_code, args=(server, result), daemon=True)
        thread.start()
        print("Opening your browser... If it does not open, visit:\n")
        print(url + "\n")
        webbrowser.open(url)
        thread.join()
        server.server_close()

    if "error" in result:
        print(f"Sign-in failed: {result['error']}", file=sys.stderr)
        return 1
    if result.get("state") != state:
        print("Sign-in failed: state mismatch (possible stale or forged response).", file=sys.stderr)
        return 1

    resp = httpx.post(
        TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": result["code"],
            "code_verifier": verifier,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        },
        timeout=30,
    )
    if resp.status_code != 200:
        print(f"Token exchange failed: {resp.text}", file=sys.stderr)
        return 1
    tokens = resp.json()
    refresh = tokens.get("refresh_token")
    if not refresh:
        print("Google did not return a refresh token. Remove the app's access at "
              "https://myaccount.google.com/permissions and try again.", file=sys.stderr)
        return 1
    if SCOPE not in tokens.get("scope", "").split():
        print("Warning: full Gmail access was not granted; some tools will fail.", file=sys.stderr)

    profile = httpx.get(
        "https://gmail.googleapis.com/gmail/v1/users/me/profile",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
        timeout=30,
    ).json()
    email = profile.get("emailAddress", "?")
    var = f"{TOKEN_PREFIX}{alias.upper()}"

    if args.save:
        path = Path(args.save).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = path.read_text().splitlines() if path.exists() else []
        lines = [l for l in lines if not l.startswith(f"{var}=")]
        if not any(l.startswith("GMAIL_CLIENT_ID=") for l in lines):
            lines += [f"GMAIL_CLIENT_ID={client_id}", f"GMAIL_CLIENT_SECRET={client_secret}"]
        lines.append(f"{var}={refresh}")
        path.write_text("\n".join(lines) + "\n")
        path.chmod(0o600)
        print(f"\nConnected {email} as '{alias}'. Saved {var} to {path}")
    else:
        print(f"\nConnected {email} as '{alias}'.\n")
        print("Add this environment variable (keep it secret, never commit it):\n")
        print(f"  Name:  {var}")
        print(f"  Value: {refresh}\n")
    return 0


# -------------------------------------------------------------------- check
async def _check() -> int:
    from .gmail import GmailClient, GmailError

    try:
        cfg = load_config()
    except ConfigError as exc:
        print(f"Configuration error: {exc}")
        return 1
    if not cfg.accounts:
        print(f"No accounts configured (no {TOKEN_PREFIX}<ALIAS> variables found).")
        return 1
    failures = 0
    async with httpx.AsyncClient(timeout=30) as http:
        for alias, acct in cfg.accounts.items():
            try:
                profile = await GmailClient(cfg, acct, http).profile()
                default = " (default)" if alias == cfg.default_account else ""
                print(f"OK    {alias}{default}: {profile['emailAddress']} — "
                      f"{profile.get('messagesTotal')} messages; permissions: {', '.join(sorted(acct.permissions))}")
            except GmailError as exc:
                failures += 1
                print(f"FAIL  {alias}: {exc}")
    return 1 if failures else 0


# --------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="multi-account-gmail-mcp", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="run the MCP server over stdio (default)")
    p_auth = sub.add_parser("auth", help="sign in to a Gmail account and get its refresh token")
    p_auth.add_argument("alias", help="short name for the account, e.g. personal, work")
    p_auth.add_argument("--email", help="pre-fill the Google sign-in with this address")
    p_auth.add_argument("--no-browser", action="store_true",
                        help="print the URL and paste back the redirect (for remote machines)")
    p_auth.add_argument("--port", type=int, default=0, help="local port for the sign-in callback")
    p_auth.add_argument("--save", nargs="?", const=str(DEFAULT_ENV_FILE), metavar="ENV_FILE",
                        help=f"save the token to an env file for local use (default {DEFAULT_ENV_FILE})")
    sub.add_parser("check", help="test every configured account")
    args = parser.parse_args(argv)

    if args.command == "auth":
        return cmd_auth(args)
    if args.command == "check":
        return asyncio.run(_check())
    from .server import create_server

    create_server().run("stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
