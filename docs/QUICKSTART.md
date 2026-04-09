# DjangoCLI Quickstart

Your personal AI assistant over iMessage. This guide walks you through everything from zero to texting your bot.

**Time:** ~45 minutes  
**What you need:** A Mac (Mini recommended), an iPhone, a credit card for API signups

---

## Part 1: Get Your Accounts (15 min)

You need three accounts before touching any code. Open these in browser tabs and sign up.

### 1A. Anthropic (the AI brain)

1. Go to [console.anthropic.com](https://console.anthropic.com)
2. Create an account, add a payment method
3. Go to **Settings > API Keys** and create a new key
4. Copy it somewhere safe — you'll need it during setup

Cost: ~$20-40/month depending on how much you text your bot.

### 1B. SendBlue (the iMessage bridge)

SendBlue lets your bot send and receive iMessages without being on a Mac.

1. Go to [sendblue.co](https://sendblue.co) and create an account (~$6/month)
2. From the dashboard, grab your **API Key** and **Secret Key**
3. Note your **SendBlue phone number** — this is the number your bot texts from
4. Leave the webhook URL blank for now — you'll set it after deploying the bot

### 1C. Tailscale (the secure tunnel)

Tailscale creates a private network between your Mac Mini and your bot in the cloud. No ports to open, no firewall rules.

1. Go to [tailscale.com](https://tailscale.com) and create a free account
2. Install Tailscale on your Mac Mini:
   ```bash
   brew install --cask tailscale
   ```
3. Open Tailscale from Applications and sign in
4. Note your **Tailscale IP** — run this to find it:
   ```bash
   tailscale ip -4
   ```
   It'll look like `100.x.y.z`. This is how your bot reaches your Mac.

### 1D. Brave Search (optional — web search)

If you want your bot to search the web:

1. Go to [api.search.brave.com](https://api.search.brave.com)
2. Sign up, create an API key (free tier: 2,000 queries/month)

### 1E. Gmail App Password (optional — email access)

If you want your bot to check your email:

1. Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
2. You need 2-Factor Authentication enabled on your Google account first
3. Create an app password (name it "DjangoCLI" or whatever you want)
4. Copy the 16-character password — this is NOT your regular Gmail password

---

## Part 2: Set Up Your Mac Mini (15 min)

### 2A. Install prerequisites

Open Terminal on your Mac Mini and run:

```bash
# Install Homebrew (if you don't have it)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Install Xcode Command Line Tools (if you don't have them)
xcode-select --install

# Verify Python 3 is installed (macOS ships with it)
python3 --version
# Should say Python 3.10 or higher
```

### 2B. Clone and run setup

```bash
git clone https://github.com/YOUR_USERNAME/djangocli.git
cd djangocli
chmod +x setup.sh
./setup.sh
```

The setup wizard will ask for everything you gathered in Part 1. Here's what to enter at each prompt:

| Prompt | What to enter |
|--------|---------------|
| Your name | Your first name |
| Phone number | Your iPhone number with country code, e.g. `+15551234567` |
| Timezone | Your timezone, e.g. `America/New_York`, `America/Chicago`, `America/Los_Angeles` |
| Anthropic API key | The key from Step 1A |
| SendBlue API key | From Step 1B |
| SendBlue secret key | From Step 1B |
| SendBlue phone number | The number SendBlue assigned you (check your SendBlue dashboard) |
| Brave Search API key | From Step 1D, or press Enter to skip |
| Vault path | Where you want your Obsidian vault. Default `~/Desktop/BRAIN` is fine |
| Server bind address | **Use your Tailscale IP** from Step 1C (e.g. `100.x.y.z`). Do NOT use `0.0.0.0` |
| Gmail/Whoop | Enter if you set them up, skip if not |
| Bot name | Whatever you want to call your bot |

The wizard will:
- Generate your `config.yaml` with all settings
- Compile the pim-tool binary (gives your bot access to Calendar, Contacts, Reminders)
- Install Python dependencies
- Create a launchd service so the server auto-starts on boot

### 2C. Grant macOS permissions

macOS needs explicit permission for your bot to access Calendar, Contacts, and Reminders. Run each command and click **Allow** on the popup:

```bash
./server/pim-tool calendar today
./server/pim-tool reminders list
./server/pim-tool contacts search "test"
```

If you're logged in via SSH (headless Mac Mini), you may need to do this at the physical Mac or via Screen Sharing.

### 2D. Start and test the server

```bash
# Start the server (it'll auto-start on reboot too)
launchctl load ~/Library/LaunchAgents/com.djangocli.server.plist

# Test it — replace YOUR_API_KEY with the key from setup
curl -H "x-api-key: YOUR_API_KEY" http://localhost:8000/health
```

You should see `{"status": "ok"}`. If not, check the logs:

```bash
cat /tmp/djangocli-server.log
cat /tmp/djangocli-server.err
```

Common issues:
- **"Address already in use"** — another process is on port 8000. Run `lsof -i :8000` to find it.
- **Permission denied on calendar/contacts** — re-run the pim-tool commands from Step 2C.
- **Module not found** — the venv didn't install correctly. Run `cd server && source venv/bin/activate && pip install -r requirements.txt`.

### 2E. Test from Tailscale

From another device on your Tailscale network (or the Mac Mini itself):

```bash
curl -H "x-api-key: YOUR_API_KEY" http://100.x.y.z:8000/health
```

This confirms the bot will be able to reach your Mac Mini from the cloud.

---

## Part 3: Deploy the Bot to Render (10 min)

The bot runs in the cloud so it's always on (your Mac Mini handles the local stuff). Render is the easiest option.

### 3A. One-click deploy (recommended)

If the repo has a `render.yaml` file, you can use Render's Blueprint:

1. Push your repo to GitHub (if you haven't already)
2. Go to [render.com](https://render.com) and sign in
3. Click **New > Blueprint** and connect your GitHub repo
4. Render will detect `render.yaml` and set up the service
5. You'll be prompted to fill in the environment variables — use the values from your `config.yaml`

### 3B. Manual deploy

1. Go to [render.com](https://render.com) and sign in
2. Click **New > Web Service**
3. Connect your GitHub repo
4. Configure:
   - **Name:** `djangocli-bot` (or whatever)
   - **Root Directory:** `bot`
   - **Runtime:** Python 3
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --timeout 120`
   - **Plan:** Starter ($7/month) — Free tier sleeps after 15 min of inactivity, which breaks webhooks

5. Add these **Environment Variables** on Render:

| Variable | Value |
|----------|-------|
| `ANTHROPIC_API_KEY` | Your Anthropic key |
| `SENDBLUE_API_KEY` | Your SendBlue API key |
| `SENDBLUE_SECRET_KEY` | Your SendBlue secret |
| `SENDBLUE_FROM` | Your SendBlue phone number |
| `MY_PHONE_NUMBER` | Your iPhone number |
| `MAC_MINI_URL` | `http://100.x.y.z:8000` (your Tailscale IP) |
| `MAC_MINI_API_KEY` | The server API key from setup |
| `BRAVE_API_KEY` | Your Brave key (if you have one) |
| `TIMEZONE` | Your timezone |
| `OWNER_NAME` | Your name |
| `BOT_NAME` | Your bot's name |

6. Click **Deploy**

### 3C. Set the SendBlue webhook

Once your Render service is live:

1. Copy your Render URL (e.g. `https://djangocli-bot.onrender.com`)
2. Go to your [SendBlue dashboard](https://sendblue.co/dashboard)
3. Set the **Webhook URL** to: `https://djangocli-bot.onrender.com/webhook`

---

## Part 4: Test It (2 min)

Open iMessage on your iPhone and text your bot's SendBlue number:

```
Hey, what can you do?
```

Your bot should respond within a few seconds. If it works, try:

```
What's on my calendar today?
What's the weather?
Set a reminder to call mom at 5pm
Add milk to my grocery list
```

### Troubleshooting

**Bot doesn't respond at all:**
- Check Render logs: Dashboard > your service > Logs
- Verify SendBlue webhook URL is correct
- Make sure the Render service is running (not sleeping on free tier)

**Bot responds but can't access calendar/reminders/email:**
- The Mac Mini server might be down: `curl -H "x-api-key: KEY" http://100.x.y.z:8000/health`
- Check server logs: `cat /tmp/djangocli-server.log`
- Make sure Tailscale is connected on both sides

**"I can't access that right now":**
- The specific tool might be disabled in config.yaml
- The Mac Mini might not have permission (re-run pim-tool TCC commands)

---

## Part 5: Make It Yours

### Set up your vault

If you use Obsidian, the setup wizard created a starter vault for you. Open it in Obsidian:

1. Open Obsidian
2. **File > Open Vault > Open folder as vault**
3. Navigate to your vault path (default: `~/Desktop/BRAIN`)
4. Open `Context/personal-context.md` and fill it in — the more you give, the better your bot gets

### Customize the personality

Edit `config.yaml` and change the `personality` section:

```yaml
personality:
  name: "Jarvis"           # Change the name
  group_mode: "snarky"     # Roast your friends in group chats
```

### Enable/disable tools

```yaml
tools:
  whoop: true              # Turn on if you have a Whoop
  email: false             # Turn off email checking
```

### Change scheduled messages

```yaml
schedule:
  morning_briefing:
    enabled: true
    hour: 7                # Wake up early? Move the briefing.
    minute: 30
```

---

## Monthly Costs

| Service | Cost |
|---------|------|
| Anthropic API | ~$20-40 |
| SendBlue | ~$6 |
| Render (Starter) | $7 |
| Tailscale | Free |
| **Total** | **~$33-53/mo** |

---

## Updating

When new features land in the repo:

```bash
cd ~/djangocli
git pull
# If bot dependencies changed:
cd bot && source venv/bin/activate && pip install -r requirements.txt && deactivate && cd ..
# If server dependencies changed:
cd server && source venv/bin/activate && pip install -r requirements.txt && deactivate && cd ..
# Restart the server:
launchctl unload ~/Library/LaunchAgents/com.djangocli.server.plist
launchctl load ~/Library/LaunchAgents/com.djangocli.server.plist
```

The bot on Render auto-deploys from `main` if you connected it to GitHub.
