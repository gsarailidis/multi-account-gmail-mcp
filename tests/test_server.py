"""Tests against a fake Gmail API (no network, no real accounts)."""

from __future__ import annotations

import base64
import email
import json
from email import policy

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from multi_account_gmail_mcp.config import ConfigError, load_config
from multi_account_gmail_mcp.server import create_server

ENV = {
    "GMAIL_CLIENT_ID": "cid",
    "GMAIL_CLIENT_SECRET": "csecret",
    "GMAIL_TOKEN_PERSONAL": "rt-personal",
    "GMAIL_TOKEN_WORK": "rt-work",
    "GMAIL_CONFIG": json.dumps(
        {"default_account": "personal", "accounts": {"work": {"permissions": ["read", "organize"]}}}
    ),
    "GMAIL_MCP_ENV_FILE": "/nonexistent",
}


def b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode()


class FakeGmail:
    """Minimal in-memory Gmail API keyed by refresh token -> mailbox."""

    def __init__(self):
        self.calls: list[tuple[str, str, dict | None]] = []
        self.sent: list[dict] = []
        self.labels = [
            {"id": "INBOX", "name": "INBOX", "type": "system"},
            {"id": "UNREAD", "name": "UNREAD", "type": "system"},
            {"id": "Label_1", "name": "Receipts", "type": "user"},
        ]
        self.message = {
            "id": "m1",
            "threadId": "t1",
            "labelIds": ["INBOX", "UNREAD"],
            "snippet": "Hello &amp; welcome",
            "payload": {
                "mimeType": "multipart/mixed",
                "headers": [
                    {"name": "From", "value": "Alice <alice@example.com>"},
                    {"name": "To", "value": "me@gmail.com, bob@example.com"},
                    {"name": "Cc", "value": "carol@example.com"},
                    {"name": "Subject", "value": "Plans"},
                    {"name": "Date", "value": "Mon, 2 Nov 2026 10:00:00 +0000"},
                    {"name": "Message-Id", "value": "<abc@example.com>"},
                ],
                "parts": [
                    {"mimeType": "text/plain", "body": {"data": b64("Hi there\nSee you")}},
                    {"mimeType": "application/pdf", "filename": "a.pdf",
                     "body": {"attachmentId": "att1", "size": 3}},
                ],
            },
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        body = json.loads(request.content) if request.content and request.headers.get("content-type", "").startswith("application/json") else None
        self.calls.append((request.method, url.path, body))
        if url.host == "oauth2.googleapis.com":
            form = dict(x.split("=") for x in request.content.decode().split("&"))
            if form["refresh_token"] == "bad":
                return httpx.Response(400, json={"error": "invalid_grant", "error_description": "Token has been expired or revoked."})
            return httpx.Response(200, json={"access_token": "at-" + form["refresh_token"], "expires_in": 3600})
        auth = request.headers["authorization"]
        email_addr = "me@gmail.com" if auth.endswith("personal") else "work@corp.com"
        path = url.path.removeprefix("/gmail/v1/users/me")
        if path == "/profile":
            return httpx.Response(200, json={"emailAddress": email_addr, "messagesTotal": 42, "threadsTotal": 30})
        if path == "/messages" and request.method == "GET":
            return httpx.Response(200, json={"messages": [{"id": "m1", "threadId": "t1"}], "resultSizeEstimate": 1})
        if path == "/messages/m1":
            return httpx.Response(200, json=self.message)
        if path == "/messages/m1/attachments/att1":
            return httpx.Response(200, json={"data": b64("PDF")})
        if path == "/messages/send":
            self.sent.append(body)
            return httpx.Response(200, json={"id": "sent1", "threadId": body.get("threadId", "tnew")})
        if path == "/messages/batchModify":
            return httpx.Response(204)
        if path == "/labels" and request.method == "GET":
            return httpx.Response(200, json={"labels": self.labels})
        if path == "/labels" and request.method == "POST":
            new = {"id": f"Label_{len(self.labels)}", "name": body["name"], "type": "user"}
            self.labels.append(new)
            return httpx.Response(200, json=new)
        if path == "/drafts" and request.method == "POST":
            return httpx.Response(200, json={"id": "d1", "message": {"id": "m9"}})
        return httpx.Response(404, json={"error": {"message": f"not faked: {request.method} {path}"}})


@pytest.fixture
def fake():
    return FakeGmail()


@pytest.fixture
def server(fake):
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    return create_server(lambda: load_config(ENV), http)


async def call(server, name, **args):
    try:
        result = await server.call_tool(name, args)
    except ToolError as exc:
        return {"error": str(exc)}
    if result.is_error:
        return {"error": result.content[0].text}
    return result.structured_content or json.loads(result.content[0].text)


def parse_sent(fake, i=-1):
    raw = base64.urlsafe_b64decode(fake.sent[i]["raw"])
    return email.message_from_bytes(raw, policy=policy.default)


# ------------------------------------------------------------------ config
def test_config_accounts_and_permissions():
    cfg = load_config(ENV)
    assert list(cfg.accounts) == ["personal", "work"]
    assert cfg.default_account == "personal"
    assert cfg.accounts["work"].permissions == {"read", "organize"}
    assert "delete" in cfg.accounts["personal"].permissions


def test_config_requires_client():
    with pytest.raises(ConfigError):
        load_config({"GMAIL_TOKEN_A": "x", "GMAIL_MCP_ENV_FILE": "/nonexistent"})


def test_config_env_file(tmp_path):
    f = tmp_path / ".env"
    f.write_text('GMAIL_CLIENT_ID=id\nGMAIL_CLIENT_SECRET="sec"\nexport GMAIL_TOKEN_HOME=tok\n')
    cfg = load_config({"GMAIL_MCP_ENV_FILE": str(f)})
    assert cfg.client_secret == "sec"
    assert cfg.get_account(None).alias == "home"  # single account becomes the default


def test_unknown_permission():
    env = {**ENV, "GMAIL_CONFIG": json.dumps({"accounts": {"work": {"permissions": ["fly"]}}})}
    with pytest.raises(ConfigError):
        load_config(env)


# ------------------------------------------------------------------- tools
async def test_tools_registered(server):
    names = {t.name for t in await server.list_tools()}
    for expected in ["list_accounts", "search_emails", "send_email", "reply_to_email", "forward_email",
                     "create_label", "move_emails", "trash_emails", "delete_emails_permanently"]:
        assert expected in names


async def test_list_accounts(server):
    res = await call(server, "list_accounts")
    emails = {a["account"]: a["email"] for a in res["accounts"]}
    assert emails == {"personal": "me@gmail.com", "work": "work@corp.com"}


async def test_search_uses_selected_account(server, fake):
    res = await call(server, "search_emails", query="from:alice", account="work")
    assert res["account"] == "work"
    assert res["messages"][0]["subject"] == "Plans"
    assert res["messages"][0]["snippet"] == "Hello & welcome"
    assert res["messages"][0]["unread"] is True


async def test_read_email(server):
    res = await call(server, "read_email", message_id="m1")
    assert res["account"] == "personal"
    assert res["body"] == "Hi there\nSee you"
    assert res["attachments"][0]["filename"] == "a.pdf"


async def test_send_email_with_attachment(server, fake, tmp_path):
    f = tmp_path / "note.txt"
    f.write_text("attached!")
    res = await call(server, "send_email", to=["x@example.com"], subject="Hi", body="Body", attachments=[str(f)])
    assert res["status"] == "sent"
    msg = parse_sent(fake)
    assert msg["To"] == "x@example.com" and msg["Subject"] == "Hi"
    assert [p.get_filename() for p in msg.iter_attachments()] == ["note.txt"]


async def test_permission_blocks_send(server, fake):
    res = await call(server, "send_email", account="work", to=["x@example.com"], subject="s", body="b")
    assert "does not allow 'send'" in res["error"]
    assert fake.sent == []


async def test_reply_all(server, fake):
    res = await call(server, "reply_to_email", message_id="m1", body="Sounds good", reply_all=True)
    assert res["to"] == ["Alice <alice@example.com>"]
    assert res["cc"] == ["bob@example.com", "carol@example.com"]  # own address excluded
    msg = parse_sent(fake)
    assert msg["Subject"] == "Re: Plans"
    assert msg["In-Reply-To"] == "<abc@example.com>"
    assert fake.sent[-1]["threadId"] == "t1"
    assert "> Hi there" in msg.get_body().get_content()


async def test_reply_as_draft(server, fake):
    res = await call(server, "reply_to_email", message_id="m1", body="Draft", send=False)
    assert res["status"] == "draft_saved"
    assert fake.sent == []


async def test_forward_includes_attachment(server, fake):
    res = await call(server, "forward_email", message_id="m1", to=["z@example.com"], note="FYI")
    assert res["attachments_forwarded"] == 1
    msg = parse_sent(fake)
    assert msg["Subject"] == "Fwd: Plans"
    att = next(msg.iter_attachments())
    assert att.get_filename() == "a.pdf" and att.get_content() == b"PDF"


async def test_move_by_label_name(server, fake):
    res = await call(server, "move_emails", message_ids=["m1"], to_label="receipts", account="work")
    assert res["to"] == ["Label_1"] and res["from"] == ["INBOX"]
    _, _, body = fake.calls[-1]
    assert body == {"ids": ["m1"], "addLabelIds": ["Label_1"], "removeLabelIds": ["INBOX"]}


async def test_modify_creates_missing_label(server, fake):
    res = await call(server, "modify_labels", message_ids=["m1"], add_labels=["Projects/2026"], create_missing=True)
    assert res["added"] == ["Label_3"]


async def test_unknown_label_errors(server):
    res = await call(server, "move_emails", message_ids=["m1"], to_label="Nope")
    assert "not found" in res["error"]


async def test_delete_permission(server):
    res = await call(server, "delete_emails_permanently", message_ids=["m1"], account="work")
    assert "does not allow 'delete'" in res["error"]


async def test_unknown_account(server):
    res = await call(server, "search_emails", account="nope")
    assert "Unknown account" in res["error"]


async def test_revoked_token_hint(fake):
    env = {**ENV, "GMAIL_TOKEN_PERSONAL": "bad"}
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    srv = create_server(lambda: load_config(env), http)
    res = await call(srv, "search_emails")
    assert "auth personal" in res["error"]


# ---------------------------------------------------------------------- cli
def test_cli_call_and_tools(monkeypatch, capsys, fake):
    from multi_account_gmail_mcp import cli, server as server_mod

    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    real = server_mod.create_server
    monkeypatch.setattr(server_mod, "create_server", lambda: real(lambda: load_config(ENV), http))

    assert cli.main(["call", "search_emails", '{"account": "work", "query": "x"}']) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["account"] == "work" and out["messages"][0]["subject"] == "Plans"

    assert cli.main(["call", "send_email", '{"account": "work", "to": ["a@b.c"], "subject": "s", "body": "b"}']) == 1
    assert "does not allow 'send'" in json.loads(capsys.readouterr().out)["error"]

    assert cli.main(["tools"]) == 0
    listing = capsys.readouterr().out
    assert "send_email(to: list, subject: string, body: string" in listing


def test_install_instructions_is_idempotent(tmp_path):
    from multi_account_gmail_mcp import cli

    md = tmp_path / "CLAUDE.md"
    md.write_text("# My notes\n")
    cli.main(["install-instructions", "--path", str(md)])
    cli.main(["install-instructions", "--path", str(md)])
    text = md.read_text()
    assert text.startswith("# My notes")
    assert text.count(cli.CLAUDE_MD_START) == 1
