#!/usr/bin/env python3
"""
DjangoCLI — Mac Mini API Server
Exposes vault, calendar, contacts, reminders, messages, email, Whoop, and weather
to the iMessage bot via Tailscale VPN (or local network).
"""

import os
import json
import subprocess
import sqlite3
import imaplib
import email
import time
import psutil
from collections import deque
from email.header import decode_header
import requests
from datetime import datetime, timedelta
from pathlib import Path
from fastapi import FastAPI, HTTPException, Header, Query, Request
from fastapi.responses import JSONResponse, HTMLResponse
from pydantic import BaseModel
from typing import Optional
import threading
import re as _re_mod
import uvicorn

app = FastAPI(title="DjangoCLI Server", version="1.0")

# ============================================================
# Request Logging (in-memory ring buffer)
# ============================================================
SERVER_START_TIME = datetime.now()
request_log = deque(maxlen=200)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    duration_ms = round((time.time() - start) * 1000)
    path = request.url.path
    if path not in ("/dashboard", "/api/status", "/api/logs", "/favicon.ico"):
        request_log.append({
            "timestamp": datetime.now().isoformat(),
            "method": request.method,
            "path": path,
            "status": response.status_code,
            "duration_ms": duration_ms,
        })
    return response


# ============================================================
# Configuration
# ============================================================
from config import *

_weather_cache = {"data": None, "fetched_at": None}


# ============================================================
# Auth Middleware
# ============================================================
def verify_key(x_api_key: str = Header(None)):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


# ============================================================
# Health Check
# ============================================================
@app.get("/health")
def health():
    return {
        "status": "ok",
        "version": "1.0",
        "product": "djangocli",
        "timestamp": datetime.now().isoformat()
    }


