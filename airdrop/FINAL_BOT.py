#!/usr/bin/env python3
"""
SLH Airdrop Bot - גרסה סופית מעודכנת
"""

import logging
import os
import requests
import time
import sys
import io
from datetime import datetime

# ====================
# CONFIGURATION
# ====================
# Token can be overridden via env (TELEGRAM_TOKEN or AIRDROP_BOT_TOKEN) so
# rotation doesn't require a code change. The hardcoded fallback below is
# the legacy token from before rotation and is currently REVOKED — set the
# fresh token via env.
TOKEN = (
    os.getenv("TELEGRAM_TOKEN")
    or os.getenv("AIRDROP_BOT_TOKEN")
    or "8530795944:AAFXDx-vWZPpiXTlfsv5izUayJ4OpLLq3Ls"
)
API_URL = "https://successful-fulfillment-production.up.railway.app"
# CSV of telegram_ids who get /admin access. Defaults preserve existing
# behavior + add Osif's secondary account 8789977826.
_admin_csv = os.getenv("ADMIN_IDS") or os.getenv("ADMIN_USER_ID") or "224223270,8789977826"
ADMIN_IDS = {x.strip() for x in _admin_csv.split(",") if x.strip()}
ADMIN_ID = next(iter(ADMIN_IDS))  # first one used for outgoing notifications
TON_WALLET = "UQCr743gEr_nqV_0SBkSp3CtYS_15R3LDLBvLmKeEv7XdGvp"

# Therapists Network deep-link (Phase 4): /start therapist_<id> in this bot
# pairs the Telegram account with an approved therapist application.
# Both env vars must be set; if absent, the deep-link silently falls through
# to the regular /start flow (no harm, just no link).
SLH_THERAPISTS_API = os.getenv(
    "SLH_THERAPISTS_API",
    "https://slh-api-production.up.railway.app/api/therapists/telegram/link",
)
TELEGRAM_LINK_SECRET = os.getenv("TELEGRAM_LINK_SECRET", "")

# ====================
# SETUP
# ====================
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

