#!/bin/bash
# DjangoCLI — Interactive Setup Wizard
# Run: chmod +x setup.sh && ./setup.sh

set -e

BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
DIM='\033[2m'
NC='\033[0m' # No Color

# Always work from the script's directory
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Helper: print a step header
step() { echo -e "\n${BOLD}${BLUE}[$1]${NC} ${BOLD}$2${NC}"; }

# Helper: print success
ok() { echo -e "  ${GREEN}✓${NC} $1"; }

# Helper: print warning
warn() { echo -e "  ${YELLOW}!${NC} $1"; }

# Helper: print error and exit
fail() { echo -e "  ${RED}✗ $1${NC}"; exit 1; }

# Helper: prompt with validation (non-empty required)
require_input() {
    local prompt="$1"
    local var=""
    while [ -z "$var" ]; do
        read -p "  $prompt" var
        if [ -z "$var" ]; then
            echo -e "  ${RED}This field is required.${NC}"
        fi
    done
    echo "$var"
}

echo ""
echo -e "${BOLD}╔══════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║         DjangoCLI Setup Wizard           ║${NC}"
echo -e "${BOLD}║   Personal AI Assistant over iMessage     ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════╝${NC}"
echo ""

# ============================================================
# Prerequisites
# ============================================================
step "0/6" "Checking prerequisites"

if ! command -v python3 &> /dev/null; then
    fail "python3 not found. Install Python 3.10+ first."
fi
ok "Python3 found: $(python3 --version 2>&1)"

PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)
if [ "$PYTHON_MAJOR" -lt 3 ] || ([ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 10 ]); then
    fail "Python 3.10+ required (found $PYTHON_VERSION). Update Python first."
fi

if ! command -v swift &> /dev/null; then
    fail "swift not found. Install Xcode Command Line Tools:\n         xcode-select --install"
fi
ok "Swift found"

if [[ "$(uname)" != "Darwin" ]]; then
    fail "DjangoCLI server requires macOS (for Calendar, Contacts, Reminders access).\n         The bot can run anywhere, but the server must be on a Mac."
fi
ok "macOS detected"

# Check for Tailscale
TAILSCALE_IP=""
if command -v tailscale &> /dev/null; then
    TAILSCALE_IP=$(tailscale ip -4 2>/dev/null || true)
    if [ -n "$TAILSCALE_IP" ]; then
        ok "Tailscale connected: $TAILSCALE_IP"
    else
        warn "Tailscale installed but not connected. Run: tailscale up"
    fi
else
    warn "Tailscale not installed. Install from: https://tailscale.com/download"
    echo -e "  ${DIM}You'll need Tailscale for your bot to reach this Mac remotely.${NC}"
fi

# ============================================================
# Step 1: Identity
# ============================================================
step "1/6" "Your Identity"

OWNER_NAME=$(require_input "Your name: ")
PHONE_NUMBER=$(require_input "Your phone number (with country code, e.g. +15551234567): ")
read -p "  Your timezone [America/New_York]: " TIMEZONE
TIMEZONE=${TIMEZONE:-America/New_York}

# ============================================================
# Step 2: API Keys
# ============================================================
step "2/6" "API Keys"

echo ""
echo -e "  ${DIM}Get your Anthropic API key from: https://console.anthropic.com/settings/keys${NC}"
ANTHROPIC_KEY=$(require_input "Anthropic API key: ")
echo ""

echo -e "  ${DIM}Get SendBlue keys from: https://sendblue.co/dashboard${NC}"
SENDBLUE_KEY=$(require_input "SendBlue API key: ")
SENDBLUE_SECRET=$(require_input "SendBlue secret key: ")
SENDBLUE_FROM=$(require_input "SendBlue phone number (the number assigned to you by SendBlue): ")
echo ""

echo -e "  ${DIM}Brave Search API (optional, for web search): https://api.search.brave.com/${NC}"
read -p "  Brave Search API key [skip]: " BRAVE_KEY
echo ""