# ============================================================
# Vault Endpoints
# ============================================================
@app.get("/vault/read")
def vault_read(path: str = Query(...), x_api_key: str = Header(None)):
    verify_key(x_api_key)
    full_path = os.path.join(VAULT_PATH, path)
    if not os.path.isfile(full_path):
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    try:
        with open(full_path, "r", encoding="utf-8") as f:
            return {"path": path, "content": f.read()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/vault/search")
def vault_search(query: str = Query(...), x_api_key: str = Header(None)):
    verify_key(x_api_key)
    matches = []
    query_lower = query.lower()
    for root, dirs, files in os.walk(VAULT_PATH):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in files:
            if not f.endswith(".md"):
                continue
            rel_path = os.path.relpath(os.path.join(root, f), VAULT_PATH)
            if query_lower in f.lower():
                matches.append({"path": rel_path, "match": "filename"})
                continue
            try:
                with open(os.path.join(root, f), "r", encoding="utf-8") as fh:
                    content = fh.read()
                    if query_lower in content.lower():
                        idx = content.lower().index(query_lower)
                        start = max(0, idx - 100)
                        end = min(len(content), idx + 200)
                        matches.append({
                            "path": rel_path,
                            "match": "content",
                            "snippet": content[start:end]
                        })
            except Exception:
                pass
            if len(matches) >= 10:
                break
    return {"query": query, "matches": matches}


class VaultWriteRequest(BaseModel):
    path: str
    content: str


@app.post("/vault/write")
def vault_write(req: VaultWriteRequest, x_api_key: str = Header(None)):
    verify_key(x_api_key)
    full_path = os.path.join(VAULT_PATH, req.path)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    try:
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(req.content)
        return {"status": "ok", "path": req.path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/vault/append")
def vault_append(req: VaultWriteRequest, x_api_key: str = Header(None)):
    verify_key(x_api_key)
    full_path = os.path.join(VAULT_PATH, req.path)
    try:
        with open(full_path, "a", encoding="utf-8") as f:
            f.write(req.content)
        return {"status": "ok", "path": req.path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# PIM Tool (Calendar, Contacts, Reminders via Swift binary)
# ============================================================
PIM_TOOL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pim-tool")


def run_pim_tool(*args, timeout=30):
    """Run pim-tool Swift binary for Calendar/Reminders/Contacts access."""
    try:
        result = subprocess.run(
            [PIM_TOOL, *args],
            capture_output=True, text=True, timeout=timeout
        )
        if result.returncode != 0:
            error_msg = result.stderr.strip() or "Unknown error"
            if result.returncode == 3:
                raise HTTPException(status_code=404, detail=error_msg)
            elif result.returncode == 2:
                raise HTTPException(status_code=503, detail=error_msg)
            else:
                raise HTTPException(status_code=500, detail=error_msg)
        return json.loads(result.stdout)
    except HTTPException:
        raise
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="pim-tool timed out")
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail=f"pim-tool returned invalid JSON: {result.stdout[:200]}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"pim-tool error: {e}")


# ============================================================
# Calendar Endpoints
# ============================================================
@app.get("/calendar/today")
def calendar_today(x_api_key: str = Header(None)):
    verify_key(x_api_key)
    data = run_pim_tool("calendar", "today")
    events = []
    for e in data.get("events", []):
        events.append({
            "summary": e.get("summary", ""),
            "start": e.get("start_display", e.get("start", "")),
            "end": e.get("end_display", e.get("end", "")),
            "calendar": e.get("calendar", ""),
        })
    return {"date": "today", "events": events}


@app.get("/calendar/range")
def calendar_range(days: int = Query(7, ge=1, le=90), x_api_key: str = Header(None)):
    verify_key(x_api_key)
    data = run_pim_tool("calendar", "range", str(days))
    events = []
    for e in data.get("events", []):
        events.append({
            "summary": e.get("summary", ""),
            "start": e.get("start_display", e.get("start", "")),
            "end": e.get("end_display", e.get("end", "")),
            "calendar": e.get("calendar", ""),
        })
    return {"days": days, "events": events}


class CalendarEventRequest(BaseModel):
    summary: str
    start: str
    end: str
    calendar_name: Optional[str] = "Home"


@app.post("/calendar/create")
def calendar_create(req: CalendarEventRequest, x_api_key: str = Header(None)):
    verify_key(x_api_key)
    payload = json.dumps({
        "summary": req.summary, "start": req.start,
        "end": req.end, "calendar_name": req.calendar_name
    })
    return run_pim_tool("calendar", "create", payload)


class CalendarEditRequest(BaseModel):
    original_summary: str
    calendar_name: Optional[str] = "Home"
    new_summary: Optional[str] = None
    new_start: Optional[str] = None
    new_end: Optional[str] = None


@app.post("/calendar/edit")
def calendar_edit(req: CalendarEditRequest, x_api_key: str = Header(None)):
    verify_key(x_api_key)
    payload = {"original_summary": req.original_summary, "calendar_name": req.calendar_name}
    if req.new_summary:
        payload["new_summary"] = req.new_summary
    if req.new_start:
        payload["new_start"] = req.new_start
    if req.new_end:
        payload["new_end"] = req.new_end
    if len(payload) <= 2:
        raise HTTPException(status_code=400, detail="No changes specified")
    return run_pim_tool("calendar", "edit", json.dumps(payload))


class CalendarDeleteRequest(BaseModel):
    summary: str
    calendar_name: Optional[str] = None


@app.post("/calendar/delete")
def calendar_delete(req: CalendarDeleteRequest, x_api_key: str = Header(None)):
    verify_key(x_api_key)
    return run_pim_tool("calendar", "delete", req.summary)


# ============================================================
# Contacts Endpoints
# ============================================================
@app.get("/contacts/search")
def contacts_search(name: str = Query(...), x_api_key: str = Header(None)):
    verify_key(x_api_key)
    return run_pim_tool("contacts", "search", name)


@app.get("/contacts/reverse")
def contacts_reverse(phones: str = Query(...), x_api_key: str = Header(None)):
    verify_key(x_api_key)
    phone_list = [p.strip() for p in phones.split(",") if p.strip()]
    results = {}
    for phone in phone_list:
        try:
            data = run_pim_tool("contacts", "reverse", phone)
            results[phone] = data.get("name", phone)
        except Exception:
            results[phone] = phone
    return {"results": results}


# ============================================================
# Reminders Endpoints
# ============================================================
@app.get("/reminders")
def reminders_list(x_api_key: str = Header(None)):
    verify_key(x_api_key)
    data = run_pim_tool("reminders", "list")
    reminders = []
    for r in data.get("reminders", []):
        reminders.append({
            "name": r.get("name", ""),
            "list": r.get("list", ""),
            "due": r.get("due_display", r.get("due", "")),
        })
    return {"reminders": reminders}


class ReminderCreateRequest(BaseModel):
    name: str
    due: Optional[str] = None
    list_name: Optional[str] = "Reminders"


@app.post("/reminders/create")
def reminders_create(req: ReminderCreateRequest, x_api_key: str = Header(None)):
    verify_key(x_api_key)
    payload = {"name": req.name}
    if req.due:
        payload["due"] = req.due
    if req.list_name:
        payload["list_name"] = req.list_name
    return run_pim_tool("reminders", "create", json.dumps(payload))


class ReminderCompleteRequest(BaseModel):
    name: str


@app.post("/reminders/complete")
def reminders_complete(req: ReminderCompleteRequest, x_api_key: str = Header(None)):
    verify_key(x_api_key)
    return run_pim_tool("reminders", "complete", req.name)


# ============================================================
# Messages Endpoint
# ============================================================
@app.get("/messages/recent")
def messages_recent(contact: str = Query(None), limit: int = Query(10), x_api_key: str = Header(None)):
    verify_key(x_api_key)
    try:
        conn = sqlite3.connect(MESSAGES_DB)
        if contact:
            query = """
            SELECT m.text, m.is_from_me, datetime(m.date/1000000000 + 978307200, 'unixepoch', 'localtime') as dt
            FROM message m JOIN handle h ON m.handle_id = h.ROWID
            WHERE h.id LIKE ? AND m.text IS NOT NULL
            ORDER BY m.date DESC LIMIT ?"""
            rows = conn.execute(query, (f"%{contact}%", limit)).fetchall()
        else:
            query = """
            SELECT m.text, m.is_from_me, datetime(m.date/1000000000 + 978307200, 'unixepoch', 'localtime') as dt, h.id
            FROM message m JOIN handle h ON m.handle_id = h.ROWID
            WHERE m.text IS NOT NULL
            ORDER BY m.date DESC LIMIT ?"""
            rows = conn.execute(query, (limit,)).fetchall()
        conn.close()
        messages = []
        for row in rows:
            msg = {"text": row[0], "from_me": bool(row[1]), "date": row[2]}
            if len(row) > 3:
                msg["contact"] = row[3]
            messages.append(msg)
        return {"messages": messages}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# Whoop Endpoints
# ============================================================
@app.get("/whoop/recovery")
def whoop_recovery(limit: int = Query(3), x_api_key: str = Header(None)):
    verify_key(x_api_key)
    try:
        if not os.path.isfile(WHOOP_CREDENTIALS_PATH):
            raise HTTPException(status_code=404, detail="Whoop credentials not found")
        with open(WHOOP_CREDENTIALS_PATH) as f:
            creds = json.load(f)
        token = creds.get("access_token")
        if not token:
            raise HTTPException(status_code=401, detail="No access token")

        headers = {"Authorization": f"Bearer {token}"}
        r = requests.get(f"https://api.prod.whoop.com/developer/v2/recovery?limit={limit}", headers=headers, timeout=10)

        if r.status_code == 401:
            new_token = refresh_whoop_token(creds)
            if new_token:
                headers = {"Authorization": f"Bearer {new_token}"}
                r = requests.get(f"https://api.prod.whoop.com/developer/v2/recovery?limit={limit}", headers=headers, timeout=10)
            else:
                raise HTTPException(status_code=401, detail="Whoop token expired and refresh failed")

        if r.status_code == 200:
            return r.json()
        raise HTTPException(status_code=r.status_code, detail=r.text)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/whoop/cycle")
def whoop_cycle(limit: int = Query(3), x_api_key: str = Header(None)):
    verify_key(x_api_key)
    try:
        if not os.path.isfile(WHOOP_CREDENTIALS_PATH):
            raise HTTPException(status_code=404, detail="Whoop credentials not found")
        with open(WHOOP_CREDENTIALS_PATH) as f:
            creds = json.load(f)
        token = creds.get("access_token")
        headers = {"Authorization": f"Bearer {token}"}
        r = requests.get(f"https://api.prod.whoop.com/developer/v2/cycle?limit={limit}", headers=headers, timeout=10)

        if r.status_code == 401:
            new_token = refresh_whoop_token(creds)
            if new_token:
                headers = {"Authorization": f"Bearer {new_token}"}
                r = requests.get(f"https://api.prod.whoop.com/developer/v2/cycle?limit={limit}", headers=headers, timeout=10)

        if r.status_code == 200:
            return r.json()
        raise HTTPException(status_code=r.status_code, detail=r.text)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def refresh_whoop_token(creds):
    try:
        r = requests.post("https://api.prod.whoop.com/oauth/oauth2/token", data={
            "grant_type": "refresh_token",
            "refresh_token": creds.get("refresh_token"),
            "client_id": WHOOP_CLIENT_ID or creds.get("client_id"),
            "client_secret": WHOOP_CLIENT_SECRET or creds.get("client_secret"),
        }, timeout=10)
        if r.status_code == 200:
            data = r.json()
            new_token = data.get("access_token")
            if new_token:
                creds["access_token"] = new_token
                if data.get("refresh_token"):
                    creds["refresh_token"] = data["refresh_token"]
                with open(WHOOP_CREDENTIALS_PATH, "w") as f:
                    json.dump(creds, f, indent=2)
                return new_token
    except Exception as e:
        print(f"Whoop refresh error: {e}")
    return None


@app.get("/whoop/auth")
def whoop_auth():
    from urllib.parse import urlencode
    creds = {}
    if os.path.isfile(WHOOP_CREDENTIALS_PATH):
        with open(WHOOP_CREDENTIALS_PATH) as f:
            creds = json.load(f)
    params = {
        "client_id": WHOOP_CLIENT_ID or creds.get("client_id", ""),
        "response_type": "code",
        "scope": "read:recovery read:cycles read:workout read:sleep read:profile read:body_measurement offline",
        "redirect_uri": WHOOP_REDIRECT_URI,
        "state": "djangocli-auth",
    }
    from fastapi.responses import RedirectResponse
    return RedirectResponse(f"https://api.prod.whoop.com/oauth/oauth2/auth?{urlencode(params)}")


@app.get("/whoop/callback")
def whoop_callback(code: str = Query(None), error: str = Query(None)):
    if error:
        return {"error": error}
    if not code:
        return {"error": "no code received"}

    creds = {}
    if os.path.isfile(WHOOP_CREDENTIALS_PATH):
        with open(WHOOP_CREDENTIALS_PATH) as f:
            creds = json.load(f)

    r = requests.post("https://api.prod.whoop.com/oauth/oauth2/token", data={
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": WHOOP_REDIRECT_URI,
        "client_id": WHOOP_CLIENT_ID or creds.get("client_id"),
        "client_secret": WHOOP_CLIENT_SECRET or creds.get("client_secret"),
    }, timeout=15)

    if r.status_code == 200:
        data = r.json()
        creds["access_token"] = data["access_token"]
        if data.get("refresh_token"):
            creds["refresh_token"] = data["refresh_token"]
        os.makedirs(os.path.dirname(WHOOP_CREDENTIALS_PATH), exist_ok=True)
        with open(WHOOP_CREDENTIALS_PATH, "w") as f:
            json.dump(creds, f, indent=2)
        return HTMLResponse("<h1>Whoop authenticated!</h1><p>Token saved. You can close this tab.</p>")
    else:
        return {"error": r.status_code, "detail": r.text[:500]}


# ============================================================
# Weather Endpoint
# ============================================================
WMO_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Depositing rime fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    66: "Light freezing rain", 67: "Heavy freezing rain",
    71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
    77: "Snow grains", 80: "Slight rain showers", 81: "Moderate rain showers",
    82: "Violent rain showers", 85: "Slight snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail",
}


def fetch_weather(lat: float, lon: float):
    now = datetime.now()
    if (_weather_cache["data"]
            and _weather_cache["fetched_at"]
            and (now - _weather_cache["fetched_at"]).seconds < 1800):
        return _weather_cache["data"]

    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat, "longitude": lon,
        "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,relative_humidity_2m,precipitation",
        "hourly": "temperature_2m,weather_code,precipitation_probability",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,sunrise,sunset",
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
        "timezone": TIMEZONE,
        "forecast_days": 7,
    }
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    data = r.json()
    _weather_cache["data"] = data
    _weather_cache["fetched_at"] = now
    return data


