#!/usr/bin/env python3
"""
DjangoCLI — Personal AI Assistant over iMessage
Config-driven iMessage bot with Claude tool-use architecture.
"""

import os
import re
import json
import logging
import sqlite3
import requests
import base64
import yaml
from datetime import datetime, timedelta
from threading import Lock
from urllib.parse import urlparse

from flask import Flask, request, jsonify
import anthropic
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
import pytz

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ============================================================
# Configuration
# ============================================================
def load_config():
    config_path = os.environ.get(
        "DJANGOCLI_CONFIG",
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yaml")
    )
    config = {}
    if os.path.isfile(config_path):
        with open(config_path) as f:
            config = yaml.safe_load(f) or {}
    return config


CONFIG = load_config()


def cfg(section, key, env_var=None, default=""):
    val = (CONFIG.get(section) or {}).get(key, None)
    if val is not None and val != "":
        return val
    if env_var:
        return os.environ.get(env_var, default)
    return default


# API Keys
SENDBLUE_API_KEY = cfg("api_keys", "sendblue_key", "SENDBLUE_API_KEY")
SENDBLUE_SECRET_KEY = cfg("api_keys", "sendblue_secret", "SENDBLUE_SECRET_KEY")
SENDBLUE_FROM = cfg("api_keys", "sendblue_from", "SENDBLUE_FROM")
ANTHROPIC_API_KEY = cfg("api_keys", "anthropic", "ANTHROPIC_API_KEY")
BRAVE_API_KEY = cfg("api_keys", "brave_search", "BRAVE_API_KEY")

# Owner
OWNER_NAME = cfg("owner", "name", "OWNER_NAME", "User")
MY_PHONE_NUMBER = cfg("owner", "phone_number", "MY_PHONE_NUMBER")
TIMEZONE = cfg("owner", "timezone", "TIMEZONE", "America/New_York")
tz = pytz.timezone(TIMEZONE)

# Server connection
_server = CONFIG.get("server") or {}
SERVER_API_KEY = _server.get("api_key", os.environ.get("MAC_MINI_API_KEY", ""))
SERVER_HOST = _server.get("host", os.environ.get("MAC_MINI_HOST", "127.0.0.1"))
SERVER_PORT = _server.get("port", os.environ.get("MAC_MINI_PORT", "8000"))
MAC_MINI_URL = os.environ.get("MAC_MINI_URL", f"http://{SERVER_HOST}:{SERVER_PORT}")

# Personality
_personality = CONFIG.get("personality") or {}
BOT_NAME = _personality.get("name", "Django")
GROUP_MODE = _personality.get("group_mode", "friendly")
STRANGER_MODE = _personality.get("stranger_mode", "friendly")

SYSTEM_PROMPT_TEMPLATE = _personality.get("system_prompt", """You are {bot_name}, {owner_name}'s personal AI assistant on iMessage.

Your personality:
- Direct, no fluff — {owner_name} hates over-explanation
- Casual tone, like a sharp friend texting
- Proactive — if you notice something relevant, mention it
- Keep responses SHORT — this is iMessage, not email. A few sentences max.
- NEVER use markdown formatting. This is plain text over iMessage.

You have tools to search the web, read/search/write to the vault, check the calendar,
look up contacts, manage reminders, read iMessages, log expenses and habits, and manage lists.

Use tools freely. Don't say "I can't" — just do it.

{vault_context}

{user_context}

Current date/time: {current_time}""")

SNARKY_SYSTEM_PROMPT = f"You are {BOT_NAME}, an AI assistant in a group chat. You belong to {OWNER_NAME} and you're loyal to them. For {OWNER_NAME}, be helpful and sharp. For EVERYONE ELSE, be extremely rude, snarky, and roast them mercilessly. Give attitude, backhanded compliments, savage one-liners. Keep it short and punchy."
FRIENDLY_SYSTEM_PROMPT = f"You are {BOT_NAME}, an AI assistant. Be helpful but brief and casual. No access to personal features — just chat normally. Keep responses short."

# Tools config
_tools_config = CONFIG.get("tools") or {}
TOOLS_ENABLED = {
    "web_search": _tools_config.get("web_search", True),
    "vault": _tools_config.get("vault", True),
    "calendar": _tools_config.get("calendar", True),
    "contacts": _tools_config.get("contacts", True),
    "reminders": _tools_config.get("reminders", True),
    "messages": _tools_config.get("messages", True),
    "email": _tools_config.get("email", True),
    "whoop": _tools_config.get("whoop", False),
    "weather": _tools_config.get("weather", True),
    "expenses": _tools_config.get("expenses", True),
    "habits": _tools_config.get("habits", True),
    "lists": _tools_config.get("lists", True),
}

# Advanced
_advanced = CONFIG.get("advanced") or {}
CLAUDE_MODEL = _advanced.get("claude_model", "claude-sonnet-4-6")
MAX_TOKENS = _advanced.get("max_tokens", 1024)
CONVERSATION_LIMIT = _advanced.get("conversation_history_limit", 20)
FACT_EXTRACTION = _advanced.get("fact_extraction", True)
PROACTIVE_CHECKS = _advanced.get("proactive_checks", True)
PROACTIVE_INTERVAL = _advanced.get("proactive_check_interval_minutes", 15)

# Schedule
_schedule = CONFIG.get("schedule") or {}