logging.basicConfig(
    format='%(asctime)s - SLH BOT - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# ====================
# HELPER FUNCTIONS
# ====================
def send_message(chat_id, text, parse_mode="HTML"):
    """שולח הודעה לטלגרם"""
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.status_code == 200
    except Exception as e:
        logger.error(f"Telegram error: {e}")
        return False

def call_api(endpoint, method="POST", data=None):
    """קורא ל-API עם Form data"""
    url = f"{API_URL}{endpoint}"
    
    try:
        if method == "POST":
            response = requests.post(url, data=data, timeout=10)
        else:
            return None
        
        if response.status_code in [200, 201]:
            return response.json()
        else:
            logger.error(f"API error {response.status_code}: {response.text}")
            return None
    except Exception as e:
        logger.error(f"API connection error: {e}")
        return None

# ====================
# MESSAGE TEMPLATES
# ====================
def get_welcome_message(name, username=""):
    return f"""
🌟 <b>ברוך הבא ל-SLH Spark!</b>

👤 {name}{(' (@' + username + ')') if username else ''}

🌐 <b>אתר הקהילה:</b>
https://slh-nft.com

📋 <b>פקודות זמינות:</b>
/me — הפרופיל שלך + יתרות
/dashboard — לוח בקרה אישי
/therapists — ספריית מטפלים
/bots — בוטי המערכת
/swarm — מצב Swarm + Brain
/help — רשימת פקודות מלאה
/buy — רכישת SLH (Genesis Pack)

💡 הבוט מקושר עם האתר ועם רשת המטפלים. כל פעולה כאן מסתנכרנת לחשבון שלך באתר.

⚠️ זהו פרויקט בשלב Pre-Launch. גילוי סיכון מלא: https://slh-nft.com/risk.html
"""

def get_help_message():
    return """
📖 <b>פקודות הבוט</b>

<b>חשבון:</b>
/me — פרופיל שלך + טוקנים + סטטוס מטפל
/dashboard — קישור ללוח בקרה אישי
/login — קישור התחברות מהיר לאתר

<b>קהילה:</b>
/therapists — ספריית מטפלים מאושרים
/courses — קורסים זמינים
/blog — בלוג יומי

<b>מערכת:</b>
/bots — רשימת בוטי SLH + סטטוס
/swarm — מצב Swarm + ESP devices + Brain

<b>תשלומים:</b>
/buy — רכישת Genesis Pack (1,000 SLH ב-44.4 TON)
/wallet — קישור לארנק SLH

<b>תמיכה:</b>
/help — מסך זה
/support — צור קשר עם הצוות
/admin — פאנל ניהול (לאדמין בלבד)

🌐 כל הפעולות מסתנכרנות עם https://slh-nft.com
"""

SLH_API_BASE = os.getenv("SLH_API_URL", "https://slh-api-production.up.railway.app")
BOT_SYNC_SECRET = os.getenv("BOT_SYNC_SECRET", "")


def slh_bot_sync(chat_id, name, username, referrer_id=None):
    """
    Upsert web_users row in SLH database (single source of truth) and
    obtain a JWT for one-tap login. Called on every /start so the bot
    and the website stay in lockstep — same telegram_id, same record.

    Returns dict with login_url + jwt + is_registered, or None on failure.
    Failure modes: BOT_SYNC_SECRET missing, network error, 403 from API.
    """
    if not BOT_SYNC_SECRET:
        logger.warning("BOT_SYNC_SECRET unset — skipping /api/auth/bot-sync")
        return None
    try:
        u = (username or "").lstrip("@")
        payload = {
            "telegram_id": int(chat_id),
            "username": u,
            "first_name": name or "",
            "photo_url": "",
            "referrer_id": referrer_id,
            "bot_secret": BOT_SYNC_SECRET,
        }
        r = requests.post(f"{SLH_API_BASE}/api/auth/bot-sync", json=payload, timeout=10)
        if r.status_code == 200:
            return r.json()
        logger.error(f"bot-sync HTTP {r.status_code}: {r.text[:200]}")
    except Exception as e:
        logger.error(f"bot-sync request failed: {e!r}")
    return None


def get_member_card(chat_id):
    """Fetch member card from SLH API for this telegram_id."""
    try:
        r = requests.get(f"{SLH_API_BASE}/api/member-card/{chat_id}", timeout=10)
        if r.status_code == 200:
            return r.json()
        if r.status_code == 404:
            return None
        logger.error(f"member-card {chat_id} HTTP {r.status_code}")
        return None
    except Exception as e:
        logger.error(f"member-card fetch failed: {e!r}")
        return None

def get_payment_instructions():
    return f"""
💸 <b>הוראות תשלום</b>

🏦 <b>ארנק TON שלנו:</b>
<code>{TON_WALLET}</code>

📋 <b>שלבי התשלום:</b>
1. שלח בדיוק <b>44.4 TON</b> לכתובת למעלה
2. שמור את מספר העסקה (Transaction Hash)
3. שלח את מספר העסקה לכאן
4. קבל אוטומטית 1,000 טוקני SLH

⚠️ <b>חשוב:</b>
 שלח בדיוק 44.4 TON
 זמן אספקה: עד 24 שעות

<b>שאלות?</b> @Osif83
"""

# ====================
# THERAPISTS DEEP-LINK (Phase 4)
# ====================
def _link_therapist_telegram(application_id: int, telegram_id: int) -> tuple[bool, str]:
    """
    Pair a Telegram chat_id with an approved therapist application_id by
    calling SLH API's /api/therapists/telegram/link.

    Returns (success, human_message). Idempotent server-side: replaying the
    same (telegram_id, application_id) returns ok with idempotent=true.
    """
    if not TELEGRAM_LINK_SECRET:
        logger.warning("TELEGRAM_LINK_SECRET not set — skipping therapist link")
        return False, (
            "⚠️ <b>חיבור הטלגרם זמנית לא זמין</b>\n\n"
            "אדמין SLH צריך לקבוע <code>TELEGRAM_LINK_SECRET</code> ב-Railway "
            "ובסביבת הבוט. נסה שוב בעוד מספר דקות."
        )
    try:
        resp = requests.post(
            SLH_THERAPISTS_API,
            headers={
                "Content-Type": "application/json",
                "X-Bot-Secret": TELEGRAM_LINK_SECRET,
            },
            json={
                "telegram_id": int(telegram_id),
                "application_id": int(application_id),
                "kind": "therapist",
            },
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("idempotent"):
                return True, (
                    f"✅ <b>כבר חובר</b>\n\n"
                    f"חשבון הטלגרם שלך כבר מקושר לאפליקציה #{application_id}."
                )
            return True, (
                f"✅ <b>חוברת בהצלחה!</b>\n\n"
                f"חשבון הטלגרם שלך מקושר עכשיו לאפליקציית מטפל #{application_id}.\n"
                f"מעכשיו תקבל כאן התראות על פגישות חדשות, אישורים ותשלומים."
            )
        if resp.status_code == 403:
            return False, "⚠️ <b>סוד בוט לא תקין</b> — פנה לתמיכה."
        if resp.status_code == 404:
            return False, (
                f"❌ <b>אפליקציה #{application_id} לא נמצאה</b>\n\n"
                "ייתכן שהאפליקציה נמחקה או שמספר האפליקציה שגוי. "
                "פנה לתמיכה: @osifeu_prog"
            )
        if resp.status_code == 400:
            return False, (
                "⏳ <b>האפליקציה עדיין לא אושרה</b>\n\n"
                "ברגע שצוות SLH יאשר את הבקשה — תוכל לחבר את הטלגרם שוב."
            )
        logger.error(f"Therapist link returned HTTP {resp.status_code}: {resp.text[:200]}")
        return False, f"❌ שגיאה (HTTP {resp.status_code})"
    except Exception as e:
        logger.error(f"Therapist link failed: {e!r}")
        return False, f"❌ שגיאת רשת: {e}"


# ====================
# BOT LOGIC
# ====================
class AirdropBot:
    def __init__(self):
        self.user_states = {}
    
    def handle_start(self, chat_id, name, username=""):
        """מטפל בפקודת /start"""
        logger.info(f"User {name} (@{username}) started bot")
        
        # רישום משתמש ב-API
        if username.startswith('@'):
            username = username[1:]
        
        user_data = {
            "telegram_id": str(chat_id),
            "username": username,
            "first_name": name
        }
        
        # נסה לרשום את המשתמש
        api_result = call_api("/api/register", "POST", user_data)
        
        # שלח הודעת ברוכים הבאים
        send_message(chat_id, get_welcome_message(name, username))
        
        # עדכן מצב משתמש
        self.user_states[chat_id] = {"state": "awaiting_username", "name": name}
        
        # התראה למנהל
        if api_result and api_result.get("status") in ["success", "exists"]:
            admin_msg = f"👤 משתמש חדש בבוט:\n{name} (@{username})\nID: {chat_id}"
            send_message(ADMIN_ID, admin_msg)
        
        return True
    
    def handle_username(self, chat_id, text):
        """מטפל בקבלת username"""
        state_data = self.user_states.get(chat_id)
        if not state_data:
            return False
        
        username = text.replace('@', '').strip()
        
        if len(username) < 3:
            send_message(chat_id, "❌ <b>שם משתמש לא תקין.</b>\n\nאנא שלח username תקין (לפחות 3 תווים).")
            return False
        
        # עדכן את ה-username ב-API
        user_data = {
            "telegram_id": str(chat_id),
            "username": username,
            "first_name": state_data["name"]
        }
        
        call_api("/api/register", "POST", user_data)
        
        # שלח הוראות תשלום
        send_message(chat_id, get_payment_instructions())
        
        # עדכן מצב
        self.user_states[chat_id] = {"state": "awaiting_payment", "name": state_data["name"], "username": username}
        
        return True
    
    def handle_transaction(self, chat_id, tx_hash):
        """מטפל בקבלת transaction hash"""
        state_data = self.user_states.get(chat_id)
        if not state_data:
            return False
        
        if len(tx_hash) < 30:
            send_message(chat_id, "❌ <b>מספר עסקה לא תקין.</b>\n\nאנא שלח את מספר העסקה המלא (לפחות 30 תווים).")
            return False
        
        # שמור את העסקה ב-API
        tx_data = {
            "telegram_id": str(chat_id),
            "transaction_hash": tx_hash,
            "amount": 44.4
        }
        
        result = call_api("/api/submit", "POST", tx_data)
        
        if result and result.get("status") == "success":
            # הודעה למשתמש
            success_msg = f"""
✅ <b>תשלום התקבל!</b>

👤 <b>משתמש:</b> {state_data['name']}
📝 <b>עסקה:</b> {tx_hash[:20]}...
💰 <b>סכום:</b> 44.4 TON
🎁 <b>טוקנים:</b> 1,000 SLH
⏳ <b>סטטוס:</b> ממתין לאישור מנהל
🕐 <b>זמן אספקה:</b> עד 24 שעות

📊 <b>למעקב:</b> שלח /status בכל עת
"""
            send_message(chat_id, success_msg)
            
            # התראה למנהל
            admin_msg = f"""
💰 <b>תשלום חדש!</b>

👤 משתמש: {state_data['name']}
📱 מזהה: {chat_id}
📝 עסקה: {tx_hash[:20]}...
💰 סכום: 44.4 TON
🕐 זמן: {datetime.now().strftime('%H:%M:%S')}

🌐 <b>פאנל ניהול:</b>
{API_URL}/admin/dashboard?admin_key=airdrop_admin_2026
"""
            send_message(ADMIN_ID, admin_msg)
            
            # עדכן מצב
            self.user_states[chat_id]["state"] = "completed"
            return True
        else:
            send_message(chat_id, "❌ <b>שגיאה בשמירת העסקה.</b>\n\nאנא נסה שוב או פנה לתמיכה: @Osif83")
            return False
    
    def show_status(self, chat_id):
        """מציג סטטוס משתמש"""
        try:
            # בדוק עם ה-API
            response = requests.get(f"{API_URL}/api/user/{chat_id}", timeout=10)
            
            if response.status_code == 200:
                result = response.json()
                if result.get("status") == "success":
                    user = result["user"]
                    transactions = result.get("transactions", [])
                    
                    status_msg = f"""
📊 <b>סטטוס אישי</b>

👤 <b>משתמש:</b> {user['first_name']}
🆔 <b>מזהה:</b> {chat_id}
💰 <b>טוקנים:</b> {user['tokens']:,} SLH
💸 <b>שווי משוער:</b> {user['tokens'] * 44.4 / 1000:,.1f} ₪

📝 <b>עסקאות אחרונות:</b>
"""
                    if transactions:
                        for tx in transactions[:3]:
                            status_msg += f" • {tx['status']}: {tx['amount']} TON ({tx['submitted_at'][:10]})\n"
                    else:
                        status_msg += "אין עסקאות עדיין"
                    
                    send_message(chat_id, status_msg)
                    return True
        
        except Exception as e:
            logger.error(f"Status error: {e}")
        
        # אם לא הצליח, שלח הודעה כללית
        send_message(chat_id, "📊 <b>עדיין לא רכשת טוקנים.</b>\n\nשלח username להתחלה!")
        return False

# ====================
# BOT REGISTRY HEARTBEAT
# ====================
def _heartbeat_loop():
    """Background thread: register this bot in /api/bots/list every 60s.
    Lets /admin/bot-registry.html show this bot as alive.
    """
    import threading, time as _time
    bot_secret = os.getenv("BOT_SYNC_SECRET", "")
    if not bot_secret:
        logger.info("[heartbeat] BOT_SYNC_SECRET not set — skipping registry heartbeat")
        return
    while True:
        try:
            r = requests.post(
                f"{SLH_API_BASE}/api/bots/heartbeat",
                json={
                    "bot_name": "slh-air-bot",
                    "display_name": "SLH Companion (@SLH_AIR_bot)",
                    "username": "SLH_AIR_bot",
                    "version": "2026.04.28",
                    "metadata": {
                        "container": "slh-airdrop",
                        "polling": True,
                        "admin_id": str(ADMIN_ID),
                    },
                },
                headers={"X-Bot-Secret": bot_secret},
                timeout=8,
            )
            if r.status_code != 200:
                logger.debug(f"[heartbeat] {r.status_code}: {r.text[:80]}")
        except Exception as e:
            logger.debug(f"[heartbeat] failed: {e}")
        _time.sleep(60)


def _start_heartbeat_thread():
    import threading
    t = threading.Thread(target=_heartbeat_loop, daemon=True, name="bot-registry-hb")
    t.start()
    logger.info("[heartbeat] registry thread started (60s interval)")


# ====================
# MAIN BOT LOOP
# ====================
def main():
    """לולאת הבוט הראשית"""
    bot = AirdropBot()
    offset = 0

    logger.info("=" * 50)
    logger.info("🤖 SLH Companion Bot — synced with slh-nft.com")
    logger.info(f"👤 ADMIN_ID: {ADMIN_ID}")
    logger.info(f"🌐 Airdrop API (legacy /buy): {API_URL}")
    logger.info(f"🩺 Therapists API: {SLH_THERAPISTS_API}")
    logger.info(f"🔐 TELEGRAM_LINK_SECRET set: {bool(TELEGRAM_LINK_SECRET)}")
    logger.info("=" * 50)

    _start_heartbeat_thread()
    
    while True:
        try:
            # קבל עדכונים מטלגרם
            url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"
            params = {"offset": offset, "timeout": 30}
            
            response = requests.get(url, params=params, timeout=35)
            data = response.json()
            
            if data.get("ok") and data.get("result"):
                for update in data["result"]:
                    offset = update["update_id"] + 1
                    
                    if "message" in update:
                        msg = update["message"]
                        chat_id = msg["chat"]["id"]
                        raw_text = msg.get("text", "").strip()
                        name = msg["chat"].get("first_name", "משתמש")
                        username = msg["chat"].get("username", "")

                        logger.info(f"📨 {name}: {raw_text!r}")

                        # If user pasted multiple commands at once (e.g. "/bots\n/swarm\n/help"),
                        # process the FIRST recognizable line and ignore the rest. This stops
                        # the whole pasted blob from being treated as one unknown command.
                        text = raw_text
                        if "\n" in raw_text:
                            for line in raw_text.split("\n"):
                                stripped = line.strip()
                                if stripped.startswith("/"):
                                    text = stripped
                                    logger.info(f"   → multi-line: handling first command {text!r}")
                                    break
                        # Strip trailing arguments for plain command match (keeps /start <arg> intact below)
                        if text.startswith("/") and " " in text and not text.startswith("/start "):
                            head = text.split(maxsplit=1)[0]
                            text = head
                        
                        # פקודות מיוחדות
                        if text == "/start" or text.startswith("/start "):
                            arg = text[len("/start"):].strip()
                            referrer_id = None
                            therapist_app_id = None

                            # Parse deep-link arg
                            if arg.startswith("therapist_"):
                                try:
                                    therapist_app_id = int(arg.split("_", 1)[1])
                                except (ValueError, IndexError):
                                    send_message(chat_id, "❌ קישור לא תקין. ודא שלחצת על הקישור המלא מהאתר.")
                                    continue
                            elif arg.startswith("ref_"):
                                try:
                                    referrer_id = int(arg.split("_", 1)[1])
                                except (ValueError, IndexError):
                                    referrer_id = None

                            # ALWAYS sync to SLH DB first — single source of truth.
                            # This creates/refreshes the web_users row and returns
                            # a one-tap login URL with JWT.
                            sync = slh_bot_sync(chat_id, name, username, referrer_id)

                            # Therapist deep-link handling (after sync so user_id is set)
                            if therapist_app_id is not None:
                                ok, link_msg = _link_therapist_telegram(therapist_app_id, chat_id)
                                send_message(chat_id, link_msg)
                                if ok and str(chat_id) not in ADMIN_IDS:
                                    try:
                                        send_message(ADMIN_ID,
                                            f"🩺 מטפל חיבר טלגרם:\n"
                                            f"app #{therapist_app_id} ↔ tg {chat_id} ({name})")
                                    except Exception:
                                        pass
                                continue

                            # Plain /start — show companion welcome + login URL
                            welcome = get_welcome_message(name, username)
                            if sync and sync.get("login_url"):
                                welcome += f"\n🔐 <b>קישור התחברות מיידי:</b>\n{sync['login_url']}\n"
                            send_message(chat_id, welcome)

                            if str(chat_id) not in ADMIN_IDS:
                                try:
                                    sync_status = "✅ סונכרן" if sync else "⚠️ sync נכשל"
                                    send_message(ADMIN_ID,
                                        f"👤 משתמש פתח את הבוט ({sync_status}):\n"
                                        f"{name} (@{username or '—'})\nID: {chat_id}")
                                except Exception:
                                    pass

                        elif text == "/help":
                            send_message(chat_id, get_help_message())

                        elif text == "/me":
                            resp = get_member_card(chat_id)
                            # API returns {"ok":true,"card":{...}} — unwrap
                            card = (resp or {}).get('card') if isinstance(resp, dict) else None
                            if not card:
                                send_message(chat_id,
                                    "❓ <b>לא נמצא חשבון</b>\n\n"
                                    "כנס לאתר https://slh-nft.com והתחבר עם טלגרם כדי לפתוח חשבון.\n\n"
                                    "אם כבר נרשמת — נסה /login לקישור התחברות מהיר.")
                            else:
                                name_ = card.get('name') or 'משתמש'
                                tier = card.get('tier', '—')
                                slh = card.get('slh_balance', 0)
                                zvk = card.get('zvk_balance', 0)
                                refs = card.get('referrals', 0)
                                rep = card.get('rep_score', 0)
                                nft = card.get('nft_number', '—')
                                is_th = '✅ כן' if card.get('is_therapist') else '—'
                                genesis = '✅' if card.get('genesis_contributor') else '—'
                                joined = card.get('joined') or '—'
                                send_message(chat_id, f"""
👤 <b>{name_}</b> · #{nft}

🆔 <code>{chat_id}</code>
🏆 רמה: <b>{tier}</b>
⭐ REP: {rep}
💎 SLH: <b>{slh}</b>
🪙 ZVK: <b>{zvk}</b>
👥 הפניות: {refs}
🩺 מטפל מאושר: {is_th}
🎟️ Genesis: {genesis}
📅 הצטרף: {joined}

🔗 <a href="https://slh-nft.com/dashboard.html?uid={chat_id}">לוח בקרה</a>
""")

                        elif text == "/dashboard":
                            send_message(chat_id, f"""
📊 <b>לוח בקרה אישי</b>

https://slh-nft.com/dashboard.html?uid={chat_id}

לחץ על הקישור כדי לראות יתרות, השקעות, פעילות והפניות.
""")

                        elif text == "/therapists":
                            send_message(chat_id, """
🩺 <b>רשת המטפלים של SLH</b>

📚 ספריית מטפלים מאושרים:
https://slh-nft.com/therapists.html

➕ הצטרף כמטפל:
https://slh-nft.com/for-therapists.html

📋 לוח בקרה למטפל מאושר:
https://slh-nft.com/dashboard-therapist.html
""")

                        elif text == "/courses":
                            send_message(chat_id, """
🎓 <b>קורסים</b>

https://slh-nft.com/academy/course-1-dynamic-yield.html

קורס חינמי שמסביר את מודל ה-Dynamic Yield של SLH Spark.
""")

                        elif text == "/blog":
                            send_message(chat_id, """
📰 <b>בלוג יומי</b>

https://slh-nft.com/blog.html

עדכון יומי של מה שקרה במערכת — שקוף ומלא.
""")

                        elif text == "/login":
                            send_message(chat_id, f"""
🔐 <b>קישור התחברות מהיר</b>

https://slh-nft.com/dashboard.html?uid={chat_id}

הקישור משלב את ה-Telegram ID שלך אוטומטית.
""")

                        elif text == "/wallet":
                            send_message(chat_id, f"""
💼 <b>הארנק שלך</b>

https://slh-nft.com/wallet.html?uid={chat_id}

חבר MetaMask / Trust Wallet כדי לראות יתרות BSC חיות.
""")

                        elif text == "/support":
                            send_message(chat_id, """
💬 <b>תמיכה</b>

🔵 צוות SLH Spark: @osifeu_prog
🐛 דווח באג: https://slh-nft.com/bug-report.html
📚 מדריכים: https://slh-nft.com/guides.html
""")

                        elif text == "/buy":
                            # Legacy airdrop flow — kept for users who explicitly opt in.
                            bot.handle_start(chat_id, name, username)

                        elif text == "/status":
                            bot.show_status(chat_id)

                        elif text == "/bots":
                            # Live bot fleet status — combines registry + static known list
                            try:
                                admin_key = os.getenv("ADMIN_API_KEY") or "slh_admin_2026_rotated_04_20"
                                r = requests.get(
                                    f"{SLH_API_BASE}/api/bots/list",
                                    headers={"X-Admin-Key": admin_key}, timeout=5)
                                live_count = len(r.json().get("bots", [])) if r.status_code == 200 else 0
                            except Exception:
                                live_count = 0
                            send_message(chat_id, f"""🤖 <b>SLH Bot Fleet</b> ({live_count} רשומים ב-DB)

<b>Public bots:</b>
🟢 @SLH_AIR_bot — main user bot (this one)
🟢 @SLH_Claude_bot — internal executor / dev tools
🟢 @SLH_macro_bot — macro economic alerts
🟢 @WEWORK_teamviwer_bot — Academia / payment flow
🟢 @G4meb0t_bot — gaming + dating (Phase 2)

<b>Admin / Internal:</b>
⚙️ @osifeu_prog (admin)
⚙️ +25 בוטים מקצועיים (Therapists, Guardian, Marketplace, etc)

📊 Bot Registry: https://slh-nft.com/admin/bot-registry.html
🤖 Add a bot: /api/bots/heartbeat (for bot devs)
""")

                        elif text == "/swarm":
                            # Live ESP32 swarm + brain summary
                            devices_total = devices_online = events_24h = "?"
                            brain_state = brain_score = "?"
                            brain_summary = ""
                            try:
                                r = requests.get(f"{SLH_API_BASE}/api/swarm/stats", timeout=5)
                                if r.status_code == 200:
                                    s = r.json()
                                    devices_total = s.get("total_devices", "?")
                                    devices_online = s.get("online", "?")
                                    events_24h = s.get("events_24h", "?")
                            except Exception:
                                pass
                            try:
                                r = requests.get(f"{SLH_API_BASE}/api/brain/state", timeout=5)
                                if r.status_code == 200:
                                    b = r.json()
                                    brain_state = b.get("system_state", "?")
                                    brain_score = b.get("health_score", "?")
                                    brain_summary = b.get("summary", "")[:140]
                            except Exception:
                                pass
                            state_emoji = "🟢" if brain_state == "HEALTHY" else ("🟠" if brain_state == "DEGRADED" else "🔴")
                            send_message(chat_id, f"""🌐 <b>SLH Swarm</b>

📡 <b>Devices:</b> {devices_online}/{devices_total} online · {events_24h} events (24h)

🧠 <b>Brain:</b> {state_emoji} {brain_state} · {brain_score}/100
<i>{brain_summary}</i>

🗺️ Neural Map: https://slh-nft.com/network.html
🎛️ Founder Panel: https://slh-nft.com/founder.html
📊 Swarm UI: https://slh-nft.com/swarm.html
""")

                        elif text == "/admin":
                            if str(chat_id) in ADMIN_IDS:
                                # Live system snapshot — pull stats from SLH API
                                health_emoji = "🟢"
                                pending = approved = "?"
                                try:
                                    h = requests.get(f"{SLH_API_BASE}/api/health", timeout=5).json()
                                    # /api/health may return either:
                                    #   {"status":"ok","db":"connected",...}
                                    #   {"status":"ok","db_connected":true,...}
                                    db_ok = (
                                        h.get("db_connected") is True
                                        or h.get("db") == "connected"
                                        or h.get("status") == "ok"
                                    )
                                    if not db_ok:
                                        health_emoji = "🔴"
                                except Exception:
                                    health_emoji = "🟡"
                                try:
                                    admin_key = os.getenv("ADMIN_API_KEY") or "slh_admin_2026_rotated_04_20"
                                    rp = requests.get(
                                        f"{SLH_API_BASE}/api/therapists/applications?status=pending&limit=1",
                                        headers={"X-Admin-Key": admin_key}, timeout=5)
                                    if rp.status_code == 200:
                                        pending = rp.json().get("total", "?")
                                    ra = requests.get(
                                        f"{SLH_API_BASE}/api/therapists/applications?status=approved&limit=1",
                                        headers={"X-Admin-Key": admin_key}, timeout=5)
                                    if ra.status_code == 200:
                                        approved = ra.json().get("total", "?")
                                except Exception:
                                    pass
                                send_message(chat_id, f"""
👑 <b>פאנל ניהול</b>

{health_emoji} SLH API health: <a href="{SLH_API_BASE}/api/health">בדוק</a>
🩺 מטפלים: {pending} ממתינים · {approved} מאושרים

<b>קישורים מהירים:</b>
📊 Mission Control: https://slh-nft.com/admin/mission-control.html
🩺 Therapists Admin: https://slh-nft.com/admin/therapists.html
📈 Reality: https://slh-nft.com/admin/reality.html
🔐 Secrets Vault: https://slh-nft.com/admin/secrets-vault.html
🤖 Bot Registry: https://slh-nft.com/admin/bot-registry.html

<b>פקודות נוספות:</b>
/status — סטטוס airdrop legacy
/users — משתמשים רשומים (legacy)
""")
                            else:
                                send_message(chat_id,
                                    f"⛔ <b>אין הרשאה</b>\n\n"
                                    f"פקודה זו זמינה רק למנהלים.\n"
                                    f"ה-Telegram ID שלך: <code>{chat_id}</code>")

                        else:
                            # State machine only for users mid-/buy flow
                            state_data = bot.user_states.get(chat_id)
                            if state_data:
                                state = state_data.get("state")
                                if state == "awaiting_username":
                                    bot.handle_username(chat_id, text)
                                    continue
                                elif state == "awaiting_payment":
                                    bot.handle_transaction(chat_id, text)
                                    continue
                            # No state + unknown command → friendly hint
                            if text.startswith("/"):
                                send_message(chat_id,
                                    "❓ <b>פקודה לא מוכרת</b>\n\n"
                                    "שלח /help לרשימת פקודות.")
                            else:
                                send_message(chat_id,
                                    "💡 שלח /help לרשימת פקודות, או /me לפרופיל שלך.")
            
            time.sleep(1)
            
        except Exception as e:
            logger.error(f"🚨 שגיאה בלולאה ראשית: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
