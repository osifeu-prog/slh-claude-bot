"""@SLH_Claude_bot — aiogram entrypoint.

100% FREE AI — Groq + Gemini, zero Anthropic, zero cost.
Guards with Telegram ID allowlist. Persists conversation per chat.
"""
import asyncio
import logging
import os
from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(HERE, ".env"))

import httpx
from aiogram import types, Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message, PreCheckoutQuery

import auth
import handlers
from admin_handlers import cmd_status, cmd_system, cmd_logs, cmd_balance
import session
import quota
import subscriptions
import admin_panel

# Payment flow kept for backwards compat but no longer promoted
try:
    import payment_flow
except Exception:
    payment_flow = None

import free_ai_client as ai_client
_AI_MODE = "Free Unlimited (Groq/Gemini)"


def _pick_ai_client(use_anthropic: bool = False):
    """Always returns the free client."""
    return ai_client, "free", "groq/gemini"

API_BASE = os.getenv("SLH_API_BASE", "https://slh-api-production.up.railway.app")
ADMIN_KEY = os.getenv("ADMIN_API_KEY", "")
TASK_BOARD_PATH = os.getenv(
    "TASK_BOARD_PATH",
    os.path.join(HERE, "..", "ops", "TASK_BOARD.md"),
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("slh-claude-bot")

TOKEN = os.getenv("SLH_CLAUDE_BOT_TOKEN")
if not TOKEN:
    raise SystemExit("SLH_CLAUDE_BOT_TOKEN not set. See .env.example.")

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
dp = Dispatcher()


# ---------- Cross-bot coordination (shared agents group) ----------
# Registers BEFORE the @dp.message handlers below so coord group messages
# don't get swallowed by the F.text catch-all on_text() handler. No-op when
# COORDINATION_GROUP_CHAT_ID is unset. See shared/coordination.py.
import sys as _sys
_WORKSPACE_ROOT = "/workspace" if os.path.isdir("/workspace/shared") else os.path.abspath(os.path.join(HERE, ".."))
if _WORKSPACE_ROOT not in _sys.path:
    _sys.path.insert(0, _WORKSPACE_ROOT)
try:
    from shared import coordination as _coord
    log.info("shared.coordination loaded; enabled=%s", _coord.is_enabled())
except Exception as _coord_err:
    _coord = None
    log.warning("shared.coordination not loadable: %s", _coord_err)


_BOT_USERNAME_FOR_COORD = os.getenv("SLH_CLAUDE_BOT_USERNAME", "SLH_Claude_bot").lstrip("@")


async def _coord_ping(msg) -> None:
    await msg.reply("pong")


async def _coord_health_handler(msg) -> None:
    try:
        await msg.reply(
            f"[OK] API: {h.get('status','?')} · DB: {h.get('db','?')} · v{h.get('version','?')}"
        )
    except Exception as e:
        await msg.reply(f"[X!] {type(e).__name__}: {e}")


async def _coord_who_handler(msg) -> None:
    me = await bot.get_me()
    await msg.reply(f"[i] @{me.username} (claude-bot) · AI={_AI_MODE}")


if _coord is not None:
    _coord.register_inbound(
        dp,
        _BOT_USERNAME_FOR_COORD,
        handlers={
            "ping": _coord_ping,
            "health": _coord_health_handler,
            "who": _coord_who_handler,
        },
    )


# Telegram messages are capped at 4096 chars; split long replies
def _chunks(text: str, size: int = 4000) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)] or [text]


