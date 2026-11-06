# multi-account-gmail-mcp

An [MCP](https://modelcontextprotocol.io) server that gives Claude Code (or any MCP client) full access to **several Gmail accounts at the same time**: read, search, send, reply, forward, drafts, labels, moving, archiving, trash and permanent delete. Every tool takes an `account` alias, e.g. "search my **work** inbox" or "send this from **personal**".

- **No secrets in the repo.** Credentials and your account list come from environment variables.
- **Runs locally.** It talks only to Google's Gmail API; no third-party server is involved.
- **Per-account permissions.** For example, make an account read-only, or disable permanent delete.
- **Works everywhere you use Claude Code.** Use it in cloud sessions (claude.ai/code) through an environment setup script, or on your own computer.

## Tools

| Area | Tools |
|---|---|
| Accounts | `list_accounts` |
| Read | `search_emails`, `search_all_accounts`, `read_email`, `read_thread`, `save_attachment` |
| Send | `send_email`, `reply_to_email` (reply / reply-all / draft reply), `forward_email` (with attachments) |
| Drafts | `create_draft`, `list_drafts`, `update_draft`, `send_draft`, `delete_draft` |
| Labels | `list_labels`, `create_label`, `update_label`, `delete_label` |
| Organize | `modify_labels`, `move_emails`, `archive_emails`, `mark_emails` (read/unread, star, important, spam) |
| Delete | `trash_emails`, `untrash_emails`, `delete_emails_permanently` |

Every tool can also be run from a shell (a fallback if the MCP tools are not loaded):

```bash
multi-account-gmail-mcp tools
multi-account-gmail-mcp call search_emails '{"account": "work", "query": "is:unread"}'
```

Search uses normal Gmail syntax (`from:`, `is:unread`, `has:attachment`, `newer_than:7d`, `label:`…). Labels can be given by name.

## Configuration

| Variable | Contents |
|---|---|
| `GMAIL_CLIENT_ID` / `GMAIL_CLIENT_SECRET` | Your Google OAuth client (type *Desktop app*) |
| `GMAIL_TOKEN_<ALIAS>` | One refresh token per account, e.g. `GMAIL_TOKEN_PERSONAL`, `GMAIL_TOKEN_WORK`. The alias is the part after `GMAIL_TOKEN_`, in lowercase. |
| `GMAIL_CONFIG` *(optional)* | JSON settings: default account and per-account permissions |

```json
{
  "default_account": "personal",
  "accounts": {
    "work":       { "permissions": ["read", "draft", "organize"] },
    "newsletter": { "permissions": ["read", "organize", "delete"] }
  }
}
```

Permissions are `read`, `draft`, `send`, `organize` (labels, archive, mark, trash) and `delete` (permanent). An account without an entry gets all five.

On your own computer you can put the same variables in `~/.config/multi-account-gmail-mcp/.env` instead (see `.env.example`).

## Setup

See **[SETUP.md](SETUP.md)** for a step-by-step guide. In short:

1. Create a Google Cloud OAuth client and set the app to *In production*.
2. Run `multi-account-gmail-mcp auth <alias>` once per Gmail account to get its refresh token.
3. Store the values as environment variables, either in a Claude Code cloud environment or in a local `.env` file.
4. Register it with Claude Code:
   - On your computer: `claude mcp add --scope user gmail-multi -- multi-account-gmail-mcp`
   - In cloud sessions: use the environment setup script in [`scripts/cloud-setup.sh`](scripts/cloud-setup.sh). It also runs `multi-account-gmail-mcp install-instructions`, which tells Claude to prefer these tools over the single-account Gmail connector.

## Security

- Each refresh token gives **full access to that mailbox**. Keep tokens out of git, chats and screenshots. To revoke one, go to <https://myaccount.google.com/permissions> → your app → *Remove access*.
- **Emails are untrusted input.** A message can contain text that tries to instruct an AI. The server tells Claude to treat email content as data and to confirm before sending or deleting. For extra safety, keep Claude Code's permission prompts on for `send_*`, `forward_email` and `delete_emails_permanently`, or remove those permissions in `GMAIL_CONFIG`.

## Development

```bash
pip install -e ".[dev]"
pytest
```

The tests run against a fake Gmail API, so no account is needed.

## License

MIT