@app.get("/weather")
def weather(lat: float = Query(None), lon: float = Query(None), x_api_key: str = Header(None)):
    verify_key(x_api_key)
    try:
        data = fetch_weather(lat or WEATHER_LAT, lon or WEATHER_LON)
        current = data.get("current", {})
        daily = data.get("daily", {})

        current_summary = {
            "temperature_f": current.get("temperature_2m"),
            "feels_like_f": current.get("apparent_temperature"),
            "condition": WMO_CODES.get(current.get("weather_code", -1), "Unknown"),
            "humidity_pct": current.get("relative_humidity_2m"),
            "wind_mph": current.get("wind_speed_10m"),
            "precipitation_in": current.get("precipitation"),
        }

        forecast = []
        if daily.get("time"):
            for i, date in enumerate(daily["time"]):
                forecast.append({
                    "date": date,
                    "high_f": daily["temperature_2m_max"][i],
                    "low_f": daily["temperature_2m_min"][i],
                    "condition": WMO_CODES.get(daily["weather_code"][i], "Unknown"),
                    "precip_chance_pct": daily["precipitation_probability_max"][i],
                    "precip_in": daily["precipitation_sum"][i],
                    "sunrise": daily["sunrise"][i],
                    "sunset": daily["sunset"][i],
                })

        return {
            "location": {"lat": lat or WEATHER_LAT, "lon": lon or WEATHER_LON},
            "current": current_summary,
            "forecast": forecast,
        }
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Weather API error: {e}")