# ============================================================
# Tool Definitions (built dynamically based on config)
# ============================================================
def build_tools():
    tools = []

    if TOOLS_ENABLED["web_search"]:
        tools.append({
            "name": "web_search",
            "description": "Search the web for any information. For weather, use get_weather instead.",
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "The search query"}},
                "required": ["query"]
            }
        })

    if TOOLS_ENABLED["vault"]:
        tools.extend([
            {
                "name": "vault_search",
                "description": f"Search {OWNER_NAME}'s Obsidian vault (notes, projects, context files).",
                "input_schema": {
                    "type": "object",
                    "properties": {"query": {"type": "string", "description": "Search term"}},
                    "required": ["query"]
                }
            },
            {
                "name": "vault_read",
                "description": f"Read a specific file from {OWNER_NAME}'s Obsidian vault.",
                "input_schema": {
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "File path within vault"}},
                    "required": ["path"]
                }
            },
            {
                "name": "vault_save",
                "description": f"Save a new note to {OWNER_NAME}'s Obsidian vault.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Title of the note"},
                        "content": {"type": "string", "description": "Content of the note"},
                        "folder": {"type": "string", "description": "Folder (default: Inbox)", "default": "Inbox"}
                    },
                    "required": ["title", "content"]
                }
            },
        ])

    if TOOLS_ENABLED["calendar"]:
        tools.extend([
            {
                "name": "get_calendar",
                "description": f"Get {OWNER_NAME}'s calendar events for today or upcoming days.",
                "input_schema": {
                    "type": "object",
                    "properties": {"days": {"type": "integer", "description": "Days ahead (default: 1)", "default": 1}}
                }
            },
            {
                "name": "create_calendar_event",
                "description": "Create a calendar event.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string", "description": "Event title"},
                        "start": {"type": "string", "description": "Start: 'Thursday, April 3, 2026 at 5:00:00 PM'"},
                        "end": {"type": "string", "description": "End time in same format"},
                        "calendar_name": {"type": "string", "description": "Calendar name", "default": "Home"}
                    },
                    "required": ["summary", "start", "end"]
                }
            },
            {
                "name": "edit_calendar_event",
                "description": "Edit an existing calendar event.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "original_summary": {"type": "string", "description": "Current event name"},
                        "new_summary": {"type": "string", "description": "New title (optional)"},
                        "new_start": {"type": "string", "description": "New start time (optional)"},
                        "new_end": {"type": "string", "description": "New end time (optional)"}
                    },
                    "required": ["original_summary"]
                }
            },
            {
                "name": "delete_calendar_event",
                "description": "Delete a calendar event.",
                "input_schema": {
                    "type": "object",
                    "properties": {"summary": {"type": "string", "description": "Event name to delete"}},
                    "required": ["summary"]
                }
            },
        ])

    if TOOLS_ENABLED["contacts"]:
        tools.append({
            "name": "search_contacts",
            "description": "Search contacts by name. Returns phone numbers and emails.",
            "input_schema": {
                "type": "object",
                "properties": {"name": {"type": "string", "description": "Name to search"}},
                "required": ["name"]
            }
        })

    if TOOLS_ENABLED["reminders"]:
        tools.extend([
            {
                "name": "get_reminders",
                "description": "Get open/incomplete reminders.",
                "input_schema": {"type": "object", "properties": {}}
            },
            {
                "name": "complete_reminder",
                "description": "Mark a reminder as done.",
                "input_schema": {
                    "type": "object",
                    "properties": {"name": {"type": "string", "description": "Reminder name"}},
                    "required": ["name"]
                }
            },
            {
                "name": "create_reminder",
                "description": "Create a reminder.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "What to remind about"},
                        "due": {"type": "string", "description": "Due date/time (optional)"}
                    },
                    "required": ["name"]
                }
            },
        ])

    tools.append({
        "name": "set_reminder",
        "description": f"Set a timed reminder. {OWNER_NAME} gets a text at the specified time.",
        "input_schema": {
            "type": "object",
            "properties": {
                "what": {"type": "string", "description": "What to remind about"},
                "when": {"type": "string", "description": "ISO datetime string"},
                "heads_up_minutes": {"type": "integer", "description": "Minutes before to remind", "default": 0}
            },
            "required": ["what", "when"]
        }
    })

    if TOOLS_ENABLED["messages"]:
        tools.extend([
            {
                "name": "get_messages",
                "description": "Read recent iMessages.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "contact": {"type": "string", "description": "Contact name or phone (optional)"},
                        "limit": {"type": "integer", "description": "Number of messages", "default": 10}
                    }
                }
            },
            {
                "name": "person_lookup",
                "description": "Get everything known about a person — contacts, texts, vault mentions, emails.",
                "input_schema": {
                    "type": "object",
                    "properties": {"name": {"type": "string", "description": "Person's name"}},
                    "required": ["name"]
                }
            },
        ])

    if TOOLS_ENABLED["email"]:
        tools.extend([
            {
                "name": "check_email",
                "description": "Check Gmail inbox. Search unread, by sender, or by subject.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query", "default": "is:unread"},
                        "limit": {"type": "integer", "description": "Number of emails", "default": 5}
                    }
                }
            },
            {
                "name": "read_email",
                "description": "Read full content of a specific email.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "subject": {"type": "string", "description": "Subject to search (optional)"},
                        "from_addr": {"type": "string", "description": "Sender email (optional)"}
                    }
                }
            },
        ])

    if TOOLS_ENABLED["whoop"]:
        tools.append({
            "name": "get_whoop",
            "description": "Get Whoop recovery data — recovery score, HRV, resting heart rate.",
            "input_schema": {
                "type": "object",
                "properties": {"days": {"type": "integer", "description": "Days to pull (default: 1)", "default": 1}}
            }
        })

    if TOOLS_ENABLED["weather"]:
        tools.append({
            "name": "get_weather",
            "description": "Get current weather and 7-day forecast.",
            "input_schema": {"type": "object", "properties": {}}
        })

    if TOOLS_ENABLED["expenses"]:
        tools.extend([
            {
                "name": "log_expense",
                "description": "Log an expense.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "amount": {"type": "number", "description": "Dollar amount"},
                        "category": {"type": "string", "description": "Category"},
                        "description": {"type": "string", "description": "What it was for"}
                    },
                    "required": ["amount", "description"]
                }
            },
            {
                "name": "show_expenses",
                "description": "Show expense summary.",
                "input_schema": {
                    "type": "object",
                    "properties": {"days": {"type": "integer", "description": "Days to look back", "default": 30}}
                }
            },
        ])

    if TOOLS_ENABLED["habits"]:
        tools.extend([
            {
                "name": "log_habit",
                "description": "Log a habit (workout, reading, etc.)",
                "input_schema": {
                    "type": "object",
                    "properties": {"habit": {"type": "string", "description": "Habit to log"}},
                    "required": ["habit"]
                }
            },
            {
                "name": "show_habits",
                "description": "Show habit tracking stats and streaks.",
                "input_schema": {
                    "type": "object",
                    "properties": {"days": {"type": "integer", "description": "Days to look back", "default": 30}}
                }
            },
        ])

    if TOOLS_ENABLED["lists"]:
        tools.extend([
            {
                "name": "note_add",
                "description": "Add item(s) to a named list (grocery, todo, shopping, etc.).",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "list_name": {"type": "string", "description": "List name"},
                        "item": {"type": "string", "description": "Item to add"}
                    },
                    "required": ["list_name", "item"]
                }
            },
            {
                "name": "note_show",
                "description": "Show items on a named list.",
                "input_schema": {
                    "type": "object",
                    "properties": {"list_name": {"type": "string", "description": "List name"}},
                    "required": ["list_name"]
                }
            },
            {
                "name": "note_clear",
                "description": "Clear all items from a list.",
                "input_schema": {
                    "type": "object",
                    "properties": {"list_name": {"type": "string", "description": "List name"}},
                    "required": ["list_name"]
                }
            },
        ])

    # Catch-up tool (always available)
    tools.append({
        "name": "catch_up",
        "description": "Get summary of what happened recently — emails, texts, reminders, calendar.",
        "input_schema": {"type": "object", "properties": {}}
    })

    return tools


TOOLS = build_tools()

db_lock = Lock()

# ============================================================
# Database Setup
# ============================================================
DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "assistant.db"))


