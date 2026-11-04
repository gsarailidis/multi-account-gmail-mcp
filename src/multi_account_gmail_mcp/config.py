"""Configuration: OAuth client, accounts and per-account permissions.

Everything comes from environment variables, so no personal data or secrets
ever need to live in this repository:

    GMAIL_CLIENT_ID          OAuth client ID (Google Cloud, type "Desktop app")
    GMAIL_CLIENT_SECRET      OAuth client secret
    GMAIL_TOKEN_<ALIAS>      Refresh token for one account, e.g. GMAIL_TOKEN_WORK.
                             The alias ("work") is what you use to pick the account.
    GMAIL_CONFIG             Optional JSON with settings, see below.

GMAIL_CONFIG example:

    {
      "default_account": "personal",
      "accounts": {
        "work": {"permissions": ["read", "draft", "organize"]}
      }
    }

Permissions (all are granted when not specified):
    read      search and read emails, threads, labels, attachments
    draft     create, update and delete drafts
    send      send emails, replies, forwards and drafts
    organize  labels, archive, move, mark read/unread/spam, trash and untrash
    delete    permanently delete emails (cannot be undone)

For use on your own computer, the same variables can be put in a file
(KEY=VALUE per line) at ~/.config/multi-account-gmail-mcp/.env, or at the path
in GMAIL_MCP_ENV_FILE. Real environment variables take precedence.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from mcp.server.mcpserver.exceptions import ToolError

TOKEN_PREFIX = "GMAIL_TOKEN_"
ALL_PERMISSIONS = frozenset({"read", "draft", "send", "organize", "delete"})
DEFAULT_ENV_FILE = Path.home() / ".config" / "multi-account-gmail-mcp" / ".env"


class ConfigError(ToolError):
    """Raised when the configuration is missing or invalid."""


@dataclass
class Account:
    alias: str
    refresh_token: str
    permissions: frozenset[str] = ALL_PERMISSIONS


@dataclass
class Config:
    client_id: str
    client_secret: str
    accounts: dict[str, Account] = field(default_factory=dict)
    default_account: str | None = None

    def get_account(self, alias: str | None) -> Account:
        if not self.accounts:
            raise ConfigError(
                "No Gmail accounts configured. Add environment variables named "
                f"{TOKEN_PREFIX}<ALIAS> containing refresh tokens."
            )
        if not alias:
            if self.default_account:
                alias = self.default_account
            elif len(self.accounts) == 1:
                alias = next(iter(self.accounts))
            else:
                raise ConfigError(
                    "Several accounts are configured and no default is set; "
                    f"specify account as one of: {', '.join(sorted(self.accounts))}"
                )
        key = normalize_alias(alias)
        if key not in self.accounts:
            raise ConfigError(
                f"Unknown account '{alias}'. Configured accounts: "
                f"{', '.join(sorted(self.accounts))}"
            )
        return self.accounts[key]


def normalize_alias(alias: str) -> str:
    return alias.strip().lower().replace("-", "_").replace(" ", "_")


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def load_environment(env: dict[str, str] | None = None) -> dict[str, str]:
    """Return environment variables merged with the optional env file."""
    environ = dict(os.environ if env is None else env)
    env_file = Path(environ.get("GMAIL_MCP_ENV_FILE") or DEFAULT_ENV_FILE).expanduser()
    if env_file.is_file():
        for key, value in parse_env_file(env_file).items():
            environ.setdefault(key, value)
    return environ


def load_config(env: dict[str, str] | None = None) -> Config:
    environ = load_environment(env)

    client_id = environ.get("GMAIL_CLIENT_ID", "").strip()
    client_secret = environ.get("GMAIL_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise ConfigError(
            "GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET must be set (see SETUP.md)."
        )

    settings: dict = {}
    raw_settings = environ.get("GMAIL_CONFIG", "").strip()
    if raw_settings:
        try:
            settings = json.loads(raw_settings)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"GMAIL_CONFIG is not valid JSON: {exc}") from exc
        if not isinstance(settings, dict):
            raise ConfigError("GMAIL_CONFIG must be a JSON object.")

    account_settings = {
        normalize_alias(k): v for k, v in (settings.get("accounts") or {}).items()
    }

    accounts: dict[str, Account] = {}
    for key, value in environ.items():
        if not key.startswith(TOKEN_PREFIX) or not value.strip():
            continue
        alias = normalize_alias(key[len(TOKEN_PREFIX) :])
        if not alias:
            continue
        perms = ALL_PERMISSIONS
        acct_cfg = account_settings.get(alias) or {}
        if "permissions" in acct_cfg:
            requested = {str(p).lower() for p in acct_cfg["permissions"]}
            unknown = requested - ALL_PERMISSIONS
            if unknown:
                raise ConfigError(
                    f"Unknown permission(s) for account '{alias}': {sorted(unknown)}. "
                    f"Valid: {sorted(ALL_PERMISSIONS)}"
                )
            perms = frozenset(requested)
        accounts[alias] = Account(alias=alias, refresh_token=value.strip(), permissions=perms)

    default = settings.get("default_account")
    if default:
        default = normalize_alias(default)
        if accounts and default not in accounts:
            raise ConfigError(
                f"default_account '{default}' has no {TOKEN_PREFIX}{default.upper()} variable."
            )

    return Config(
        client_id=client_id,
        client_secret=client_secret,
        accounts=dict(sorted(accounts.items())),
        default_account=default,
    )