# ============================================================
# Email Endpoints
# ============================================================
def decode_mime_header(header):
    if not header:
        return ""
    parts = decode_header(header)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="ignore"))
        else:
            decoded.append(part)
    return " ".join(decoded)


def get_email_body(msg):
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode("utf-8", errors="ignore")[:2000]
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            return payload.decode("utf-8", errors="ignore")[:2000]
    return ""


@app.get("/email/search")
def email_search(query: str = Query("is:unread"), limit: int = Query(10), x_api_key: str = Header(None)):
    verify_key(x_api_key)
    if not GMAIL_EMAIL or not GMAIL_APP_PASSWORD:
        raise HTTPException(status_code=503, detail="Gmail not configured")
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(GMAIL_EMAIL, GMAIL_APP_PASSWORD)
        mail.select("INBOX")

        imap_criteria = "ALL"
        q = query.lower().strip()
        if "is:unread" in q:
            imap_criteria = "UNSEEN"
        elif "is:starred" in q:
            imap_criteria = "FLAGGED"
        elif q.startswith("from:"):
            addr = q.replace("from:", "").strip()
            imap_criteria = f'(FROM "{addr}")'
        elif q.startswith("subject:"):
            subj = q.replace("subject:", "").strip()
            imap_criteria = f'(SUBJECT "{subj}")'
        else:
            imap_criteria = f'(OR (SUBJECT "{query}") (FROM "{query}"))'

        status, msg_ids = mail.search(None, imap_criteria)
        if status != "OK" or not msg_ids[0]:
            mail.logout()
            return {"query": query, "emails": []}

        ids = msg_ids[0].split()[-limit:]
        ids.reverse()

        emails = []
        for mid in ids:
            status, data = mail.fetch(mid, "(RFC822)")
            if status != "OK":
                continue
            msg = email.message_from_bytes(data[0][1])
            emails.append({
                "from": decode_mime_header(msg.get("From", "")),
                "subject": decode_mime_header(msg.get("Subject", "")),
                "date": msg.get("Date", ""),
                "snippet": get_email_body(msg)[:300]
            })
            # Mark as read after fetching
            mail.store(mid, '+FLAGS', '\\Seen')

        mail.logout()
        return {"query": query, "emails": emails}
    except Exception as e:
        return {"error": str(e), "query": query, "emails": []}