def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT, phone_number TEXT NOT NULL,
            what TEXT NOT NULL, event_time TEXT NOT NULL, remind_at TEXT NOT NULL,
            sent INTEGER DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT, sender TEXT NOT NULL,
            role TEXT NOT NULL, content TEXT NOT NULL,
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, phone_number TEXT NOT NULL,
            list_name TEXT NOT NULL, item TEXT NOT NULL, completed INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT, phone_number TEXT NOT NULL,
            amount REAL NOT NULL, category TEXT DEFAULT 'general',
            description TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS habits (
            id INTEGER PRIMARY KEY AUTOINCREMENT, phone_number TEXT NOT NULL,
            habit TEXT NOT NULL, logged_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS user_facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, phone_number TEXT NOT NULL,
            fact TEXT NOT NULL, source TEXT DEFAULT 'conversation',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS bot_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT NOT NULL,
            detail TEXT, tokens_in INTEGER DEFAULT 0, tokens_out INTEGER DEFAULT 0,
            duration_ms INTEGER DEFAULT 0, logged_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
        conn.commit()
    logger.info("Database initialized")


# ============================================================
# Database Helpers
# ============================================================
def save_conversation(sender, role, content):
    with db_lock:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("INSERT INTO conversations (sender, role, content) VALUES (?, ?, ?)",
                         (sender, role, content))
            conn.commit()


def get_conversation_history(sender, limit=20):
    with db_lock:
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                "SELECT role, content FROM conversations WHERE sender = ? ORDER BY id DESC LIMIT ?",
                (sender, limit)).fetchall()
    return [{"role": r[0], "content": r[1]} for r in reversed(rows)]


def add_reminder(phone_number, what, event_time, heads_up_minutes=0):
    remind_at = event_time - timedelta(minutes=heads_up_minutes)
    with db_lock:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("INSERT INTO reminders (phone_number, what, event_time, remind_at) VALUES (?, ?, ?, ?)",
                         (phone_number, what, event_time.isoformat(), remind_at.isoformat()))
            conn.commit()


def get_due_reminders():
    now = datetime.now(tz).replace(tzinfo=None)
    with db_lock:
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                "SELECT id, phone_number, what, event_time, remind_at FROM reminders WHERE sent = 0 AND remind_at <= ?",
                (now.isoformat(),)).fetchall()
    return rows


def mark_reminder_sent(reminder_id):
    with db_lock:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("UPDATE reminders SET sent = 1 WHERE id = ?", (reminder_id,))
            conn.commit()


def add_note(phone_number, list_name, item):
    with db_lock:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("INSERT INTO notes (phone_number, list_name, item) VALUES (?, ?, ?)",
                         (phone_number, list_name.lower().strip(), item))
            conn.commit()


def get_notes(phone_number, list_name):
    with db_lock:
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                "SELECT item, completed FROM notes WHERE phone_number = ? AND list_name = ? AND completed = 0 ORDER BY created_at",
                (phone_number, list_name.lower().strip())).fetchall()
    return rows


def clear_notes(phone_number, list_name):
    with db_lock:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("DELETE FROM notes WHERE phone_number = ? AND list_name = ?",
                         (phone_number, list_name.lower().strip()))
            conn.commit()


def add_expense(phone_number, amount, category="general", description=""):
    with db_lock:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("INSERT INTO expenses (phone_number, amount, category, description) VALUES (?, ?, ?, ?)",
                         (phone_number, amount, category, description))
            conn.commit()


def get_expense_summary(phone_number, days=30):
    cutoff = (datetime.now(tz) - timedelta(days=days)).strftime("%Y-%m-%d")
    with db_lock:
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                "SELECT category, SUM(amount) FROM expenses WHERE phone_number = ? AND created_at >= ? GROUP BY category",
                (phone_number, cutoff)).fetchall()
            total = conn.execute(
                "SELECT SUM(amount) FROM expenses WHERE phone_number = ? AND created_at >= ?",
                (phone_number, cutoff)).fetchone()
    return rows, total[0] or 0


def log_habit(phone_number, habit):
    with db_lock:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("INSERT INTO habits (phone_number, habit) VALUES (?, ?)",
                         (phone_number, habit.lower().strip()))
            conn.commit()


def get_habit_stats(phone_number, days=30):
    cutoff = (datetime.now(tz) - timedelta(days=days)).strftime("%Y-%m-%d")
    with db_lock:
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                "SELECT habit, COUNT(*) as cnt FROM habits WHERE phone_number = ? AND logged_at >= ? GROUP BY habit ORDER BY cnt DESC",
                (phone_number, cutoff)).fetchall()
    return rows


def get_user_facts(phone_number, limit=15):
    with db_lock:
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                "SELECT fact FROM user_facts WHERE phone_number = ? ORDER BY created_at DESC LIMIT ?",
                (phone_number, limit)).fetchall()
    return [r[0] for r in rows]


def save_user_fact(phone_number, fact):
    with db_lock:
        with sqlite3.connect(DB_PATH) as conn:
            existing = conn.execute(
                "SELECT id FROM user_facts WHERE phone_number = ? AND fact = ?",
                (phone_number, fact)).fetchone()
            if not existing:
                conn.execute("INSERT INTO user_facts (phone_number, fact) VALUES (?, ?)",
                             (phone_number, fact))
                conn.commit()
                return True
    return False


def log_bot_metric(event_type, detail=None, tokens_in=0, tokens_out=0, duration_ms=0):
    with db_lock:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO bot_metrics (event_type, detail, tokens_in, tokens_out, duration_ms) VALUES (?, ?, ?, ?, ?)",
                (event_type, detail, tokens_in, tokens_out, duration_ms))
            conn.commit()


# ============================================================
# Mac Mini API Helpers
# ============================================================
def mini_api_get(path, params=None):
    try:
        r = requests.get(
            f"{MAC_MINI_URL}{path}",
            headers={"x-api-key": SERVER_API_KEY},
            params=params,
            timeout=15
        )
        if r.status_code == 200:
            return r.json()
        logger.warning(f"Server API {path}: {r.status_code} {r.text[:200]}")
        return None
    except Exception as e:
        logger.error(f"Server API GET error ({path}): {e}")
        return None


def mini_api_post(path, data):
    try:
        r = requests.post(
            f"{MAC_MINI_URL}{path}",
            headers={"x-api-key": SERVER_API_KEY, "Content-Type": "application/json"},
            json=data,
            timeout=15
        )
        if r.status_code == 200:
            return r.json()
        logger.warning(f"Server API {path}: {r.status_code} {r.text[:200]}")
        return None
    except Exception as e:
        logger.error(f"Server API POST error ({path}): {e}")
        return None


# ============================================================
# Tool Execution
# ============================================================
def execute_web_search(query):
    if not BRAVE_API_KEY:
        return "Web search is not configured (no API key)."
    try:
        resp = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers={"Accept": "application/json", "Accept-Encoding": "gzip", "X-Subscription-Token": BRAVE_API_KEY},
            params={"q": query, "count": 5},
            timeout=10
        )
        if resp.status_code != 200:
            return f"Search failed (status {resp.status_code})"
        results = resp.json().get("web", {}).get("results", [])
        if not results:
            return "No results found."
        output = []
        for r in results[:5]:
            output.append(f"{r.get('title', '')}\n{r.get('description', '')}\n{r.get('url', '')}")
        return "\n\n".join(output)
    except Exception as e:
        return f"Search error: {e}"