@dp.message(Command("start"))
async def cmd_start(msg: Message) -> None:
    if not auth.is_authorized(msg.from_user.id):
        await msg.answer(auth.unauthorized_reply_he(msg.from_user.id))
        return
    await msg.answer(
        f"שלום 👋 אני SLH Spark AI\n"
        f"מצב: {_AI_MODE} — ללא הגבלה ✅\n\n"
        f"━━━ שיחה חופשית ━━━\n"
        f"כל טקסט → AI חינם (Groq/Gemini)\n\n"
        f"━━━ מערכת ━━━\n"
        f"/control  — סיכום מצב בשורה אחת\n"
        f"/health   — בריאות API + DB\n"
        f"/price    — מחירי SLH/MNH/ZVK\n"
        f"/devices  — מכשירים מחוברים\n"
        f"/credits  — סטטיסטיקת שימוש\n\n"
        f"━━━ Ops ━━━\n"
        f"/ps  /bots  /logs <name>  /git\n"
        f"/task <desc>  /clear  /ai_mode\n\n"
        f"━━━ עורך אתר ━━━\n"
        f"/ls  /cat  /grep  /find\n"
        f"/append  /replace  /newpage\n"
        f"/commit  /push  /sync\n\n"
        f"עזרה מלאה: /help",
        parse_mode=None,
    )


@dp.message(Command("help"))
async def cmd_help(msg: Message) -> None:
    if not auth.is_authorized(msg.from_user.id):
        await msg.answer(auth.unauthorized_reply_he(msg.from_user.id))
        return
    await msg.answer(
        "*🤖 פקודות Ops \\(מיידי, ללא AI\\):*\n"
        "`/ps` `/bots` `/logs <name>` `/git` `/health` `/price` `/devices` `/task` `/ai_mode`\n\n"
        "*🛠 פקודות עורך \\(שליטה באתר\\):*\n"
        "`/cat` `/ls` `/grep` `/find`\n"
        "`/append` `/replace` `/newpage`\n"
        "`/commit` `/push` `/sync`\n"
        "`/draft` `/apply` `/reject`\n"
        "פירוט מלא: `/editor`\n\n"
        f"*🧠 שיחה חופשית \\(AI: {_AI_MODE}\\):*\n"
        "כל טקסט אחר נענה דרך Groq חינם\\.\n\n"
        "*דוגמאות:*\n"
        "• `/ls website`\n"
        "• `/cat website/voice\\.html`\n"
        "• `/draft website/index\\.html שנה את הכותרת`\n"
        "• `/sync \"feat: my edit\"`"
    )


# ---------- Phase 1: direct API handlers (no Claude, no $ cost) ----------

async def _http_get_json(path: str, headers: dict | None = None) -> dict:
    """Thin httpx wrapper. Raises on non-2xx."""
    url = path if path.startswith("http") else API_BASE + path
    async with httpx.AsyncClient(timeout=8.0) as client:
        resp = await client.get(url, headers=headers or {})
        resp.raise_for_status()
        return resp.json()


def _escape_md(text: str) -> str:
    """Escape MarkdownV1 special chars inside values."""
    if not isinstance(text, str):
        text = str(text)
    # aiogram default is MARKDOWN (v1) — escape only `_*`[
    return (
        text.replace("_", "\\_")
        .replace("*", "\\*")
        .replace("`", "\\`")
        .replace("[", "\\[")
    )


@dp.message(Command("health"))
async def cmd_health(msg: Message) -> None:
    if not auth.is_authorized(msg.from_user.id):
        await msg.answer(auth.unauthorized_reply_he(msg.from_user.id))
        return
    try:
        h = await _http_get_json("/api/health")
        api_ok = h.get("status") == "ok" or h.get("api") == "ok"
        db = h.get("db") or (h.get("checks") or {}).get("db") or "unknown"
        lines = [
            f"*API:* {'חי ✓' if api_ok else 'כבוי ✗'}",
            f"*DB:* `{_escape_md(db)}`",
        ]
        if "version" in h:
            lines.append(f"*גרסה:* `{_escape_md(h['version'])}`")
        if "timestamp" in h:
            lines.append(f"*בדוק ב:* `{_escape_md(h['timestamp'])}`")
        await msg.answer("\n".join(lines))
    except httpx.HTTPStatusError as e:
        await msg.answer(f"ה-API החזיר {e.response.status_code}. כנראה down.")
    except Exception as e:
        log.exception("/health failed")
        await msg.answer(f"שגיאה: `{_escape_md(type(e).__name__)}: {_escape_md(str(e))}`")