# ============================================================
# Step 3: Server Configuration
# ============================================================
step "3/6" "Server Configuration"

# Generate API key
SERVER_API_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
echo -e "  Generated server API key: ${GREEN}${SERVER_API_KEY}${NC}"
echo -e "  ${DIM}(Save this — you'll need it when deploying the bot to Render)${NC}"
echo ""

read -p "  Vault path (Obsidian vault) [~/Desktop/BRAIN]: " VAULT_PATH
VAULT_PATH=${VAULT_PATH:-~/Desktop/BRAIN}

# Server bind address — default to Tailscale IP or 127.0.0.1
if [ -n "$TAILSCALE_IP" ]; then
    DEFAULT_HOST="$TAILSCALE_IP"
    echo -e "  ${DIM}Defaulting to your Tailscale IP for security.${NC}"
else
    DEFAULT_HOST="127.0.0.1"
    echo -e "  ${YELLOW}No Tailscale IP detected. Defaulting to localhost (127.0.0.1).${NC}"
    echo -e "  ${DIM}Install Tailscale and re-run, or enter your Tailscale IP manually.${NC}"
fi
read -p "  Server bind address [$DEFAULT_HOST]: " SERVER_HOST
SERVER_HOST=${SERVER_HOST:-$DEFAULT_HOST}

if [ "$SERVER_HOST" = "0.0.0.0" ]; then
    echo -e "  ${RED}WARNING: 0.0.0.0 exposes the server to the public internet.${NC}"
    echo -e "  ${RED}Use your Tailscale IP instead for security.${NC}"
    read -p "  Continue anyway? [y/N]: " CONFIRM_HOST
    if [[ ! "$CONFIRM_HOST" =~ ^[Yy]$ ]]; then
        read -p "  Server bind address: " SERVER_HOST
        SERVER_HOST=${SERVER_HOST:-$DEFAULT_HOST}
    fi
fi

read -p "  Server port [8000]: " SERVER_PORT
SERVER_PORT=${SERVER_PORT:-8000}
echo ""

# ============================================================
# Step 4: Optional Integrations
# ============================================================
step "4/6" "Optional Integrations"

read -p "  Gmail address (for email access, press Enter to skip): " GMAIL_EMAIL
GMAIL_APP_PASSWORD=""
if [ -n "$GMAIL_EMAIL" ]; then
    echo -e "  ${DIM}Generate an App Password at: https://myaccount.google.com/apppasswords${NC}"
    read -p "  Gmail App Password: " GMAIL_APP_PASSWORD
fi
echo ""

read -p "  Enable Whoop integration? [y/N]: " WHOOP_ENABLE
WHOOP_ENABLED=false
WHOOP_CLIENT_ID=""
WHOOP_CLIENT_SECRET=""
if [[ "$WHOOP_ENABLE" =~ ^[Yy]$ ]]; then
    WHOOP_ENABLED=true
    echo -e "  ${DIM}Get Whoop developer credentials from: https://developer.whoop.com${NC}"
    read -p "  Whoop Client ID: " WHOOP_CLIENT_ID
    read -p "  Whoop Client Secret: " WHOOP_CLIENT_SECRET
fi

echo ""
read -p "  Your latitude (for weather) [40.7128]: " WEATHER_LAT
WEATHER_LAT=${WEATHER_LAT:-40.7128}
read -p "  Your longitude [-74.0060]: " WEATHER_LON
WEATHER_LON=${WEATHER_LON:--74.0060}
read -p "  Your city name [New York]: " WEATHER_CITY
WEATHER_CITY=${WEATHER_CITY:-New York}
echo ""

# ============================================================
# Step 5: Bot Personality
# ============================================================
step "5/6" "Bot Personality"

read -p "  Bot name [Django]: " BOT_NAME
BOT_NAME=${BOT_NAME:-Django}
echo ""

# ============================================================
# Write config.yaml
# ============================================================
step "6/6" "Installing"
echo ""