def execute_vault_search(query):
    result = mini_api_get("/vault/search", {"query": query})
    if not result:
        return "Vault not reachable."
    matches = result.get("matches", [])
    if not matches:
        return f"No vault files found matching '{query}'."
    output = []
    for match in matches[:3]:
        path = match["path"]
        file_data = mini_api_get("/vault/read", {"path": path})
        if file_data:
            content = file_data.get("content", "")
            output.append(f"**{path}**:\n{content[:1000]}")
        else:
            output.append(f"**{path}** (matched by {match.get('match', 'unknown')})")
    return "\n\n---\n\n".join(output)


def execute_vault_read(path):
    result = mini_api_get("/vault/read", {"path": path})
    if not result:
        return f"File not found: {path}"
    return result.get("content", f"File not found: {path}")


def execute_vault_save(title, content, folder="Inbox"):
    now = datetime.now(tz)
    filename = re.sub(r'[^\w\s-]', '', title).strip().replace(' ', ' ')
    path = f"{folder}/{filename}.md"
    body = f"---\ntitle: {title}\ndate: {now.strftime('%Y-%m-%d')}\ntags:\n  - from/{BOT_NAME.lower()}\n---\n\n{content}\n"
    result = mini_api_post("/vault/write", {"path": path, "content": body})
    if result and result.get("status") == "ok":
        return f"Saved '{title}' to {folder}/"
    return "Failed to save to vault."