@dp.message(Command("price"))
async def cmd_price(msg: Message) -> None:
    if not auth.is_authorized(msg.from_user.id):
        await msg.answer(auth.unauthorized_reply_he(msg.from_user.id))
        return
async def cmd_price(msg: Message) -> None:
    if not auth.is_authorized(msg.from_user.id):
        await msg.answer(auth.unauthorized_reply_he(msg.from_user.id))
        return
    try:
        p = await _http_get_json("/api/prices")
        prices = p.get("prices") or p
        if not isinstance(prices, dict) or not prices:
            await msg.answer("אין נתוני מחיר כרגע.")
            return
        lines = ["*מחירים \\(₪\\):*"]
        for token, value in prices.items():
            # /api/prices returns {ils, usd} objects; fall back to scalar if not
            if isinstance(value, dict):
                ils = value.get("ils") or value.get("price") or value.get("value")
            else:
                ils = value
            try:
                fmt = f"{float(ils):,.2f}"
            except (TypeError, ValueError):
                fmt = str(ils)
            lines.append(f"• *{_escape_md(token)}:* `{fmt}`")
        await msg.answer("\n".join(lines))
    except Exception as e:
        log.exception("/price failed")
        await msg.answer(f"שגיאה: `{_escape_md(str(e))}`")


@dp.message(Command("devices"))
async def cmd_devices(msg: Message) -> None:
    if not auth.is_authorized(msg.from_user.id):
        await msg.answer(auth.unauthorized_reply_he(msg.from_user.id))
        return
async def cmd_devices(msg: Message) -> None:
    if not auth.is_authorized(msg.from_user.id):
        await msg.answer(auth.unauthorized_reply_he(msg.from_user.id))
        return
    if not ADMIN_KEY:
        await msg.answer("חסר `ADMIN_API_KEY` ב-.env של הבוט.")
        return
    try:
        d = await _http_get_json(
            "/api/admin/devices/list", headers={"X-Admin-Key": ADMIN_KEY}
        )
        devices = d.get("devices") or d if isinstance(d, (list, dict)) else []
        if not devices:
            await msg.answer("אין מכשירים רשומים.")
            return
        lines = [f"*מכשירים \\({len(devices)}\\):*"]
        for dev in devices[:10]:
            dev_id = dev.get("device_id") or dev.get("id") or "?"
            last_seen = dev.get("last_seen_at") or dev.get("last_heartbeat") or "--"
            online = dev.get("online") or dev.get("is_online")
            mark = "🟢" if online else "⚫"
            lines.append(
                f"{mark} `{_escape_md(str(dev_id))}` · {_escape_md(str(last_seen))}"
            )
        if len(devices) > 10:
            lines.append(f"_\\+ {len(devices) - 10} נוספים_")
        await msg.answer("\n".join(lines))
    except httpx.HTTPStatusError as e:
        await msg.answer(f"admin API החזיר {e.response.status_code}.")
    except Exception as e:
        log.exception("/devices failed")
        await msg.answer(f"שגיאה: `{_escape_md(str(e))}`")


