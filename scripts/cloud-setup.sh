#!/bin/bash
# Setup script for a cloud environment: Claude Code (environment settings -> Setup script)
# or OpenAI Codex (chatgpt.com/codex/settings/environments -> Setup script). The same two
# steps work for both, and on your own computer.
# Credentials (GMAIL_* variables) go in the environment's settings; in Codex as
# "Environment variables", not "Secrets" (Codex removes secrets before the agent runs).
REPO="git+https://github.com/gsarailidis/multi-account-gmail-mcp"
pip install --quiet "$REPO" || pip install --quiet --break-system-packages "$REPO"
multi-account-gmail-mcp setup