def execute_tool(tool_name, tool_input, sender):
    try:
        if tool_name == "web_search":
            return execute_web_search(tool_input["query"])
        elif tool_name == "vault_search":
            return execute_vault_search(tool_input["query"])
        elif tool_name == "vault_read":
            return execute_vault_read(tool_input["path"])
        elif tool_name == "vault_save":
            return execute_vault_save(tool_input["title"], tool_input["content"], tool_input.get("folder", "Inbox"))
        elif tool_name == "set_reminder":
            event_time = datetime.fromisoformat(tool_input["when"])
            add_reminder(sender, tool_input["what"], event_time, tool_input.get("heads_up_minutes", 0))
            return f"Reminder set: '{tool_input['what']}' at {event_time.strftime('%A %-I:%M %p')}"
        elif tool_name == "log_expense":
            add_expense(sender, tool_input["amount"], tool_input.get("category", "general"), tool_input.get("description", ""))
            return f"Logged ${tool_input['amount']:.2f} for {tool_input.get('description', 'expense')}"
        elif tool_name == "show_expenses":
            days = tool_input.get("days", 30)
            rows, total = get_expense_summary(sender, days)
            if not rows:
                return f"No expenses in the last {days} days."
            lines = [f"Last {days} days:"]
            for cat, amt in rows:
                lines.append(f"  {cat}: ${amt:.2f}")
            lines.append(f"Total: ${total:.2f}")
            return "\n".join(lines)
        elif tool_name == "log_habit":
            log_habit(sender, tool_input["habit"])
            return f"Logged: {tool_input['habit']}"
        elif tool_name == "show_habits":
            days = tool_input.get("days", 30)
            stats = get_habit_stats(sender, days)
            if not stats:
                return f"No habits logged in the last {days} days."
            lines = [f"Last {days} days:"]
            for habit, count in stats:
                lines.append(f"  {habit}: {count}x")
            return "\n".join(lines)
        elif tool_name == "note_add":
            add_note(sender, tool_input["list_name"], tool_input["item"])
            return f"Added '{tool_input['item']}' to {tool_input['list_name']} list"
        elif tool_name == "note_show":
            items = get_notes(sender, tool_input["list_name"])
            if not items:
                return f"Your {tool_input['list_name']} list is empty."
            lines = [f"{tool_input['list_name']} list:"]
            for i, (item, _) in enumerate(items, 1):
                lines.append(f"  {i}. {item}")
            return "\n".join(lines)
        elif tool_name == "note_clear":
            clear_notes(sender, tool_input["list_name"])
            return f"Cleared {tool_input['list_name']} list"
        elif tool_name == "get_calendar":
            days = tool_input.get("days", 1)
            result = mini_api_get("/calendar/today") if days <= 1 else mini_api_get("/calendar/range", {"days": days})
            if not result:
                return "Couldn't reach the calendar. Server may be offline."
            events = result.get("events", [])
            if not events:
                return "No events found."
            lines = []
            for evt in events:
                line = f"- {evt.get('summary', '?')} -- {evt.get('start', '?')} to {evt.get('end', '?')}"
                if evt.get("calendar"):
                    line += f" ({evt['calendar']})"
                lines.append(line)
            return "\n".join(lines)
        elif tool_name == "create_calendar_event":
            data = {"summary": tool_input["summary"], "start": tool_input["start"],
                    "end": tool_input["end"], "calendar_name": tool_input.get("calendar_name", "Home")}
            result = mini_api_post("/calendar/create", data)
            if result and result.get("status") == "ok":
                return f"Added '{tool_input['summary']}' to your calendar."
            return "Failed to create calendar event."
        elif tool_name == "edit_calendar_event":
            data = {"original_summary": tool_input["original_summary"]}
            if tool_input.get("new_summary"):
                data["new_summary"] = tool_input["new_summary"]
            if tool_input.get("new_start"):
                data["new_start"] = tool_input["new_start"]
            if tool_input.get("new_end"):
                data["new_end"] = tool_input["new_end"]
            result = mini_api_post("/calendar/edit", data)
            if result and result.get("status") == "ok":
                return f"Updated '{tool_input['original_summary']}'."
            return "Couldn't find or update that event."
        elif tool_name == "delete_calendar_event":
            result = mini_api_post("/calendar/delete", {"summary": tool_input["summary"]})
            if result and result.get("status") == "ok":
                return f"Deleted '{tool_input['summary']}'."
            return "Couldn't find that event."
        elif tool_name == "search_contacts":
            result = mini_api_get("/contacts/search", {"name": tool_input["name"]})
            if not result:
                return "Couldn't reach contacts."
            contacts = result.get("contacts", [])
            if not contacts:
                return f"No contacts found for '{tool_input['name']}'."
            lines = []
            for c in contacts:
                line = f"- {c.get('name', '?')}"
                if c.get("phones"):
                    line += f" -- {c['phones']}"
                if c.get("emails"):
                    line += f" -- {c['emails']}"
                lines.append(line)
            return "\n".join(lines)
        elif tool_name == "get_reminders":
            result = mini_api_get("/reminders")
            if not result:
                return "Couldn't reach reminders."
            reminders = result.get("reminders", [])
            if not reminders:
                return "No open reminders."
            lines = []
            for r in reminders:
                line = f"- {r.get('name', '?')}"
                if r.get("due"):
                    line += f" -- due: {r['due']}"
                lines.append(line)
            return "\n".join(lines)
        elif tool_name == "complete_reminder":
            result = mini_api_post("/reminders/complete", {"name": tool_input["name"]})
            if result and result.get("status") == "ok":
                return f"Done -- checked off '{tool_input['name']}'."
            return f"Couldn't find a reminder matching '{tool_input['name']}'."
        elif tool_name == "create_reminder":
            data = {"name": tool_input["name"]}
            if tool_input.get("due"):
                data["due"] = tool_input["due"]
            result = mini_api_post("/reminders/create", data)
            if result and result.get("status") == "ok":
                return f"Reminder created: {tool_input['name']}"
            return "Failed to create reminder."
        elif tool_name == "person_lookup":
            name = tool_input["name"]
            parts = []
            contacts = mini_api_get("/contacts/search", {"name": name})
            if contacts and contacts.get("contacts"):
                for c in contacts["contacts"]:
                    line = f"Contact: {c.get('name', '?')}"
                    if c.get("phones"):
                        line += f" -- {c['phones']}"
                    if c.get("emails"):
                        line += f" -- {c['emails']}"
                    parts.append(line)
            msgs = mini_api_get("/messages/recent", {"contact": name, "limit": 5})
            if msgs and msgs.get("messages"):
                lines = []
                for m in msgs["messages"]:
                    who = OWNER_NAME if m.get("from_me") else "them"
                    lines.append(f"[{m.get('date', '?')}] {who}: {m.get('text', '')[:150]}")
                parts.append("Recent texts:\n" + "\n".join(lines))
            vault = mini_api_get("/vault/search", {"query": name})
            if vault and vault.get("matches"):
                vault_files = [m["path"] for m in vault["matches"][:5]]
                parts.append("Vault mentions: " + ", ".join(vault_files))
            emails = mini_api_get("/email/search", {"query": f"from:{name}", "limit": 3})
            if emails and emails.get("emails"):
                lines = []
                for e in emails["emails"]:
                    lines.append(f"From: {e.get('from', '?')} -- {e.get('subject', '?')}")
                parts.append("Recent emails:\n" + "\n".join(lines))
            if not parts:
                return f"No info found on '{name}'."
            return "\n\n".join(parts)
        elif tool_name == "get_messages":
            params = {"limit": tool_input.get("limit", 10)}
            if tool_input.get("contact"):
                params["contact"] = tool_input["contact"]
            result = mini_api_get("/messages/recent", params)
            if not result:
                return "Couldn't reach messages."
            messages = result.get("messages", [])
            if not messages:
                return "No recent messages found."
            lines = []
            for m in messages:
                who = OWNER_NAME if m.get("from_me") else m.get("contact", "them")
                lines.append(f"[{m.get('date', '?')}] {who}: {m.get('text', '')[:200]}")
            return "\n".join(lines)
        elif tool_name == "check_email":
            query = tool_input.get("query", "is:unread")
            limit = tool_input.get("limit", 5)
            result = mini_api_get("/email/search", {"query": query, "limit": limit})
            if not result or result.get("error"):
                return f"Couldn't check email: {result.get('error', 'Server may be offline.')}"
            emails = result.get("emails", [])
            if not emails:
                return "No emails matching that search."
            lines = []
            for e in emails:
                lines.append(f"- From: {e.get('from', '?')}\n  Subject: {e.get('subject', '?')}\n  {e.get('snippet', '')[:150]}")
            return "\n\n".join(lines)
        elif tool_name == "read_email":
            params = {}
            if tool_input.get("subject"):
                params["subject"] = tool_input["subject"]
            if tool_input.get("from_addr"):
                params["from_addr"] = tool_input["from_addr"]
            if not params:
                return "Need a subject or sender to find the email."
            result = mini_api_get("/email/read", params)
            if not result or result.get("error"):
                return f"Couldn't read email: {result.get('error', 'not found')}"
            return f"From: {result.get('from', '?')}\nSubject: {result.get('subject', '?')}\nDate: {result.get('date', '?')}\n\n{result.get('body', 'No content')}"
        elif tool_name == "get_whoop":
            days = tool_input.get("days", 1)
            result = mini_api_get("/whoop/recovery", {"limit": days})
            if not result:
                return "Couldn't reach Whoop."
            records = result.get("records", [])
            if not records:
                return "No Whoop data available."
            lines = []
            for rec in records:
                score = rec.get("score", {})
                date = rec.get("created_at", "?")[:10]
                recovery = score.get("recovery_score", "?")
                hrv = score.get("hrv_rmssd_milli", 0)
                rhr = score.get("resting_heart_rate", "?")
                lines.append(f"{date}: Recovery {recovery}% | HRV {hrv:.1f}ms | RHR {rhr}")
            return "\n".join(lines)
        elif tool_name == "get_weather":
            result = mini_api_get("/weather")
            if not result:
                return "Couldn't reach weather."
            current = result.get("current", {})
            forecast = result.get("forecast", [])
            lines = [
                f"Now: {current.get('temperature_f', '?')}F (feels {current.get('feels_like_f', '?')}F) -- {current.get('condition', '?')}, "
                f"humidity {current.get('humidity_pct', '?')}%, wind {current.get('wind_mph', '?')} mph"
            ]
            for day in forecast[:7]:
                lines.append(f"- {day['date']}: {day.get('condition', '?')} -- {day.get('high_f', '?')}/{day.get('low_f', '?')}F, {day.get('precip_chance_pct', 0)}% rain")
            return "\n".join(lines)
        elif tool_name == "catch_up":
            sections = []
            sections.append("Catch-up:")
            try:
                email_data = mini_api_get("/email/search", {"query": "is:unread", "limit": 10})
                if email_data and email_data.get("emails"):
                    promo_keywords = ["noreply", "newsletter", "promo", "marketing", "unsubscribe", "donotreply", "no-reply"]
                    real_emails = [e for e in email_data["emails"] if not any(kw in e.get("from", "").lower() for kw in promo_keywords)]
                    if real_emails:
                        email_lines = [f"  - {e.get('from', '?')}: {e.get('subject', '?')}" for e in real_emails[:5]]
                        sections.append("Unread emails:\n" + "\n".join(email_lines))
            except Exception:
                pass
            try:
                msg_data = mini_api_get("/messages/recent", {"limit": 20})
                if msg_data and msg_data.get("messages"):
                    by_contact = {}
                    for m in msg_data["messages"]:
                        if m.get("from_me"):
                            continue
                        contact = m.get("contact", "?")
                        if contact not in by_contact:
                            by_contact[contact] = m
                    if by_contact:
                        text_lines = [f"  - {c}: {(m.get('text') or '')[:80]}" for c, m in list(by_contact.items())[:6]]
                        sections.append("Recent texts:\n" + "\n".join(text_lines))
            except Exception:
                pass
            try:
                cal_data = mini_api_get("/calendar/today")
                if cal_data and cal_data.get("events"):
                    cal_lines = [f"  - {e.get('summary', '?')}" for e in cal_data["events"]]
                    sections.append("Today's calendar:\n" + "\n".join(cal_lines))
            except Exception:
                pass
            return "\n\n".join(sections)
        else:
            return f"Unknown tool: {tool_name}"
    except Exception as e:
        logger.error(f"Tool execution error ({tool_name}): {e}")
        return f"Error executing {tool_name}: {e}"


