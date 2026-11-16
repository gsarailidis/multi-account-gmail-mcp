# AGENTS.md — multi-account-gmail-mcp

An MCP server that gives Claude access to several Gmail accounts. See `README.md`. This
repo is **public**: never commit tokens, client secrets, `.env` files or real email content.
The `home-env` cloud environment installs it from `main` at every session start, so a
broken `main` breaks Gmail in every personal session: test before pushing.

## Git
Commit and push straight to **`main`** (single owner; no `claude/…` or `codex/…` branches, no pull requests).
