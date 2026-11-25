"""X DRAFTS: the moment something happens, write a tweet draft for YOUR account and notify you. You post it yourself.

Triggers (each can be switched off): a coin crosses $100k and passes the REAL checks, the sniper buys / sells, a narrative
META SHIFT, the hourly thesis. Text: Claude in your own voice (from your own past tweets, pasted in settings) when keyed,
else a template. Drafts never imitate anyone else, only use facts from the bot's data, and say so when the sniper holds
the coin ("I hold this"). Notifications: the dashboard (desktop notification + toast) and, optionally, your own Discord
webhook. Posting is one click to X's composer with the text filled in; nothing is ever posted automatically."""
from __future__ import annotations

import json
import re
import threading
import time

from core import http, llm
from core.db import get_db
from core.events import log

TRIGGERS = {"runner": "A coin crosses $100k and passes the REAL checks", "sniper": "The sniper buys or sells",
            "meta_shift": "A narrative META SHIFT", "thesis": "The hourly thesis"}
_lock = threading.Lock()


def ensure_table():
    get_db().x("""CREATE TABLE IF NOT EXISTS x_drafts (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, trigger TEXT, mint TEXT,
                  symbol TEXT, text TEXT, by TEXT, status TEXT, meta TEXT)""")


def settings() -> dict:
    s = {"enabled": True, "triggers": {k: True for k in TRIGGERS}, "voice": "", "discord_webhook": "", "max_per_hour": 12,
         "include_ca": True}
    saved = get_db().kv_get("draft_settings") or {}
    s.update({k: v for k, v in saved.items() if k in s})
    s["triggers"] = {**{k: True for k in TRIGGERS}, **(saved.get("triggers") or {})}
    return s


_HOOK = re.compile(r"^https://(?:discord\.com|discordapp\.com)/api/webhooks/\d+/[\w-]+$")


def save_settings(upd: dict) -> dict:
    cur = settings()
    if "enabled" in upd:
        cur["enabled"] = bool(upd["enabled"])
    if isinstance(upd.get("triggers"), dict):
        cur["triggers"].update({k: bool(v) for k, v in upd["triggers"].items() if k in TRIGGERS})
    if "voice" in upd:
        cur["voice"] = str(upd["voice"] or "")[:4000]
    if "discord_webhook" in upd:
        h = str(upd["discord_webhook"] or "").strip()
        if h and not _HOOK.match(h):
            raise ValueError("that isn't a Discord webhook URL (https://discord.com/api/webhooks/...)")
        cur["discord_webhook"] = h
    if "max_per_hour" in upd:
        cur["max_per_hour"] = max(1, min(60, int(upd["max_per_hour"])))
    if "include_ca" in upd:
        cur["include_ca"] = bool(upd["include_ca"])
    get_db().kv_set("draft_settings", cur)
    return cur


VOICE_SYSTEM = ("You draft tweets for the user's OWN X account about Solana memecoins. Write in the user's voice, using their "
                "sample tweets for tone only. Never impersonate or speak as anyone else, never invent facts, quotes, "
                "endorsements or numbers: use only the data given. If the data says the user holds the coin, say so "
                "plainly. Under 260 characters. No more than 2 hashtags. Output only the tweet.")


def _template(trigger: str, d: dict) -> str:
    sym = f"${d.get('symbol')}" if d.get("symbol") else ""
    hold = " (I hold this)" if d.get("holding") else ""
    if trigger == "runner":
        why = (d.get("why") or [""])[0]
        return f"{sym} just crossed $100k in {d.get('ttt', '?')} min and passes my checks{hold}. {why}".strip()
    if trigger == "sniper":
        if d.get("side") == "buy":
            return f"Paper-sniped {sym} at ${d.get('mcap', 0) / 1e3:,.0f}k. Evidence: {d.get('why', '')}. Let's see.{hold}"
        return f"Out of {sym} at {d.get('x', 0):.2f}x ({d.get('pnl', 0):+.2f} SOL on paper). {d.get('reason', '')}"
    if trigger == "meta_shift":
        return f"Meta shift in the trenches: {d.get('reason', '')}. Watching {d.get('leader', '')} closely."
    if trigger == "idea":
        return (f"{d.get('parent')} is running and the '{d.get('keyword')}' narrative is heating up. "
                f"Working on {d.get('name')} (${d.get('ticker')}): {d.get('pitch', '')}").strip()
    if trigger == "thesis":
        return f"Hourly read: {d.get('leader', 'no clear leader')} leads, {d.get('runners', 0)} runners in the last hour. {d.get('line', '')}".strip()
    return ""


def _llm(trigger: str, d: dict, voice: str) -> str | None:
    if not llm.available():
        return None
    p = f"Event: {TRIGGERS.get(trigger, trigger)}\nData (JSON): {json.dumps(d, default=str)[:3000]}\n"
    if voice.strip():
        p += f"\nThe user's own past tweets (tone reference only):\n{voice[:2500]}\n"
    p += "\nDraft one tweet."
    out = llm.ask(p, system=VOICE_SYSTEM, max_tokens=200)
    return out.strip().strip('"') if out else None


