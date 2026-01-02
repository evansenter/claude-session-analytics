#!/bin/bash
# Uninstall session-analytics-cli from ~/.local/bin

set -e

CLI_PATH="$HOME/.local/bin/session-analytics-cli"

if [[ ! -e "$CLI_PATH" && ! -L "$CLI_PATH" ]]; then
    echo "session-analytics-cli not installed."
    exit 0
fi

rm -f "$CLI_PATH"
echo "Removed session-analytics-cli from ~/.local/bin"
