#!/bin/bash
# Compile pim-tool Swift binary for macOS Calendar/Contacts/Reminders access
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "Compiling pim-tool..."
swiftc pim-tool.swift -o pim-tool -O

if [ -f pim-tool ]; then
    echo "Success: pim-tool compiled at $SCRIPT_DIR/pim-tool"
    echo ""
    echo "Grant TCC permissions by running:"
    echo "  ./pim-tool calendar today"
    echo "  ./pim-tool reminders list"
    echo "  ./pim-tool contacts search \"test\""
    echo ""
    echo "Click 'Allow' on each macOS permission popup."
else
    echo "ERROR: Compilation failed."
    exit 1
fi
