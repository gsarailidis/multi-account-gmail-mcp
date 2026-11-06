# Setup guide

This takes about 20–30 minutes and is done once. You will:

1. Create your own Google "app" (an OAuth client). This is free.
2. Sign each Gmail account in to it once to get a refresh token.
3. Save the values in your Claude Code environment.
4. Add a setup script so every new Claude Code session has the Gmail tools.

> **Never paste tokens or the client secret into a Claude chat.** Put them only in environment settings or in your local `.env` file.

---

## Step 1 — Create the Google OAuth client

1. Go to <https://console.cloud.google.com/> and sign in. Any of your Google accounts works; this account only *owns* the app.
2. **Create a project:** project picker at the top → *New project* → name it e.g. `gmail-mcp` → *Create*. Make sure it is selected.
3. **Enable the Gmail API:** <https://console.cloud.google.com/apis/library/gmail.googleapis.com> → *Enable*.
4. **Configure the consent screen:** open <https://console.cloud.google.com/auth/overview> (*Google Auth Platform*) → *Get started*.
   - App name: e.g. `My Gmail MCP`. User support email: your address.
   - Audience: **External**.
   - Contact email: your address → agree → *Create*.
5. **Publish the app (important):** *Audience* → *Publish app* → confirm. The status must say **In production**.
   - If it stays in *Testing*, Google expires your tokens every 7 days and you would have to sign in again each week.
   - You do **not** need to submit the app for verification. It is only for you.
6. **Create the client:** *Clients* → *Create client* → Application type **Desktop app** → name it → *Create*.
7. Copy the **Client ID** and **Client secret**. You can also download the JSON as a backup, but keep it out of any git repo.

## Step 2 — Get a refresh token for each Gmail account

Do this **on your own computer**, not in a Claude session, so the tokens never appear in a chat.

You need Python 3.10+. The easiest route is [uv](https://docs.astral.sh/uv/getting-started/installation/), which needs no install step for this tool:

```bash
uvx --from git+https://github.com/gsarailidis/multi-account-gmail-mcp multi-account-gmail-mcp auth personal
```

Without uv:

```bash
pip install git+https://github.com/gsarailidis/multi-account-gmail-mcp
```

```bash
multi-account-gmail-mcp auth personal
```

For each account:

1. Choose a short alias: `personal`, `work`, `business`, … Letters, digits and `_` only.
2. Run the command with that alias. It asks for the client ID and secret, unless `GMAIL_CLIENT_ID` and `GMAIL_CLIENT_SECRET` are set.
3. A browser opens. **Sign in with the Gmail account for that alias.** Tip: add `--email you@gmail.com` to pre-select it.
4. Google shows **"Google hasn't verified this app"**. That is expected, because it is your own app. Click *Advanced* → *Go to My Gmail MCP (unsafe)* → tick the permission → *Continue*.
5. The terminal prints something like:

   ```
   Name:  GMAIL_TOKEN_PERSONAL
   Value: 1//0g...
   ```

   Keep this window open, or store the value in your password manager.

Repeat for all accounts, e.g. `auth work`, `auth business`, `auth shop`, `auth old`.

> **Using it only on this computer?** Add `--save` to the command. It writes everything to `~/.config/multi-account-gmail-mcp/.env`, readable only by you. Then skip to Step 5.

## Step 3 — Store the values in your Claude Code cloud environment

In Claude Code on the web or in the app: open the **environment menu** in the session's title bar → **Edit** your environment, then:

**Environment variables** (one per line, `NAME=value`):

```
GMAIL_CLIENT_ID=1234567890-abc.apps.googleusercontent.com
GMAIL_CLIENT_SECRET=GOCSPX-...
GMAIL_TOKEN_PERSONAL=1//0g...
GMAIL_TOKEN_WORK=1//0g...
GMAIL_TOKEN_BUSINESS=1//0g...
GMAIL_TOKEN_SHOP=1//0g...
GMAIL_TOKEN_OLD=1//0g...
GMAIL_CONFIG={"default_account":"personal"}
```

If your app shows a separate **secrets** section, put the client secret and tokens there.

`GMAIL_CONFIG` is optional. See the README for per-account permissions, e.g. making an account read-only.

Make sure **Network access** allows `oauth2.googleapis.com` and `gmail.googleapis.com`. The default trusted setting already does.

## Step 4 — Add the setup script

In the same environment settings, paste this into **Setup script**. It is also in [`scripts/cloud-setup.sh`](scripts/cloud-setup.sh):

```bash
#!/bin/bash
REPO="git+https://github.com/gsarailidis/multi-account-gmail-mcp"
pip install --quiet "$REPO" || pip install --quiet --break-system-packages "$REPO"
claude mcp add --scope user gmail-multi -- multi-account-gmail-mcp 2>/dev/null || true
multi-account-gmail-mcp install-instructions
```

The last line adds a short note to Claude Code's personal instructions (`~/.claude/CLAUDE.md`). Cloud sessions don't load MCP servers registered with `claude mcp add`, so in the cloud Claude runs the tools as shell commands (`multi-account-gmail-mcp call …`). The note tells Claude how, and to use this instead of the single-account Gmail connector.

Save. Every **new** session in this environment now has the Gmail tools, whichever repository you open.

## Step 5 — Check it

Start a new session and ask Claude: *"List my Gmail accounts."* It should show all your aliases with their email addresses.

From a terminal you can also run:

```bash
multi-account-gmail-mcp check
```

```
OK    personal (default): you@gmail.com — 18234 messages; permissions: delete, draft, organize, read, send
OK    work: you@company.com — 5120 messages; permissions: ...
```

### On your own computer (Claude Code CLI or desktop)

```bash
pip install git+https://github.com/gsarailidis/multi-account-gmail-mcp
```

```bash
claude mcp add --scope user gmail-multi -- multi-account-gmail-mcp
```

Credentials come from `~/.config/multi-account-gmail-mcp/.env`, written by `auth --save`, or from your shell environment.

---

## Maintenance

| Task | What to do |
|---|---|
| Add a 6th account | Run `auth <newalias>` and add `GMAIL_TOKEN_NEWALIAS` to the environment |
| Remove an account | Delete its variable, and revoke at <https://myaccount.google.com/permissions> |
| A token stopped working (`invalid_grant`) | Run `auth <alias>` again and replace the variable. This happens if you changed that account's password, revoked access, or the app was still in *Testing*. |
| Update the tool | Nothing to do: new sessions install the latest version |

## Troubleshooting

- **"Access blocked: this app's request is invalid" / `redirect_uri_mismatch`**: the client type must be **Desktop app**, not *Web application*.
- **"This app is blocked"**: some Google Workspace (company) accounts don't allow unverified apps. Ask your Workspace admin to trust the app's client ID, or use that account read-only through other means.
- **No browser on the machine**: use `auth <alias> --no-browser` and follow the prompts.
- **Tools don't appear in a new session**: check the setup script output in the session's startup log, and that the environment variables are spelled exactly as above.