@dp.message(Command("control"))
async def cmd_control(msg: Message) -> None:
    """One-shot ops summary: API + DB + Gateway + Swarm + Marketplace + Events.

    This is THE 'where do I stand right now' command Osif's advisor asked for.
    No AI, no token cost, ~500ms response. Plain text, no markdown traps.
    """
    if not auth.is_authorized(msg.from_user.id):
        await msg.answer(auth.unauthorized_reply_he(msg.from_user.id))
        return

    sections = []
    timestamp = __import__("datetime").datetime.now().strftime("%H:%M:%S %d/%m")

    # 1. API health
    try:
        api_ok = h.get("status") == "ok"
        db_ok = h.get("db") == "connected"
        sections.append(
            f"🟢 API: ok · DB: {h.get('db','?')} · v{h.get('version','?')}"
            if api_ok and db_ok
            else f"🔴 API: {h}"
        )
    except Exception as e:
        sections.append(f"🔴 API: unreachable ({type(e).__name__})")

    # 2. Gateway
    try:
        g = await _http_get_json("/api/miniapp/health")
        if g.get("gateway_loaded"):
            tok = "✓" if g.get("primary_bot_token_set") else "⚠ TELEGRAM_BOT_TOKEN חסר"
            sections.append(f"🟢 Gateway: loaded · admins:{g.get('admin_ids_count')} · bot_token:{tok}")
        else:
            sections.append(f"🔴 Gateway: not loaded")
    except Exception as e:
        sections.append(f"⚪ Gateway: skip ({type(e).__name__})")

    # 3. Swarm
    try:
        s = await _http_get_json("/api/swarm/stats")
        sections.append(
            f"🐝 Swarm: {s.get('online',0)}/{s.get('total_devices',0)} online · "
            f"{s.get('events_24h',0)} events 24h · {s.get('pending_commands',0)} cmds pending"
        )
    except Exception as e:
        sections.append(f"⚪ Swarm: skip ({type(e).__name__})")

    # 4. Marketplace
    try:
        m = await _http_get_json("/api/marketplace/items?limit=100")
        items = [i for i in (m.get("items") or []) if i.get("status") == "approved"]
        sections.append(f"🛒 Marketplace: {len(items)} פריטים approved")
    except Exception as e:
        sections.append(f"⚪ Marketplace: skip")

    # 5. Recent events
    try:
        e = await _http_get_json("/api/events/public?limit=5")
        evts = e.get("events") or []
        if evts:
            recent = ", ".join(set(ev.get("type") or ev.get("event_type", "?") for ev in evts[:5]))
            sections.append(f"📡 Events 5 last: {recent}")
        else:
            sections.append("📡 Events: 0 (פיד פעילות ריק)")
    except Exception:
        sections.append(f"⚪ Events: skip")

    # 6. Your queue (4 user-action blockers)
    queue_items = []
    if 'g' in locals() and not g.get("primary_bot_token_set"):
        queue_items.append("• הגדר TELEGRAM_BOT_TOKEN ב-Railway")
    queue_items.append("• פייר ESP — שלח /devices לבדיקה")
    queue_items.append("• הגדר SMS_PROVIDER ב-Railway (Inforu)")
    queue_items.append("• BotFather: הגדר Mini App URL")

    sections.append("")
    sections.append("📋 התור שלך:")
    sections.extend(queue_items)
    sections.append("")
    sections.append(f"🏠 הבית: https://slh-nft.com/my.html")
    sections.append(f"⏱ נבדק: {timestamp}")

    await msg.answer("\n".join(sections), parse_mode=None)