echo -e "  ${BOLD}Writing config.yaml...${NC}"

cat > config.yaml << YAML
# DjangoCLI Configuration — Generated by setup.sh on $(date +%Y-%m-%d)

owner:
  name: "${OWNER_NAME}"
  phone_number: "${PHONE_NUMBER}"
  timezone: "${TIMEZONE}"

api_keys:
  anthropic: "${ANTHROPIC_KEY}"
  sendblue_key: "${SENDBLUE_KEY}"
  sendblue_secret: "${SENDBLUE_SECRET}"
  sendblue_from: "${SENDBLUE_FROM}"
  brave_search: "${BRAVE_KEY}"

server:
  api_key: "${SERVER_API_KEY}"
  host: "${SERVER_HOST}"
  port: ${SERVER_PORT}
  vault_path: "${VAULT_PATH}"

gmail:
  email: "${GMAIL_EMAIL}"
  app_password: "${GMAIL_APP_PASSWORD}"

whoop:
  credentials_path: "~/.djangocli/whoop_credentials.json"
  client_id: "${WHOOP_CLIENT_ID}"
  client_secret: "${WHOOP_CLIENT_SECRET}"

weather:
  latitude: ${WEATHER_LAT}
  longitude: ${WEATHER_LON}
  city: "${WEATHER_CITY}"

personality:
  name: "${BOT_NAME}"
  group_mode: "friendly"
  stranger_mode: "friendly"

schedule:
  morning_briefing:
    enabled: true
    hour: 9
    minute: 0
  evening_wrap:
    enabled: true
    hour: 22
    minute: 0

tools:
  web_search: true
  vault: true
  calendar: true
  contacts: true
  reminders: true
  messages: true
  email: $([ -n "$GMAIL_EMAIL" ] && echo "true" || echo "false")
  whoop: ${WHOOP_ENABLED}
  weather: true
  expenses: true
  habits: true
  lists: true

advanced:
  claude_model: "claude-sonnet-4-6"
  max_tokens: 1024
  conversation_history_limit: 20
  fact_extraction: true
  proactive_checks: true
  proactive_check_interval_minutes: 15
YAML

ok "config.yaml written"

# Ensure config.yaml is gitignored (contains secrets)
if ! grep -q "config.yaml" .gitignore 2>/dev/null; then
    echo "config.yaml" >> .gitignore
    ok "Added config.yaml to .gitignore (contains your API keys)"
fi

# ============================================================
# Set up Obsidian Vault
# ============================================================
EXPANDED_VAULT=$(eval echo "$VAULT_PATH")
if [ ! -d "$EXPANDED_VAULT" ]; then
    echo -e "  ${BOLD}Setting up Obsidian vault...${NC}"
    # Make sure parent directory exists
    mkdir -p "$(dirname "$EXPANDED_VAULT")"
    cp -r vault-template/ "$EXPANDED_VAULT"
    sed -i '' "s/<!-- Fill in your personal context file and update this pointer -->/${OWNER_NAME}'s personal AI vault/" "$EXPANDED_VAULT/CLAUDE.md" 2>/dev/null || true
    ok "Vault created at ${EXPANDED_VAULT}"
    echo -e "  ${DIM}Open in Obsidian: File > Open Vault > ${EXPANDED_VAULT}${NC}"
else
    ok "Vault already exists at ${EXPANDED_VAULT}"
fi

# ============================================================
# Compile pim-tool
# ============================================================
echo -e "  ${BOLD}Compiling pim-tool...${NC}"

if [ -f server/pim-tool.swift ]; then
    if swiftc server/pim-tool.swift -o server/pim-tool -O 2>/tmp/pim-tool-compile.err; then
        ok "pim-tool compiled"
    else
        echo -e "  ${RED}✗ pim-tool compilation failed:${NC}"
        cat /tmp/pim-tool-compile.err
        echo -e "  ${DIM}Try manually: swiftc server/pim-tool.swift -o server/pim-tool${NC}"
    fi