def _notify_discord(hook: str, draft: dict):
    try:
        http.post(hook, json={"content": f"**New X draft** ({draft['trigger']}):\n{draft['text']}\n<{intent_url(draft['text'])}>"[:1900]},
                  rate_key="discord_hook", per_min=20, retries=1)
    except Exception as e:
        log("THESIS", f"draft webhook failed: {str(e)[:80]}", "warn")


def intent_url(text: str) -> str:
    from urllib.parse import quote
    return "https://x.com/intent/post?text=" + quote(text)


def make(trigger: str, data: dict, key: str | None = None, manual: bool = False) -> dict | None:
    """Write a draft now (dedup by `key`), store it, notify. Returns the draft or None when skipped.
    manual=True (you clicked DRAFT) ignores the on/off switches and the hourly cap."""
    s = settings()
    if not manual and (not s["enabled"] or not s["triggers"].get(trigger)):
        return None
    ensure_table()
    db = get_db()
    with _lock:
        if key:
            done = set(db.kv_get("draft_keys", []) or [])
            if key in done:
                return None
        hour = db.q("SELECT COUNT(*) n FROM x_drafts WHERE ts > ?", (int(time.time()) - 3600,))[0]["n"]
        if hour >= s["max_per_hour"] and not manual:
            return None
        if key:
            db.kv_set("draft_keys", (list(done) + [key])[-2000:])
    text = _llm(trigger, data, s["voice"])
    by = "claude" if text else "template"
    text = text or _template(trigger, data)
    if s["include_ca"] and data.get("mint") and data["mint"] not in text and len(text) + len(data["mint"]) < 278:
        text += "\n" + data["mint"]
    text = text[:280]
    did = db.x("INSERT INTO x_drafts(ts,trigger,mint,symbol,text,by,status,meta) VALUES(?,?,?,?,?,?,?,?)",
               (int(time.time()), trigger, data.get("mint"), data.get("symbol"), text, by, "new", json.dumps(data, default=str)[:4000]))
    draft = {"id": did, "trigger": trigger, "text": text}
    log("THESIS", f"X draft #{did} ({trigger}) ready: {text[:60]}…")
    if s["discord_webhook"]:
        threading.Thread(target=_notify_discord, args=(s["discord_webhook"], draft), daemon=True).start()
    return draft


def make_async(trigger: str, data: dict, key: str | None = None):
    threading.Thread(target=lambda: _safe(trigger, data, key), daemon=True).start()


def _safe(trigger, data, key):
    try:
        make(trigger, data, key)
    except Exception as e:
        log("THESIS", f"draft failed: {e}", "warn")


def holding(mint: str) -> bool:
    try:
        return bool(get_db().q("SELECT 1 FROM sniper_positions WHERE mint=? AND status IN ('open','handoff')", (mint,)))
    except Exception:
        return False


# ---------------------------------------------------------------- triggers
def on_runners(runners: list[dict]):
    """After a scan: drafts for coins that crossed $100k in the last 30 min and pass the REAL checks."""
    now = time.time()
    for c in runners:
        if c.get("is_real") is True and c.get("ts_100k") and now - c["ts_100k"] < 1800:
            make_async("runner", {"mint": c["mint"], "symbol": c.get("symbol"), "name": c.get("name"), "ath": c.get("ath_mcap"),
                                  "ttt": round(c["time_to_100k_min"]) if c.get("time_to_100k_min") is not None else None,
                                  "why": c.get("why") or c.get("why_template"), "themes": c.get("themes"),
                                  "catalyst": c.get("primary_catalyst"), "holding": holding(c["mint"])}, key=f"runner:{c['mint']}")


def on_thesis(kind: str, out: dict):
    s = out.get("data") or {}
    shift = s.get("meta_shift") or {}
    if shift.get("shift"):
        make_async("meta_shift", {"reason": shift.get("reason"), "leader": s.get("leader")}, key=f"shift:{out.get('id')}")
    if kind == "hourly":
        make_async("thesis", {"leader": s.get("leader"), "runners": s.get("runners"), "line": (out.get("text") or "").split("\n")[0][:160]},
                   key=f"thesis:{out.get('id')}")


def list_drafts(after: int = 0, limit: int = 60) -> list[dict]:
    ensure_table()
    rows = get_db().q("SELECT * FROM x_drafts WHERE id > ? ORDER BY id DESC LIMIT ?", (after, limit))
    return [{**{k: r[k] for k in r.keys() if k != "meta"}, "intent": intent_url(r["text"])} for r in rows]


def set_status(did: int, status: str):
    ensure_table()
    get_db().x("UPDATE x_drafts SET status=? WHERE id=?", (status, did))