@dp.message(Command("swarm"))
async def cmd_swarm(msg: Message) -> None:
    """Show SLH Swarm mesh status — total/online/events/pending + per-device list."""
    if not auth.is_authorized(msg.from_user.id):
        await msg.answer(auth.unauthorized_reply_he(msg.from_user.id))
        return
    try:
        stats = await _http_get_json("/api/swarm/stats")
        devices_resp = await _http_get_json("/api/swarm/devices?limit=20")
        devices = devices_resp.get("devices", [])

        lines = [
            "*🐝 רשת Swarm:*",
            f"• *סה״כ:* `{stats.get('total_devices', 0)}` · "
            f"*online:* `{stats.get('online', 0)}`",
            f"• *events 24h:* `{stats.get('events_24h', 0)}` · "
            f"*commands ממתינות:* `{stats.get('pending_commands', 0)}`",
        ]

        if devices:
            lines.append("\n*מכשירים:*")
            for d in devices[:10]:
                dev_id = d.get("device_id", "?")
                online = d.get("online", False)
                mark = "🟢" if online else "⚫"
                rssi = d.get("last_rssi")
                bat = d.get("last_battery_pct")
                tail_bits = []
                if rssi is not None:
                    tail_bits.append(f"RSSI {rssi}dBm")
                if bat is not None:
                    tail_bits.append(f"{bat}%")
                tail = " · ".join(tail_bits)
                lines.append(
                    f"{mark} `{_escape_md(str(dev_id))}`"
                    + (f" · {_escape_md(tail)}" if tail else "")
                )
            if len(devices) > 10:
                lines.append(f"_\\+ {len(devices) - 10} נוספים_")
        else:
            lines.append(
                "\n_אין מכשירים רשומים עדיין\\. כשתבעיר את ה-firmware עם תמיכת ESP-NOW, המכשירים יירשמו אוטומטית\\._"
            )

        await msg.answer("\n".join(lines))
    except Exception as e:
        log.exception("/swarm failed")
        await msg.answer(f"שגיאה: `{_escape_md(str(e))}`")


@dp.message(Command("task"))
async def cmd_task(msg: Message) -> None:
    if not auth.is_authorized(msg.from_user.id):
        await msg.answer(auth.unauthorized_reply_he(msg.from_user.id))
        return
    # Everything after the /task command word
    text = (msg.text or "").split(maxsplit=1)
    if len(text) < 2 or not text[1].strip():
        await msg.answer("שימוש: `/task \\<תיאור המשימה\\>`")
        return
    task_text = text[1].strip()
    try:
        import datetime

        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        line = f"- [ ] {task_text}  _(added {ts} via bot)_\n"
        os.makedirs(os.path.dirname(TASK_BOARD_PATH), exist_ok=True)
        with open(TASK_BOARD_PATH, "a", encoding="utf-8") as f:
            f.write(line)
        await msg.answer(
            f"נוסף ל\\-TASK\\_BOARD\\.md:\n`{_escape_md(task_text)}`"
        )
    except Exception as e:
        log.exception("/task failed")
        await msg.answer(f"שגיאה: `{_escape_md(str(e))}`")


@dp.message(Command("status"))
async def cmd_status(msg: Message) -> None:
    if not auth.is_authorized(msg.from_user.id):
        return
    try:
        import asyncpg as _pg
        conn = await _pg.connect(os.getenv("DATABASE_URL"))
        premium = await conn.fetchval("SELECT COUNT(*) FROM premium_users")
        balances = await conn.fetchval("SELECT COUNT(*) FROM token_balances")
        wallets = await conn.fetchval("SELECT COUNT(*) FROM wallets")
        web_users = await conn.fetchval("SELECT COUNT(*) FROM web_users")
        investors_verified = await conn.fetchval("SELECT COUNT(*) FROM launch_contributions WHERE status='verified'")
        investors_pending = await conn.fetchval("SELECT COUNT(*) FROM launch_contributions WHERE status='pending'")
        total_raised = await conn.fetchval("SELECT SUM(amount_usd) FROM launch_contributions WHERE status != 'cancelled'")
        await conn.close()
        lines = [
            "SLH Ecosystem Status",
            "",
            "DATABASE",
            f"Premium Users: {premium}",
            f"Token Balances: {balances}",
            f"Wallets: {wallets}",
            f"Web Users: {web_users}",
            "",
            "INVESTORS",
            f"Verified: {investors_verified}",
            f"Pending: {investors_pending}",
            f"Total Raised: usd{float(total_raised or 0):.2f}",
            "",
            "BOT: Railway OK",
        ]
        await msg.answer("\n".join(lines), parse_mode=None)
    except Exception as e:
        await msg.answer(f"Error {e}", parse_mode=None)


if __name__ == "__main__":
    asyncio.run(main())






