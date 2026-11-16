#!/bin/bash
# Setup script for an OpenAI Codex cloud environment.
# Paste into: chatgpt.com/codex/settings/environments -> your environment -> Setup script.
# Put the GMAIL_* values under "Environment variables", not "Secrets": Codex removes
# secrets before the agent starts, and the tool needs the values while the agent runs.
# Agent internet access: On, allowlist gmail.googleapis.com and oauth2.googleapis.com.
REPO="git+https://github.com/gsarailidis/multi-account-gmail-mcp"
pip install --quiet "$REPO" || pip install --quiet --break-system-packages "$REPO"
# Codex reads ~/.codex/AGENTS.md as global instructions. MCP servers are not documented
# for Codex cloud tasks, so the instructions tell Codex to use the command-line form
# (`multi-account-gmail-mcp call TOOL 'JSON'`), which works without MCP.
multi-account-gmail-mcp install-instructions --path ~/.codex/AGENTS.md
