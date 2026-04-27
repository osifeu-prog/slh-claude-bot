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
ADMIN_ID = "224223270"  # 👈 זה המזהה הנכון שלך
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
🎉 <b>ברוך הבא ל-SLH Airdrop System!</b>

👤 <b>משתמש:</b> {name}
{'@' + username if username else ''}

💰 <b>מבצע השקה בלעדי:</b>
 1,000 טוקני SLH = 44.4 ₪ בלבד!
 קבלה אוטומטית תוך 24 שעות
 תמיכה טכנית 24/7

🚀 <b>התחלת תהליך:</b>
שלח לי את שם המשתמש הטלגרם שלך (לדוגמה: @username)
"""

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
# MAIN BOT LOOP
# ====================
def main():
    """לולאת הבוט הראשית"""
    bot = AirdropBot()
    offset = 0
    
    logger.info("=" * 50)
    logger.info("🤖 SLH Airdrop Bot - גרסה סופית")
    logger.info(f"👤 מנהל: {ADMIN_ID}")
    logger.info(f"🌐 API: {API_URL}")
    logger.info("=" * 50)
    
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
                        text = msg.get("text", "").strip()
                        name = msg["chat"].get("first_name", "משתמש")
                        username = msg["chat"].get("username", "")
                        
                        logger.info(f"📨 {name}: {text}")
                        
                        # פקודות מיוחדות
                        if text == "/start" or text.startswith("/start "):
                            # Phase 4: deep-link parser. /start therapist_<id>
                            # pairs the chat_id with an approved therapist app.
                            arg = text[len("/start"):].strip()
                            if arg.startswith("therapist_"):
                                try:
                                    app_id = int(arg.split("_", 1)[1])
                                    ok, msg = _link_therapist_telegram(app_id, chat_id)
                                    send_message(chat_id, msg)
                                    if ok and str(chat_id) != ADMIN_ID:
                                        send_message(
                                            ADMIN_ID,
                                            f"🩺 מטפל חיבר טלגרם:\n"
                                            f"app #{app_id} ↔ tg {chat_id} ({name})",
                                        )
                                    # don't fall through to airdrop handler
                                    continue
                                except (ValueError, IndexError):
                                    send_message(chat_id, "❌ קישור לא תקין. ודא שלחצת על הקישור המלא מהאתר.")
                                    continue
                            # Default: airdrop /start flow
                            bot.handle_start(chat_id, name, username)
                        
                        elif text == "/status":
                            bot.show_status(chat_id)
                        
                        elif text == "/help":
                            help_msg = """
❓ <b>עזרה - SLH Airdrop Bot</b>

<b>פקודות:</b>
/start - התחלת מערכת
/status - בדיקת סטטוס
/help - הצגת עזרה זו

<b>תהליך רכישה:</b>
1. שלח username טלגרם
2. שלח 44.4 TON לארנק שלנו
3. שלח את מספר העסקה
4. קבל 1,000 טוקני SLH

<b>תמיכה:</b> @Osif83
"""
                            send_message(chat_id, help_msg)
                        
                        elif text == "/admin":
                            if str(chat_id) == ADMIN_ID:
                                admin_panel = f"""
👑 <b>פאנל ניהול מנהל</b>

🌐 API: {API_URL}
📊 פאנל: {API_URL}/admin/dashboard?admin_key=airdrop_admin_2026
❤️  בריאות: {API_URL}/health

<b>פקודות:</b>
/status - סטטוס מערכת
/users - משתמשים רשומים
"""
                                send_message(chat_id, admin_panel)
                        
                        else:
                            # בדוק מצב נוכחי
                            state_data = bot.user_states.get(chat_id)
                            
                            if state_data:
                                state = state_data.get("state")
                                
                                if state == "awaiting_username":
                                    bot.handle_username(chat_id, text)
                                
                                elif state == "awaiting_payment":
                                    bot.handle_transaction(chat_id, text)
                                
                                else:
                                    # ברירת מחדל
                                    if text.startswith("/"):
                                        send_message(chat_id, "❓ <b>פקודה לא מוכרת.</b>\n\nלחץ /start להתחיל מחדש.")
                                    else:
                                        send_message(chat_id, "🤖 <b>הבוט מוכן!</b>\n\nלחץ /start להתחיל תהליך רכישה.")
                            else:
                                # אם לא במצב פעיל, התחל מחדש
                                if text and not text.startswith("/"):
                                    send_message(chat_id, "🤖 <b>ברוך הבא!</b>\n\nלחץ /start להתחיל תהליך רכישה.")
            
            time.sleep(1)
            
        except Exception as e:
            logger.error(f"🚨 שגיאה בלולאה ראשית: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