@app.get("/email/read")
def email_read(subject: str = Query(None), from_addr: str = Query(None), x_api_key: str = Header(None)):
    verify_key(x_api_key)
    if not GMAIL_EMAIL or not GMAIL_APP_PASSWORD:
        raise HTTPException(status_code=503, detail="Gmail not configured")
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(GMAIL_EMAIL, GMAIL_APP_PASSWORD)
        mail.select("INBOX")

        criteria = []
        if subject:
            criteria.append(f'(SUBJECT "{subject}")')
        if from_addr:
            criteria.append(f'(FROM "{from_addr}")')
        if not criteria:
            criteria.append("ALL")

        search_str = " ".join(criteria)
        status, msg_ids = mail.search(None, search_str)
        if status != "OK" or not msg_ids[0]:
            mail.logout()
            return {"error": "No matching email found"}

        latest_id = msg_ids[0].split()[-1]
        status, data = mail.fetch(latest_id, "(RFC822)")
        if status != "OK":
            mail.logout()
            return {"error": "Failed to fetch email"}

        msg = email.message_from_bytes(data[0][1])
        body = get_email_body(msg)
        mail.logout()
        return {
            "from": decode_mime_header(msg.get("From", "")),
            "subject": decode_mime_header(msg.get("Subject", "")),
            "date": msg.get("Date", ""),
            "body": body
        }
    except Exception as e:
        return {"error": str(e)}


