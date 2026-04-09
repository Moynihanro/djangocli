# DjangoCLI

A personal AI assistant that lives in iMessage, runs on your Mac, and knows your life.

**What you get:** Text your AI assistant from iMessage — it can check your calendar, search your notes, look up contacts, manage reminders, read your email, track expenses, check weather, and more. It runs on your hardware, talks to your data, and costs ~$30/mo in API fees.

## Architecture

```
┌─────────────────────┐     ┌──────────────────────────┐
│   iMessage (you)    │     │     Render / Local        │
│                     │────>│     Flask Bot (bot/)       │
│   SendBlue gateway  │<────│     Claude API + Tools     │
└─────────────────────┘     └──────────┬───────────────┘
                                       │ HTTP via Tailscale
                            ┌──────────▼───────────────┐
                            │     Mac Mini Server       │
                            │     FastAPI (server/)     │
                            │                           │
                            │  ┌─────────┐ ┌─────────┐ │
                            │  │Calendar │ │Contacts │ │
                            │  │Reminders│ │Messages │ │
                            │  │Vault    │ │Email    │ │
                            │  │Weather  │ │Whoop    │ │
                            │  └─────────┘ └─────────┘ │
                            └───────────────────────────┘
```

**Bot** (deploys to Render or runs locally): Receives iMessages via SendBlue webhook, processes them with Claude (tool-use), sends replies back. Handles scheduling (morning briefings, evening wraps, reminders).

**Server** (runs on your Mac Mini): FastAPI server exposing your local macOS services — Calendar, Contacts, Reminders, iMessage history, Obsidian vault, Gmail, Whoop, and weather. Connected to the bot via Tailscale VPN.

## Quickstart

### Prerequisites
- Mac (Mini recommended) running macOS
- Python 3.10+
- Xcode Command Line Tools (`xcode-select --install`)
- [Tailscale](https://tailscale.com/) (for remote access)

### Accounts Needed
- **Anthropic** — Claude API key ([console.anthropic.com](https://console.anthropic.com))
- **SendBlue** — iMessage API gateway ([sendblue.co](https://sendblue.co)) ~$6/mo
- **Brave Search** (optional) — Web search API ([api.search.brave.com](https://api.search.brave.com))
- **Gmail App Password** (optional) — For email integration
- **Whoop** (optional) — Developer API access

### Setup (~30 minutes)

```bash
git clone https://github.com/YOUR_USERNAME/djangocli.git
cd djangocli
./setup.sh
```

The setup wizard will:
1. Collect your API keys and preferences
2. Generate `config.yaml`
3. Compile the `pim-tool` Swift binary (Calendar/Contacts/Reminders access)
4. Install Python dependencies for both bot and server
5. Create a launchd service for auto-start

### Post-Setup

1. **Grant macOS permissions** (one-time):
   ```bash
   ./server/pim-tool calendar today      # Click "Allow"
   ./server/pim-tool reminders list      # Click "Allow"
   ./server/pim-tool contacts search "test"  # Click "Allow"
   ```

2. **Start the server**:
   ```bash
   launchctl load ~/Library/LaunchAgents/com.djangocli.server.plist
   ```

3. **Test the server**:
   ```bash
   curl -H "x-api-key: YOUR_API_KEY" http://localhost:8000/health
   ```

4. **Deploy the bot** (Render recommended):
   - Push to GitHub
   - Create a Web Service on Render from the `bot/` directory
   - Set environment variables (see below)
   - Set SendBlue webhook URL to `https://YOUR-APP.onrender.com/webhook`

5. **Text your bot!**

## Environment Variables (for Render deployment)

The bot reads from `config.yaml` when available, but on Render you'll set these as env vars:

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Claude API key |
| `SENDBLUE_API_KEY` | SendBlue API key |
| `SENDBLUE_SECRET_KEY` | SendBlue secret |
| `SENDBLUE_FROM` | SendBlue phone number |
| `MY_PHONE_NUMBER` | Your phone number |
| `MAC_MINI_URL` | Server URL (Tailscale IP) |
| `MAC_MINI_API_KEY` | Server API key |
| `BRAVE_API_KEY` | Brave Search key (optional) |
| `TIMEZONE` | Your timezone |
| `OWNER_NAME` | Your name |

## Tools (25+)

| Tool | What it does |
|------|-------------|
| `web_search` | Search the web via Brave Search |
| `vault_search` / `vault_read` / `vault_save` | Obsidian vault CRUD |
| `get_calendar` / `create_calendar_event` / `edit_calendar_event` / `delete_calendar_event` | Apple Calendar |
| `search_contacts` | Apple Contacts lookup |
| `get_reminders` / `create_reminder` / `complete_reminder` | Apple Reminders |
| `set_reminder` | Timed text reminders |
| `get_messages` | iMessage history |
| `person_lookup` | Everything known about a person |
| `check_email` / `read_email` | Gmail via IMAP |
| `get_whoop` | Whoop recovery data |
| `get_weather` | 7-day forecast |
| `log_expense` / `show_expenses` | Expense tracking |
| `log_habit` / `show_habits` | Habit tracking |
| `note_add` / `note_show` / `note_clear` | List management |
| `catch_up` | "What did I miss?" summary |

All tools are configurable — enable/disable in `config.yaml`.

## Scheduled Messages

- **Morning Briefing** (9 AM) — Calendar, reminders, weather, priorities
- **Evening Wrap** (10 PM) — Tomorrow's schedule, suggested tasks, weather
- **Custom** — Add your own in `config.yaml`

## Customization

### System Prompt
Edit `personality.system_prompt` in `config.yaml` to change your bot's personality.

### Group Chat Behavior
- `group_mode: "friendly"` — Helpful to everyone
- `group_mode: "snarky"` — Roasts non-owners, helpful to you
- `group_mode: "ignore"` — Only responds to owner

### Adding Your Own Tools
Add tool definitions to `build_tools()` in `bot/app.py` and handlers to `execute_tool()`. The server endpoints are in `server/server.py`.

## Cost

| Service | Monthly Cost |
|---------|-------------|
| Anthropic API | ~$20-40 (depends on usage) |
| SendBlue | ~$6 |
| Render (Starter) | $7 |
| Tailscale | Free |
| **Total** | **~$33-53/mo** |

## File Structure

```
djangocli/
├── config.yaml              # Your configuration (generated by setup.sh)
├── config.example.yaml      # Template
├── setup.sh                 # Interactive setup wizard
├── bot/                     # iMessage bot (deploys to Render)
│   ├── app.py               # Main bot application
│   ├── requirements.txt
│   └── Procfile
├── server/                  # Mac Mini FastAPI server
│   ├── server.py            # Server application
│   ├── config.py            # Config loader
│   ├── pim-tool.swift       # Swift CLI for macOS PIM access
│   ├── compile-pim-tool.sh  # Compiles Swift binary
│   └── requirements.txt
└── README.md
```

## License

MIT
