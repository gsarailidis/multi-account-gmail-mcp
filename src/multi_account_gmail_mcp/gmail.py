"""Thin async Gmail REST API client with automatic access-token refresh."""

from __future__ import annotations

import asyncio
import base64
import html as html_lib
import mimetypes
import re
import time
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr
from pathlib import Path
from typing import Any

import httpx
from mcp.server.mcpserver.exceptions import ToolError

from .config import Account, Config

TOKEN_URL = "https://oauth2.googleapis.com/token"
API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
SUMMARY_HEADERS = ["From", "To", "Cc", "Subject", "Date"]
MAX_CONCURRENCY = 10


class GmailError(ToolError):
    """An error returned by Google, with a readable message."""


class GmailClient:
    def __init__(self, config: Config, account: Account, http: httpx.AsyncClient):
        self.config = config
        self.account = account
        self.http = http
        self._access_token: str | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()
        self._label_cache: list[dict] | None = None

    # ------------------------------------------------------------------ auth
    async def _token(self) -> str:
        async with self._lock:
            if self._access_token and time.time() < self._expires_at - 60:
                return self._access_token
            resp = await self.http.post(
                TOKEN_URL,
                data={
                    "client_id": self.config.client_id,
                    "client_secret": self.config.client_secret,
                    "refresh_token": self.account.refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            if resp.status_code != 200:
                detail = _error_detail(resp)
                hint = ""
                if "invalid_grant" in detail:
                    hint = (
                        " The refresh token was revoked or expired. Run "
                        f"`multi-account-gmail-mcp auth {self.account.alias}` again "
                        "(and make sure the OAuth app is 'In production', not 'Testing')."
                    )
                raise GmailError(
                    f"Could not refresh the token for account '{self.account.alias}': {detail}.{hint}"
                )
            data = resp.json()
            self._access_token = data["access_token"]
            self._expires_at = time.time() + int(data.get("expires_in", 3600))
            return self._access_token

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        for attempt in range(4):
            token = await self._token()
            headers = {"Authorization": f"Bearer {token}", **kwargs.pop("headers", {})}
            resp = await self.http.request(method, f"{API_BASE}{path}", headers=headers, **kwargs)
            if resp.status_code == 401 and attempt == 0:
                self._access_token = None  # force refresh and retry once
                continue
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < 3:
                await asyncio.sleep(2**attempt)
                continue
            if resp.status_code >= 400:
                raise GmailError(f"Gmail API error {resp.status_code}: {_error_detail(resp)}")
            if resp.status_code == 204 or not resp.content:
                return {}
            return resp.json()
        raise GmailError("Gmail API request failed after retries.")

    # --------------------------------------------------------------- profile
    async def profile(self) -> dict:
        return await self.request("GET", "/profile")

    # -------------------------------------------------------------- messages
    async def search(
        self,
        query: str,
        max_results: int = 20,
        page_token: str | None = None,
        include_spam_trash: bool = False,
        label_ids: list[str] | None = None,
    ) -> dict:
        params: dict[str, Any] = {
            "q": query,
            "maxResults": max(1, min(max_results, 100)),
            "includeSpamTrash": str(include_spam_trash).lower(),
        }
        if page_token:
            params["pageToken"] = page_token
        if label_ids:
            params["labelIds"] = label_ids
        listing = await self.request("GET", "/messages", params=params)
        ids = [m["id"] for m in listing.get("messages", [])]
        sem = asyncio.Semaphore(MAX_CONCURRENCY)

        async def fetch(mid: str) -> dict:
            async with sem:
                msg = await self.request(
                    "GET",
                    f"/messages/{mid}",
                    params={"format": "metadata", "metadataHeaders": SUMMARY_HEADERS},
                )
                return summarize(msg)

        messages = await asyncio.gather(*(fetch(i) for i in ids))
        return {
            "messages": messages,
            "next_page_token": listing.get("nextPageToken"),
            "result_size_estimate": listing.get("resultSizeEstimate"),
        }

    async def get_message(self, message_id: str, fmt: str = "full") -> dict:
        return await self.request("GET", f"/messages/{message_id}", params={"format": fmt})

    async def get_thread(self, thread_id: str) -> dict:
        return await self.request("GET", f"/threads/{thread_id}", params={"format": "full"})

    async def get_attachment(self, message_id: str, attachment_id: str) -> bytes:
        data = await self.request("GET", f"/messages/{message_id}/attachments/{attachment_id}")
        return b64decode(data.get("data", ""))

    async def send_raw(self, raw: bytes, thread_id: str | None = None) -> dict:
        body: dict[str, Any] = {"raw": b64encode(raw)}
        if thread_id:
            body["threadId"] = thread_id
        return await self.request("POST", "/messages/send", json=body)

    async def batch_modify(self, ids: list[str], add: list[str], remove: list[str]) -> None:
        for chunk in _chunks(ids, 1000):
            await self.request(
                "POST",
                "/messages/batchModify",
                json={"ids": chunk, "addLabelIds": add, "removeLabelIds": remove},
            )

    async def trash(self, message_id: str) -> dict:
        return await self.request("POST", f"/messages/{message_id}/trash")

    async def untrash(self, message_id: str) -> dict:
        return await self.request("POST", f"/messages/{message_id}/untrash")

    async def batch_delete(self, ids: list[str]) -> None:
        for chunk in _chunks(ids, 1000):
            await self.request("POST", "/messages/batchDelete", json={"ids": chunk})

    # ---------------------------------------------------------------- drafts
    async def list_drafts(self, max_results: int = 20, page_token: str | None = None, query: str | None = None) -> dict:
        params: dict[str, Any] = {"maxResults": max(1, min(max_results, 100))}
        if page_token:
            params["pageToken"] = page_token
        if query:
            params["q"] = query
        listing = await self.request("GET", "/drafts", params=params)
        sem = asyncio.Semaphore(MAX_CONCURRENCY)

        async def fetch(draft_id: str) -> dict:
            async with sem:
                d = await self.request("GET", f"/drafts/{draft_id}", params={"format": "metadata"})
                return {"draft_id": d["id"], **summarize(d.get("message", {}))}

        drafts = await asyncio.gather(*(fetch(d["id"]) for d in listing.get("drafts", [])))
        return {"drafts": drafts, "next_page_token": listing.get("nextPageToken")}

    async def create_draft(self, raw: bytes, thread_id: str | None = None) -> dict:
        message: dict[str, Any] = {"raw": b64encode(raw)}
        if thread_id:
            message["threadId"] = thread_id
        return await self.request("POST", "/drafts", json={"message": message})

    async def update_draft(self, draft_id: str, raw: bytes, thread_id: str | None = None) -> dict:
        message: dict[str, Any] = {"raw": b64encode(raw)}
        if thread_id:
            message["threadId"] = thread_id
        return await self.request("PUT", f"/drafts/{draft_id}", json={"id": draft_id, "message": message})

    async def get_draft(self, draft_id: str) -> dict:
        return await self.request("GET", f"/drafts/{draft_id}", params={"format": "full"})

    async def send_draft(self, draft_id: str) -> dict:
        return await self.request("POST", "/drafts/send", json={"id": draft_id})

    async def delete_draft(self, draft_id: str) -> None:
        await self.request("DELETE", f"/drafts/{draft_id}")

    # ---------------------------------------------------------------- labels
    async def list_labels(self, refresh: bool = False) -> list[dict]:
        if self._label_cache is None or refresh:
            data = await self.request("GET", "/labels")
            self._label_cache = data.get("labels", [])
        return self._label_cache

    async def create_label(self, body: dict) -> dict:
        label = await self.request("POST", "/labels", json=body)
        self._label_cache = None
        return label

    async def update_label(self, label_id: str, body: dict) -> dict:
        label = await self.request("PATCH", f"/labels/{label_id}", json=body)
        self._label_cache = None
        return label

    async def delete_label(self, label_id: str) -> None:
        await self.request("DELETE", f"/labels/{label_id}")
        self._label_cache = None

    async def resolve_label(self, name_or_id: str, create_missing: bool = False) -> str:
        """Turn a label name (case-insensitive) or ID into a label ID."""
        labels = await self.list_labels()
        wanted = name_or_id.strip()
        for label in labels:
            if label["id"] == wanted:
                return label["id"]
        for label in labels:
            if label["name"].lower() == wanted.lower():
                return label["id"]
        # System labels can be referred to in any case: "inbox", "Starred", ...
        if wanted.upper() in {l["id"] for l in labels}:
            return wanted.upper()
        if create_missing:
            created = await self.create_label({"name": wanted})
            return created["id"]
        raise GmailError(
            f"Label '{name_or_id}' not found in account '{self.account.alias}'. "
            "Use list_labels to see the available labels, or set create_missing=true."
        )


# ---------------------------------------------------------------------------
# Helpers for encoding, parsing and building messages
# ---------------------------------------------------------------------------


def b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii")


def b64decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _chunks(items: list[str], size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _error_detail(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except ValueError:
        return resp.text[:500] or resp.reason_phrase
    err = data.get("error")
    if isinstance(err, dict):
        return err.get("message") or str(err)
    if isinstance(err, str):
        return f"{err}: {data.get('error_description', '')}".rstrip(": ")
    return str(data)[:500]


def headers_dict(payload: dict) -> dict[str, str]:
    result: dict[str, str] = {}
    for h in payload.get("headers", []) or []:
        name = h.get("name", "")
        # Keep the first occurrence, with a canonical capitalisation.
        key = name.title() if name.lower() != "message-id" else "Message-ID"
        result.setdefault(key, h.get("value", ""))
    return result


def summarize(msg: dict) -> dict:
    headers = headers_dict(msg.get("payload", {}))
    return {
        "id": msg.get("id"),
        "thread_id": msg.get("threadId"),
        "from": headers.get("From"),
        "to": headers.get("To"),
        "cc": headers.get("Cc"),
        "subject": headers.get("Subject"),
        "date": headers.get("Date"),
        "snippet": html_lib.unescape(msg.get("snippet", "")),
        "labels": msg.get("labelIds", []),
        "unread": "UNREAD" in msg.get("labelIds", []),
    }


def html_to_text(html: str) -> str:
    text = re.sub(r"(?is)<(script|style|head).*?</\1>", "", html)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|tr|li|h[1-6])>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", "", text)
    text = html_lib.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()


def _walk(part: dict):
    yield part
    for sub in part.get("parts", []) or []:
        yield from _walk(sub)


def extract_content(payload: dict) -> dict:
    """Return plain-text body, HTML body and attachment metadata of a message."""
    text_parts: list[str] = []
    html_parts: list[str] = []
    attachments: list[dict] = []
    for part in _walk(payload):
        mime = (part.get("mimeType") or "").lower()
        body = part.get("body", {}) or {}
        filename = part.get("filename") or ""
        if filename or (body.get("attachmentId") and not mime.startswith("text/")):
            attachments.append(
                {
                    "filename": filename,
                    "mime_type": mime,
                    "size": body.get("size", 0),
                    "attachment_id": body.get("attachmentId"),
                }
            )
            continue
        data = body.get("data")
        if not data:
            continue
        decoded = b64decode(data).decode("utf-8", errors="replace")
        if mime == "text/plain":
            text_parts.append(decoded)
        elif mime == "text/html":
            html_parts.append(decoded)
    text = "\n".join(text_parts).strip()
    html = "\n".join(html_parts).strip()
    if not text and html:
        text = html_to_text(html)
    return {"text": text, "html": html, "attachments": attachments}


def detail(msg: dict, include_html: bool = False, max_chars: int = 50_000) -> dict:
    result = summarize(msg)
    payload = msg.get("payload", {})
    headers = headers_dict(payload)
    content = extract_content(payload)
    body = content["text"]
    truncated = len(body) > max_chars
    result.update(
        {
            "bcc": headers.get("Bcc"),
            "reply_to": headers.get("Reply-To"),
            "message_id_header": headers.get("Message-ID"),
            "body": body[:max_chars],
            "body_truncated": truncated,
            "attachments": content["attachments"],
        }
    )
    if include_html:
        result["html"] = content["html"][:max_chars]
    return result


def build_message(
    *,
    to: list[str] | None,
    subject: str,
    body: str,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    html: bool = False,
    sender: str | None = None,
    reply_to: str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
    attachments: list[str] | None = None,
    extra_attachments: list[tuple[str, str, bytes]] | None = None,
) -> bytes:
    msg = EmailMessage()
    if sender:
        msg["From"] = sender
    if to:
        msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    if bcc:
        msg["Bcc"] = ", ".join(bcc)
    if reply_to:
        msg["Reply-To"] = reply_to
    msg["Subject"] = subject
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references
    if html:
        msg.set_content(html_to_text(body) or " ")
        msg.add_alternative(body, subtype="html")
    else:
        msg.set_content(body)
    for path_str in attachments or []:
        path = Path(path_str).expanduser()
        if not path.is_file():
            raise GmailError(f"Attachment not found: {path}")
        ctype, _ = mimetypes.guess_type(path.name)
        maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
        msg.add_attachment(path.read_bytes(), maintype=maintype, subtype=subtype, filename=path.name)
    for filename, ctype, data in extra_attachments or []:
        maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
    return msg.as_bytes()


def split_addresses(value: str | None) -> list[str]:
    if not value:
        return []
    return [f"{n} <{a}>" if n else a for n, a in getaddresses([value]) if a]


def bare_address(value: str | None) -> str:
    return parseaddr(value or "")[1].lower()