# ============================================================
# Unified Context Endpoint
# ============================================================
@app.get("/context/now")
def context_now(x_api_key: str = Header(None)):
    verify_key(x_api_key)
    context = {}

    try:
        context["calendar"] = calendar_today(x_api_key=x_api_key)
    except Exception as e:
        context["calendar"] = {"error": str(e)}

    try:
        context["reminders"] = reminders_list(x_api_key=x_api_key)
    except Exception as e:
        context["reminders"] = {"error": str(e)}

    if GMAIL_EMAIL:
        try:
            context["email"] = email_search(query="is:unread", limit=10, x_api_key=x_api_key)
        except Exception as e:
            context["email"] = {"error": str(e)}

    if TOOLS_ENABLED.get("whoop"):
        try:
            context["whoop"] = whoop_recovery(limit=1, x_api_key=x_api_key)
        except Exception as e:
            context["whoop"] = {"error": str(e)}

    try:
        context["weather"] = weather(lat=None, lon=None, x_api_key=x_api_key)
    except Exception as e:
        context["weather"] = {"error": str(e)}

    return context


# ============================================================
# Status API
# ============================================================
@app.get("/api/status")
def api_status(x_api_key: str = Header(None)):
    verify_key(x_api_key)
    process = psutil.Process()

    def check_source(name, check_fn):
        try:
            check_fn()
            return {"name": name, "status": "ok"}
        except Exception as e:
            return {"name": name, "status": "error", "detail": str(e)[:200]}

    sources = [
        check_source("Vault", lambda: (
            assert_check(os.path.isdir(VAULT_PATH), "Vault directory not found"),
            assert_check(sum(1 for _ in Path(VAULT_PATH).rglob("*.md")) > 0, "No markdown files")
        )),
        check_source("Calendar", lambda: run_pim_tool("calendar", "today")),
        check_source("Contacts", lambda: run_pim_tool("contacts", "search", "test")),
        check_source("Reminders", lambda: run_pim_tool("reminders", "list")),
    ]

    if GMAIL_EMAIL:
        sources.append(check_source("Gmail", lambda: check_gmail_connection()))
    if TOOLS_ENABLED.get("whoop"):
        sources.append(check_source("Whoop", lambda: check_whoop_connection()))

    uptime_seconds = int((datetime.now() - SERVER_START_TIME).total_seconds())
    hours, remainder = divmod(uptime_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    return {
        "server": {
            "status": "ok",
            "version": "1.0",
            "product": "djangocli",
            "uptime": f"{hours}h {minutes}m {seconds}s",
            "uptime_seconds": uptime_seconds,
            "started": SERVER_START_TIME.isoformat(),
            "memory_mb": round(process.memory_info().rss / 1024 / 1024, 1),
        },
        "sources": sources,
        "recent_requests": len(request_log),
    }


def assert_check(condition, msg):
    if not condition:
        raise Exception(msg)


def check_gmail_connection():
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(GMAIL_EMAIL, GMAIL_APP_PASSWORD)
    mail.logout()


def check_whoop_connection():
    assert os.path.isfile(WHOOP_CREDENTIALS_PATH), "Credentials file not found"
    with open(WHOOP_CREDENTIALS_PATH) as f:
        creds = json.load(f)
    assert creds.get("access_token"), "No access token"


@app.get("/api/logs")
def api_logs(limit: int = Query(50), x_api_key: str = Header(None)):
    verify_key(x_api_key)
    logs = list(request_log)[-limit:]
    logs.reverse()
    return {"logs": logs, "total": len(request_log)}


# ============================================================
# Deep Research (Claude Code CLI)
# ============================================================
class ResearchRequest(BaseModel):
    query: str
    depth: str = "deep"  # "quick" or "deep"


_research_jobs = {}


def send_imessage_via_relay(message: str):
    """Send an iMessage through the bot's SendBlue integration."""
    if not RENDER_BOT_URL:
        print(f"[research] No RENDER_BOT_URL configured — cannot relay message")
        return
    try:
        r = requests.post(
            f"{RENDER_BOT_URL}/relay/send",
            headers={"x-api-key": RENDER_BOT_KEY, "Content-Type": "application/json"},
            json={"message": message},
            timeout=30,
        )
        return f"relay_{r.status_code}"
    except Exception as e:
        return f"relay_error: {e}"


def _clean_for_phone(text: str) -> str:
    """Strip markdown formatting and links for clean iMessage reading."""
    text = _re_mod.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    text = _re_mod.sub(r'https?://\S+', '', text)
    text = _re_mod.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    text = _re_mod.sub(r'\*([^*]+)\*', r'\1', text)
    text = _re_mod.sub(r'^#{1,4}\s+', '', text, flags=_re_mod.MULTILINE)
    text = _re_mod.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _run_research(job_id: str, query: str, depth: str):
    """Run Claude Code CLI in a background thread and text results back."""
    try:
        _research_jobs[job_id]["status"] = "running"

        if depth == "quick":
            research_prompt = f"""Research the following topic. Search the web for current information. Cross-check key claims against official/primary sources before including them.

Topic: {query}

Write a thorough but concise brief (under 500 words). Include source URLs at the end."""
            timeout = 180
        else:
            research_prompt = f"""You are a deep research agent. Conduct thorough research on the following topic.

Topic: {query}

Instructions:
1. Search the web extensively — use multiple searches to cross-reference
2. Look for PRIMARY and OFFICIAL sources (government sites, official orgs) first
3. Cross-check every factual claim against at least 2 sources
4. If sources conflict, note the conflict — don't just pick one
5. Identify key players, trends, numbers, and actionable insights

Write a comprehensive research brief (800-1500 words). Include all source URLs at the end."""
            timeout = 360

        env = {**os.environ, "CLAUDE_CODE_DISABLE_NONESSENTIAL": "1"}
        env["PATH"] = f"/opt/homebrew/bin:{env.get('PATH', '/usr/bin:/bin')}"
        env["HOME"] = os.path.expanduser("~")

        # Step 1: Research
        _research_jobs[job_id]["status"] = "researching"
        result = subprocess.run(
            ["/opt/homebrew/bin/claude", "--print", "-p", research_prompt,
             "--allowedTools", "WebSearch,WebFetch,Read,Grep,Glob"],
            capture_output=True, text=True, timeout=timeout,
            env=env
        )

        raw_output = result.stdout.strip()
        if not raw_output:
            raw_output = f"Research completed but no output. stderr: {result.stderr[:500]}"
            _research_jobs[job_id]["status"] = "error"
            _research_jobs[job_id]["error"] = raw_output
            send_imessage_via_relay(f"Research failed: {query}\n{raw_output[:500]}")
            return

        # Step 2: Self-verification
        _research_jobs[job_id]["status"] = "verifying"
        verify_prompt = f"""You are a fact-checker. Review this research output and verify every specific claim.

RESEARCH OUTPUT:
{raw_output[:6000]}

Instructions:
1. Identify every specific factual claim (numbers, dates, rules, requirements, names)
2. For each claim, search the web to verify it against official/primary sources
3. Flag anything that is wrong, outdated, misleading, or unsupported
4. Pay special attention to: legal/regulatory claims, statistics, prices, and any claim that inverts a meaning (e.g. saying "below X" when it should be "above X")

Output format — be brief:
CORRECTIONS (list each error and the correct info, or "None found" if all checks out)
CONFIDENCE: HIGH / MEDIUM / LOW (how confident you are in the research overall)"""

        verify_result = subprocess.run(
            ["/opt/homebrew/bin/claude", "--print", "-p", verify_prompt,
             "--allowedTools", "WebSearch,WebFetch"],
            capture_output=True, text=True, timeout=180,
            env=env
        )

        verification = verify_result.stdout.strip() if verify_result.stdout else "Verification step failed"

        # Step 3: Generate phone-friendly summary
        _research_jobs[job_id]["status"] = "formatting"
        phone_prompt = f"""Rewrite this research into a short, phone-friendly text message. Rules:
- Max 500 words
- No links, no URLs, no markdown, no bold, no headers, no tables
- Plain text only with line breaks between sections
- Use short paragraphs (2-3 sentences max)
- Lead with the bottom line / most important finding
- Use dashes (-) for lists, not bullets
- If the verification found errors, incorporate the corrections into the summary — don't repeat wrong info
- End with "Full report saved to vault" (nothing else)
- Do NOT include any preamble like "Here's the summary" or "Here's the text message" — just go straight into the content

RESEARCH:
{raw_output[:5000]}

VERIFICATION:
{verification[:2000]}"""

        phone_result = subprocess.run(
            ["/opt/homebrew/bin/claude", "--print", "-p", phone_prompt],
            capture_output=True, text=True, timeout=60,
            env=env
        )

        phone_text = phone_result.stdout.strip() if phone_result.stdout else _clean_for_phone(raw_output[:2000])

        _research_jobs[job_id]["status"] = "done"
        _research_jobs[job_id]["result"] = raw_output
        _research_jobs[job_id]["verification"] = verification

        # Save full report + verification to vault
        date_str = datetime.now().strftime("%Y-%m-%d")
        safe_title = query[:60].replace("/", "-").replace(":", "-")
        vault_path = f"Raw/research/{date_str}-{safe_title}.md"
        full_path = Path(VAULT_PATH) / vault_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(
            f"---\ntype: research\ncreated: {date_str}\nquery: \"{query}\"\ndepth: {depth}\n---\n\n# {query}\n\n{raw_output}\n\n---\n\n## Verification\n\n{verification}\n"
        )

        # Text the clean phone version
        phone_msg = f"Research: {query}\n\n{phone_text[:2800]}\n\nFull report saved to vault: {vault_path}"
        send_imessage_via_relay(phone_msg)

    except subprocess.TimeoutExpired:
        _research_jobs[job_id]["status"] = "timeout"
        send_imessage_via_relay(f"Research timed out: {query}\nTry a more specific query or use depth='quick'.")
    except Exception as e:
        _research_jobs[job_id]["status"] = "error"
        _research_jobs[job_id]["error"] = str(e)
        send_imessage_via_relay(f"Research failed: {query}\nError: {str(e)[:200]}")


@app.post("/research")
def start_research(req: ResearchRequest, x_api_key: str = Header(None)):
    """Kick off a deep research job using Claude Code CLI."""
    verify_key(x_api_key)
    job_id = f"research_{int(time.time())}"
    _research_jobs[job_id] = {"query": req.query, "depth": req.depth, "status": "queued"}

    thread = threading.Thread(target=_run_research, args=(job_id, req.query, req.depth), daemon=True)
    thread.start()

    return {"status": "started", "job_id": job_id, "message": f"Research started ({req.depth} mode). I'll text you when it's done."}


@app.get("/research/status")
def research_status(job_id: str = Query(None), x_api_key: str = Header(None)):
    """Check status of a research job."""
    verify_key(x_api_key)
    if job_id and job_id in _research_jobs:
        return _research_jobs[job_id]
    return {"jobs": {k: {"status": v["status"], "query": v["query"]} for k, v in _research_jobs.items()}}


# ============================================================
# Startup
# ============================================================
if __name__ == "__main__":
    print(f"DjangoCLI Server starting on {HOST}:{PORT}")
    print(f"Vault: {VAULT_PATH} ({'found' if os.path.isdir(VAULT_PATH) else 'NOT FOUND'})")
    print(f"PIM tool: {PIM_TOOL} ({'found' if os.path.isfile(PIM_TOOL) else 'NOT FOUND — run compile-pim-tool.sh'})")
    uvicorn.run(app, host=HOST, port=PORT)