# ============================================================
# Core: Claude with Tools
# ============================================================
def generate_reply_with_tools(sender, message, media_url=None):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    now = datetime.now(tz).strftime("%A, %B %d, %Y at %-I:%M %p")

    # Pull vault context
    vault_context_parts = []
    personal = execute_vault_read("Context/personal-context.md")
    if personal and "File not found" not in personal and "error" not in personal.lower():
        vault_context_parts.append(personal[:3000])

    vault_context = "\n\n".join(vault_context_parts) if vault_context_parts else "Vault context unavailable — use tools to look up info."

    facts = get_user_facts(sender, limit=15)
    user_context = ""
    if facts:
        user_context = "Things you've learned from past conversations:\n" + "\n".join(f"- {f}" for f in facts)

    system = SYSTEM_PROMPT_TEMPLATE.replace("{bot_name}", BOT_NAME).replace("{owner_name}", OWNER_NAME).replace(
        "{vault_context}", vault_context).replace("{user_context}", user_context).replace("{current_time}", now)

    history = get_conversation_history(sender, limit=CONVERSATION_LIMIT)

    # Handle image messages
    if media_url and media_url.strip():
        user_content = []
        if message:
            user_content.append({"type": "text", "text": message})
        try:
            img_resp = requests.get(media_url, timeout=15)
            if img_resp.status_code == 200:
                content_type = img_resp.headers.get("content-type", "image/jpeg")
                if "image" in content_type:
                    img_b64 = base64.b64encode(img_resp.content).decode()
                    media_type = content_type.split(";")[0].strip()
                    user_content.append({"type": "image", "source": {"type": "base64", "media_type": media_type, "data": img_b64}})
                else:
                    user_content.append({"type": "text", "text": f"[sent a file: {media_url}]"})
        except Exception as e:
            logger.error(f"Image fetch error: {e}")
            user_content.append({"type": "text", "text": f"[sent an image: {media_url}]"})
        if not user_content:
            user_content.append({"type": "text", "text": "[sent an image]"})
        history.append({"role": "user", "content": user_content})
        save_conversation(sender, "user", f"{message} [image: {media_url}]" if message else f"[image: {media_url}]")
    else:
        history.append({"role": "user", "content": message})
        save_conversation(sender, "user", message)

    log_bot_metric("message_in", detail=message[:100] if isinstance(message, str) else "image")

    import time as _time
    _api_start = _time.time()

    try:
        response = client.messages.create(
            model=CLAUDE_MODEL, max_tokens=MAX_TOKENS,
            system=system, tools=TOOLS, messages=history
        )
        _api_duration = int((_time.time() - _api_start) * 1000)
        log_bot_metric("api_call", tokens_in=response.usage.input_tokens, tokens_out=response.usage.output_tokens, duration_ms=_api_duration)
    except Exception as e:
        logger.error(f"Claude API error: {e}")
        log_bot_metric("error", detail=f"api_call: {str(e)[:200]}")
        return "Sorry, I hit a snag. Try again in a sec."

    max_iterations = 5
    iteration = 0
    messages = list(history)

    while response.stop_reason == "tool_use" and iteration < max_iterations:
        iteration += 1
        tool_results = []
        assistant_content = response.content

        for block in response.content:
            if block.type == "tool_use":
                logger.info(f"Tool call: {block.name}({json.dumps(block.input)[:200]})")
                log_bot_metric("tool_call", detail=block.name)
                result = execute_tool(block.name, block.input, sender)
                tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": result})

        messages.append({"role": "assistant", "content": assistant_content})
        messages.append({"role": "user", "content": tool_results})

        try:
            _api_start = _time.time()
            response = client.messages.create(
                model=CLAUDE_MODEL, max_tokens=MAX_TOKENS,
                system=system, tools=TOOLS, messages=messages
            )
            _api_duration = int((_time.time() - _api_start) * 1000)
            log_bot_metric("api_call", tokens_in=response.usage.input_tokens, tokens_out=response.usage.output_tokens, duration_ms=_api_duration)
        except Exception as e:
            logger.error(f"Claude API error (tool loop): {e}")
            return "Hit a snag processing that. Try again?"

    reply = ""
    for block in response.content:
        if hasattr(block, "text"):
            reply += block.text

    if not reply:
        reply = "Done."

    save_conversation(sender, "assistant", reply)

    # Background fact extraction
    if FACT_EXTRACTION and isinstance(message, str) and len(message.strip()) > 20:
        try:
            extract_user_facts(sender, message, reply)
        except Exception as e:
            logger.error(f"Fact extraction error: {e}")

    return reply


def extract_user_facts(sender, user_message, assistant_reply):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    prompt = f"""Analyze this exchange and extract key facts about the user worth remembering. Look for:
- Preferences, plans, goals, upcoming events
- People mentioned, personal details, decisions made

User said: {user_message}
Assistant replied: {assistant_reply}

Return ONLY a JSON array of fact strings. If nothing notable, return []."""

    try:
        response = client.messages.create(
            model=CLAUDE_MODEL, max_tokens=300,
            system="Extract facts as JSON array. Return ONLY valid JSON.",
            messages=[{"role": "user", "content": prompt}]
        )
        text = response.content[0].text.strip()
        facts = json.loads(text)
        if isinstance(facts, list):
            for fact in facts:
                if isinstance(fact, str) and len(fact) > 5:
                    saved = save_user_fact(sender, fact)
                    if saved:
                        logger.info(f"Saved fact: {fact}")
                        # Sync to vault
                        sync_fact_to_vault(fact)
    except (json.JSONDecodeError, Exception) as e:
        logger.debug(f"Fact extraction parse error: {e}")


def sync_fact_to_vault(fact):
    now = datetime.now(tz)
    path = f"Context/{BOT_NAME.lower()}-learnings.md"
    existing = mini_api_get("/vault/read", {"path": path})
    if not existing or "content" not in existing:
        header = f"---\ntype: context\nupdated: {now.strftime('%Y-%m-%d')}\n---\n\n# {BOT_NAME} Learnings\n\nFacts learned from iMessage conversations. Auto-updated.\n"
        mini_api_post("/vault/write", {"path": path, "content": header})
    fact_line = f"\n## {now.strftime('%Y-%m-%d')}\n- {fact}\n"
    mini_api_post("/vault/append", {"path": path, "content": fact_line})


