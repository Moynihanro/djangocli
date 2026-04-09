#!/usr/bin/env python3
"""
DjangoCLI Server — Configuration loader
Reads from config.yaml in the project root.
"""

import os
import yaml

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def load_config():
    """Load config from config.yaml, falling back to environment variables."""
    config_path = os.environ.get(
        "DJANGOCLI_CONFIG",
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yaml")
    )
    config = {}
    if os.path.isfile(config_path):
        with open(config_path) as f:
            config = yaml.safe_load(f) or {}
    return config


_config = load_config()

# Helper to dig into nested config with fallback to env vars
def _get(section, key, env_var=None, default=""):
    val = (_config.get(section) or {}).get(key, None)
    if val is not None and val != "":
        return val
    if env_var:
        return os.environ.get(env_var, default)
    return default


# Core
API_KEY = _get("server", "api_key", "DJANGOCLI_API_KEY", "change-me-to-a-real-key")
VAULT_PATH = os.path.expanduser(_get("server", "vault_path", "VAULT_PATH", "~/Desktop/BRAIN"))
HOST = _get("server", "host", "HOST", "0.0.0.0")
PORT = int(_get("server", "port", "PORT", "8000"))

# Owner
OWNER_NAME = _get("owner", "name", "OWNER_NAME", "User")
TIMEZONE = _get("owner", "timezone", "TIMEZONE", "America/New_York")

# Whoop
WHOOP_CREDENTIALS_PATH = os.path.expanduser(
    _get("whoop", "credentials_path", "WHOOP_CREDENTIALS_PATH", "~/.djangocli/whoop_credentials.json")
)
WHOOP_CLIENT_ID = _get("whoop", "client_id", "WHOOP_CLIENT_ID", "")
WHOOP_CLIENT_SECRET = _get("whoop", "client_secret", "WHOOP_CLIENT_SECRET", "")
WHOOP_REDIRECT_URI = os.environ.get(
    "WHOOP_REDIRECT_URI",
    f"http://{_get('server', 'host', 'HOST', '127.0.0.1')}:{int(_get('server', 'port', 'PORT', '8000'))}/whoop/callback"
)

# Gmail
GMAIL_EMAIL = _get("gmail", "email", "GMAIL_EMAIL", "")
GMAIL_APP_PASSWORD = _get("gmail", "app_password", "GMAIL_APP_PASSWORD", "")

# Weather
WEATHER_LAT = float(_get("weather", "latitude", "WEATHER_LAT", "0.0"))
WEATHER_LON = float(_get("weather", "longitude", "WEATHER_LON", "0.0"))
WEATHER_CITY = _get("weather", "city", "WEATHER_CITY", "")

# Messages DB (macOS iMessage)
MESSAGES_DB = os.path.expanduser("~/Library/Messages/chat.db")

# Tools enabled
_tools = _config.get("tools") or {}
TOOLS_ENABLED = {
    "web_search": _tools.get("web_search", True),
    "vault": _tools.get("vault", True),
    "calendar": _tools.get("calendar", True),
    "contacts": _tools.get("contacts", True),
    "reminders": _tools.get("reminders", True),
    "messages": _tools.get("messages", True),
    "email": _tools.get("email", bool(GMAIL_EMAIL)),
    "whoop": _tools.get("whoop", False),
    "weather": _tools.get("weather", True),
    "expenses": _tools.get("expenses", True),
    "habits": _tools.get("habits", True),
    "lists": _tools.get("lists", True),
}