else
    fail "server/pim-tool.swift not found — is the repo complete?"
fi

# ============================================================
# Set up Python environments
# ============================================================
echo -e "  ${BOLD}Installing server dependencies...${NC}"

cd server
python3 -m venv venv
if source venv/bin/activate && pip install -q -r requirements.txt 2>/tmp/pip-server.err; then
    deactivate
    ok "Server dependencies installed"
else
    deactivate 2>/dev/null || true
    echo -e "  ${RED}✗ Server pip install failed:${NC}"
    tail -5 /tmp/pip-server.err
fi
cd "$SCRIPT_DIR"

echo -e "  ${BOLD}Installing bot dependencies...${NC}"

cd bot
python3 -m venv venv
if source venv/bin/activate && pip install -q -r requirements.txt 2>/tmp/pip-bot.err; then
    deactivate
    ok "Bot dependencies installed"
else
    deactivate 2>/dev/null || true
    echo -e "  ${RED}✗ Bot pip install failed:${NC}"
    tail -5 /tmp/pip-bot.err
fi
cd "$SCRIPT_DIR"

# ============================================================
# Create launchd plist for server
# ============================================================
echo -e "  ${BOLD}Creating launchd service...${NC}"

PLIST_PATH="$HOME/Library/LaunchAgents/com.djangocli.server.plist"

# Make sure LaunchAgents directory exists
mkdir -p "$HOME/Library/LaunchAgents"

cat > "$PLIST_PATH" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.djangocli.server</string>
    <key>ProgramArguments</key>
    <array>
        <string>${SCRIPT_DIR}/server/venv/bin/python3</string>
        <string>${SCRIPT_DIR}/server/server.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${SCRIPT_DIR}/server</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>DJANGOCLI_CONFIG</key>
        <string>${SCRIPT_DIR}/config.yaml</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/djangocli-server.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/djangocli-server.err</string>
</dict>
</plist>
PLIST

ok "Launchd service created"

# ============================================================
# Create DjangoCLI config directory
# ============================================================
mkdir -p ~/.djangocli
ok "Created ~/.djangocli/"

# ============================================================
# Done
# ============================================================
echo ""
echo -e "${BOLD}╔══════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║           Setup Complete!                ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════╝${NC}"
echo ""
echo -e "${BOLD}${YELLOW}Before starting — grant macOS permissions:${NC}"
echo ""
echo -e "  Run each command and click ${BOLD}Allow${NC} on the popup:"
echo ""
echo -e "    ${GREEN}./server/pim-tool calendar today${NC}"
echo -e "    ${GREEN}./server/pim-tool reminders list${NC}"
echo -e "    ${GREEN}./server/pim-tool contacts search \"test\"${NC}"
echo ""
echo -e "${BOLD}Then start the server:${NC}"
echo ""
echo -e "  ${GREEN}launchctl bootstrap gui/\$(id -u) ~/Library/LaunchAgents/com.djangocli.server.plist${NC}"
echo ""
echo -e "${BOLD}Test it:${NC}"
echo ""
echo -e "  ${GREEN}curl -H 'x-api-key: ${SERVER_API_KEY}' http://${SERVER_HOST}:${SERVER_PORT}/health${NC}"
echo ""
echo -e "${BOLD}Deploy the bot:${NC}"
echo ""
echo "  See docs/QUICKSTART.md Part 3 for Render deployment instructions."
echo ""
echo "  Key values you'll need on Render:"
echo -e "    MAC_MINI_URL  = ${GREEN}http://${SERVER_HOST}:${SERVER_PORT}${NC}"
echo -e "    MAC_MINI_API_KEY = ${GREEN}${SERVER_API_KEY}${NC}"
echo ""
echo -e "  ${DIM}Config: ${SCRIPT_DIR}/config.yaml${NC}"
echo -e "  ${DIM}Logs:   /tmp/djangocli-server.log${NC}"
echo ""