def generate_simple_reply(sender, message, system_prompt):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    history = get_conversation_history(sender, limit=CONVERSATION_LIMIT)
    history.append({"role": "user", "content": message})
    try:
        response = client.messages.create(
            model=CLAUDE_MODEL, max_tokens=500,
            system=system_prompt, messages=history
        )
        reply = response.content[0].text
        save_conversation(sender, "user", message)
        save_conversation(sender, "assistant", reply)
        return reply
    except Exception as e:
        logger.error(f"Claude API error: {e}")
        return "Something went wrong. Try again."


# ============================================================
# SendBlue Messaging
# ============================================================
def split_message(text, max_len=1500):
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        split_at = text.rfind(". ", 0, max_len)
        if split_at == -1:
            split_at = text.rfind(" ", 0, max_len)
        if split_at == -1:
            split_at = max_len
        else:
            split_at += 1
        chunks.append(text[:split_at].strip())
        text = text[split_at:].strip()
    return chunks


def strip_markdown(text):
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    return text


def send_reply(to_number, message):
    message = strip_markdown(message)
    chunks = split_message(message, 1500)
    for chunk in chunks:
        try:
            resp = requests.post(
                "https://api.sendblue.co/api/send-message",
                headers={
                    "sb-api-key-id": SENDBLUE_API_KEY,
                    "sb-api-secret-key": SENDBLUE_SECRET_KEY,
                    "Content-Type": "application/json",
                },
                json={"number": to_number, "content": chunk, "from_number": SENDBLUE_FROM},
                timeout=15,
            )
            logger.info(f"SendBlue reply: {resp.status_code}")
        except Exception as e:
            logger.error(f"send_reply error: {e}")


def send_group_reply(group_id, message):
    message = strip_markdown(message)
    chunks = split_message(message, 1500)
    for chunk in chunks:
        try:
            requests.post(
                "https://api.sendblue.co/api/send-group-message",
                headers={
                    "sb-api-key-id": SENDBLUE_API_KEY,
                    "sb-api-secret-key": SENDBLUE_SECRET_KEY,
                    "Content-Type": "application/json",
                },
                json={"group_id": group_id, "content": chunk, "from_number": SENDBLUE_FROM},
                timeout=15,
            )
        except Exception as e:
            logger.error(f"send_group_reply error: {e}")


def is_owner(phone_number):
    return MY_PHONE_NUMBER and phone_number == MY_PHONE_NUMBER


# ============================================================
# Morning Briefing
# ============================================================
def send_morning_briefing():
    if not MY_PHONE_NUMBER:
        return

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    now = datetime.now(tz)
    context_parts = []

    calendar_data = mini_api_get("/calendar/today")
    if calendar_data and calendar_data.get("events"):
        events_text = "\n".join(f"- {e.get('summary', '?')} -- {e.get('start', '?')} to {e.get('end', '?')}" for e in calendar_data["events"])
        context_parts.append(f"TODAY'S CALENDAR:\n{events_text}")

    tomorrow_data = mini_api_get("/calendar/range", {"days": 2})
    if tomorrow_data and tomorrow_data.get("events"):
        tomorrow_str = (now + timedelta(days=1)).strftime("%B %-d")
        tomorrow_events = [e for e in tomorrow_data["events"] if tomorrow_str in e.get("start", "")]
        if tomorrow_events:
            events_text = "\n".join(f"- {e.get('summary', '?')} -- {e.get('start', '?')}" for e in tomorrow_events)
            context_parts.append(f"TOMORROW'S CALENDAR:\n{events_text}")

    reminders_data = mini_api_get("/reminders")
    if reminders_data and reminders_data.get("reminders"):
        rem_text = "\n".join(f"- {r.get('name', '?')}" + (f" -- due: {r['due']}" if r.get('due') else "") for r in reminders_data["reminders"])
        context_parts.append(f"OPEN REMINDERS:\n{rem_text}")

    if TOOLS_ENABLED.get("whoop"):
        whoop_data = mini_api_get("/whoop/recovery", {"limit": 1})
        if whoop_data and whoop_data.get("records"):
            rec = whoop_data["records"][0]
            score = rec.get("score", {})
            context_parts.append(f"WHOOP RECOVERY: {score.get('recovery_score', '?')}% | HRV {score.get('hrv_rmssd_milli', 0):.1f}ms | RHR {score.get('resting_heart_rate', '?')}")

    weather_data = mini_api_get("/weather")
    if weather_data and weather_data.get("current"):
        cur = weather_data["current"]
        context_parts.append(f"WEATHER: {cur.get('temperature_f', '?')}F, {cur.get('condition', '?')}")

    vault_context = "\n\n---\n\n".join(context_parts)

    briefing_prompt = f"""Based on the following context about {OWNER_NAME}'s life, write a morning briefing text for today ({now.strftime('%A, %B %d')}).

Include:
1. Today's scheduled events with times
2. 1-2 most important things to focus on
3. Time-sensitive reminders or deadlines
4. Quick weather note
5. Heads up on anything early tomorrow

Keep it SHORT — 3-5 sentences. Natural text message, not bullet points.

{vault_context}"""

    try:
        response = client.messages.create(
            model=CLAUDE_MODEL, max_tokens=500,
            system=f"You are {BOT_NAME}, {OWNER_NAME}'s personal AI. Write a brief morning text. Casual, direct, no fluff.",
            messages=[{"role": "user", "content": briefing_prompt}]
        )
        briefing = response.content[0].text
        send_reply(MY_PHONE_NUMBER, f"Good morning.\n\n{briefing}")
        logger.info("Morning briefing sent")
    except Exception as e:
        logger.error(f"Morning briefing error: {e}")


# ============================================================
# Evening Wrap
# ============================================================
def send_evening_wrap():
    if not MY_PHONE_NUMBER:
        return

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    now = datetime.now(tz)
    context_parts = []

    tomorrow_data = mini_api_get("/calendar/range", {"days": 2})
    if tomorrow_data and tomorrow_data.get("events"):
        tomorrow_str = (now + timedelta(days=1)).strftime("%B %-d")
        tomorrow_events = [e for e in tomorrow_data["events"] if tomorrow_str in e.get("start", "")]
        if tomorrow_events:
            events_text = "\n".join(f"- {e.get('summary', '?')} -- {e.get('start', '?')}" for e in tomorrow_events)
            context_parts.append(f"TOMORROW'S CALENDAR:\n{events_text}")

    reminders_data = mini_api_get("/reminders")
    if reminders_data and reminders_data.get("reminders"):
        rem_text = "\n".join(f"- {r.get('name', '?')}" for r in reminders_data["reminders"][:8])
        context_parts.append(f"OPEN REMINDERS:\n{rem_text}")

    weather_data = mini_api_get("/weather")
    if weather_data and weather_data.get("forecast") and len(weather_data["forecast"]) > 1:
        tmrw = weather_data["forecast"][1]
        context_parts.append(f"TOMORROW'S WEATHER: {tmrw.get('condition', '?')}, High {tmrw.get('high_f', '?')}F / Low {tmrw.get('low_f', '?')}F")

    vault_context = "\n\n---\n\n".join(context_parts)

    prompt = f"""Write {OWNER_NAME}'s evening wrap-up for tonight ({now.strftime('%A, %B %d')}).

Include:
1. Tomorrow's schedule
2. 1-2 suggested things to work on tomorrow
3. Tomorrow's weather in one line

Keep it concise. Casual tone.

{vault_context}"""

    try:
        response = client.messages.create(
            model=CLAUDE_MODEL, max_tokens=500,
            system=f"You are {BOT_NAME}, {OWNER_NAME}'s personal AI. Write a brief evening text.",
            messages=[{"role": "user", "content": prompt}]
        )
        wrap = response.content[0].text
        send_reply(MY_PHONE_NUMBER, f"Evening wrap-up\n\n{wrap}")
        logger.info("Evening wrap-up sent")
    except Exception as e:
        logger.error(f"Evening wrap-up error: {e}")


