#!/bin/bash
# Setup script for a Claude Code cloud environment.
# Paste into: environment settings -> Setup script. Runs at the start of every new session.
REPO="git+https://github.com/gsarailidis/multi-account-gmail-mcp"
pip install --quiet "$REPO" || pip install --quiet --break-system-packages "$REPO"
claude mcp add --scope user gmail-multi -- multi-account-gmail-mcp 2>/dev/null || true