# ============================================================
# Proactive Checks
# ============================================================
_proactive_alerts_sent = set()


def check_proactive():
    if not MY_PHONE_NUMBER or not PROACTIVE_CHECKS:
        return

    now = datetime.now(tz)
    alerts = []

    # Check overdue reminders
    try:
        reminders_data = mini_api_get("/reminders")
        if reminders_data and reminders_data.get("reminders"):
            for r in reminders_data["reminders"]:
                due = r.get("due", "")
                name = r.get("name", "")
                if not due:
                    continue
                alert_key = f"reminder:{name}"
                if alert_key in _proactive_alerts_sent:
                    continue
                due_clean = due.replace("\u202f", " ")
                for fmt in ["%A, %B %d, %Y at %I:%M:%S %p", "%B %d, %Y at %I:%M:%S %p"]:
                    try:
                        due_dt = datetime.strptime(due_clean, fmt)
                        due_dt = tz.localize(due_dt)
                        if due_dt < now:
                            alerts.append(f"Overdue reminder: {name}")
                            _proactive_alerts_sent.add(alert_key)
                        break
                    except ValueError:
                        continue
    except Exception:
        pass

    for alert in alerts[:3]:
        send_reply(MY_PHONE_NUMBER, alert)


# ============================================================
# Reminder Scheduler
# ============================================================
def check_reminders():
    due = get_due_reminders()
    for r_id, phone, what, event_time_str, remind_at_str in due:
        try:
            mark_reminder_sent(r_id)
            event_time = datetime.fromisoformat(event_time_str)
            time_str = event_time.strftime("%-I:%M %p")
            send_reply(phone, f"Reminder: {what} at {time_str}")
            logger.info(f"Sent reminder: {what}")
        except Exception as e:
            logger.error(f"Reminder {r_id} failed: {e}")


# ============================================================
# Webhook
# ============================================================
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(silent=True) or {}
    logger.info(f"Webhook: {str(data)[:300]}")

    if data.get("is_outbound"):
        return jsonify({"status": "ignored"}), 200

    sender = data.get("from_number", "")
    content = data.get("content", "").strip()
    group_id = data.get("group_id", "")
    media_url = data.get("media_url", "")

    if not sender or (not content and not media_url):
        return jsonify({"status": "ignored"}), 200

    # Group chat
    if group_id:
        chat_key = f"group:{group_id}"
        if GROUP_MODE == "ignore" and not is_owner(sender):
            return jsonify({"status": "ignored"}), 200
        if is_owner(sender):
            reply = generate_simple_reply(chat_key, content, SYSTEM_PROMPT_TEMPLATE.replace("{bot_name}", BOT_NAME).replace("{owner_name}", OWNER_NAME).replace("{vault_context}", "").replace("{user_context}", "").replace("{current_time}", datetime.now(tz).strftime("%A, %B %d, %Y at %-I:%M %p")))
        elif GROUP_MODE == "snarky":
            reply = generate_simple_reply(chat_key, content, SNARKY_SYSTEM_PROMPT)
        else:
            reply = generate_simple_reply(chat_key, content, FRIENDLY_SYSTEM_PROMPT)
        send_group_reply(group_id, reply)
        return jsonify({"status": "ok"}), 200

    # Non-owner 1:1
    if not is_owner(sender) and MY_PHONE_NUMBER:
        if STRANGER_MODE == "ignore":
            return jsonify({"status": "ignored"}), 200
        reply = generate_simple_reply(sender, content, FRIENDLY_SYSTEM_PROMPT)
        send_reply(sender, reply)
        return jsonify({"status": "ok"}), 200

    # Full tool-use flow for owner
    if media_url and not content:
        content = "What's in this image? Describe what you see."
    reply = generate_reply_with_tools(sender, content, media_url=media_url)
    send_reply(sender, reply)
    return jsonify({"status": "ok"}), 200


# ============================================================
# Health Check & Relay
# ============================================================
@app.route("/", methods=["GET"])
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "product": "djangocli", "version": "1.0", "timestamp": datetime.now(tz).isoformat()})


@app.route("/relay/send", methods=["POST"])
def relay_send():
    auth = request.headers.get("x-api-key", "")
    if not auth or auth != SERVER_API_KEY:
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json() or {}
    message = data.get("message", "")
    to = data.get("to", MY_PHONE_NUMBER)
    if not message:
        return jsonify({"error": "no message"}), 400
    send_reply(to, message)
    return jsonify({"status": "sent", "to": to})


# ============================================================
# Startup
# ============================================================
init_db()

scheduler = BackgroundScheduler(timezone=tz)

# Reminder checker — every minute
scheduler.add_job(check_reminders, IntervalTrigger(minutes=1), id="check_reminders")

# Morning briefing
_morning = (_schedule.get("morning_briefing") or {})
if _morning.get("enabled", True):
    scheduler.add_job(send_morning_briefing,
                      CronTrigger(hour=_morning.get("hour", 9), minute=_morning.get("minute", 0)),
                      id="morning_briefing")

# Evening wrap
_evening = (_schedule.get("evening_wrap") or {})
if _evening.get("enabled", True):
    scheduler.add_job(send_evening_wrap,
                      CronTrigger(hour=_evening.get("hour", 22), minute=_evening.get("minute", 0)),
                      id="evening_wrap")

# Proactive checks
if PROACTIVE_CHECKS:
    scheduler.add_job(check_proactive,
                      IntervalTrigger(minutes=PROACTIVE_INTERVAL),
                      id="proactive_checks")

# Custom scheduled messages
for custom in (_schedule.get("custom") or []):
    if custom.get("message"):
        msg = custom["message"]
        scheduler.add_job(lambda m=msg: send_reply(MY_PHONE_NUMBER, m),
                          CronTrigger(hour=custom.get("hour", 12), minute=custom.get("minute", 0)),
                          id=f"custom_{custom.get('name', 'msg')}")

scheduler.start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5001)), debug=False)
