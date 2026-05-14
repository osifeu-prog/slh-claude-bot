# -*- coding: utf-8 -*-
"""
SLH CORE SYSTEM v53.2
Clean Admin Bot with Task Board
"""

import os
import logging
import asyncio
import datetime
from dotenv import load_dotenv
load_dotenv()

from aiogram.client.default import DefaultBotProperties
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from admin_handlers import cmd_status, cmd_system, cmd_logs, cmd_balance


logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise ValueError("BOT_TOKEN is not set")

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()


# ===================== TASK BOARD =====================
class TaskBoard:
    def __init__(self):
        self.tasks = {}
        self.next_id = 1

    def add_task(self, title: str, creator: str):
        tid = self.next_id
        self.tasks[tid] = {
            "id": tid,
            "title": title,
            "status": "open",
            "creator": creator,
            "assignee": None,
            "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "comments": []
        }
        self.next_id += 1
        return tid

    def get_all(self):
        if not self.tasks:
            return "📋 אין משימות פתוחות."
        lines = ["<b>📋 SLH Task Board</b>\n"]
        for t in sorted(self.tasks.values(), key=lambda x: x["id"]):
            emoji = "🟢" if t["status"] == "open" else "🔵" if t["status"] == "in_progress" else "✅"
            assignee = f" → @{t['assignee']}" if t["assignee"] else ""
            lines.append(f"{emoji} <b>#{t['id']}</b> {t['title']}{assignee}")
        return "\n".join(lines)

    def info(self, tid: int):
        t = self.tasks.get(tid)
        if not t:
            return "❌ משימה לא נמצאה."
        comments = "\n".join([f"▪ {c['time']} @{c['user']}: {c['text']}" for c in t["comments"]]) or "אין תגובות"
        return f"""
<b>📋 משימה #{t['id']}</b>
כותרת: {t['title']}
סטטוס: {t['status']}
יוצר: @{t['creator']}
אחראי: @{t.get('assignee') or '—'}
נוצרה: {t['created_at']}

💬 <b>תגובות:</b>
{comments}
""".strip()

    def pick(self, tid: int, user: str):
        t = self.tasks.get(tid)
        if not t: return "❌ משימה לא נמצאה"
        if t["status"] == "done": return "❌ המשימה כבר הושלמה"
        t["status"] = "in_progress"
        t["assignee"] = user
        return f"✅ משימה #{tid} נלקחה על ידי @{user}"

    def done(self, tid: int):
        t = self.tasks.get(tid)
        if not t: return "❌ משימה לא נמצאה"
        t["status"] = "done"
        return f"🎉 משימה #{tid} הושלמה!"

    def comment(self, tid: int, user: str, text: str):
        t = self.tasks.get(tid)
        if not t: return "❌ משימה לא נמצאה"
        t["comments"].append({"user": user, "text": text, "time": datetime.datetime.now().strftime("%H:%M")})
        return f"💬 תגובה נוספה למשימה #{tid}"


task_board = TaskBoard()


async def login_banner(message: types.Message):
    banner = f"""
╔══════════════════════════════════════════════════════════╗
║                🔐  SLH CORE SYSTEM v53.2  🔐            ║
║               SLH Spark — Intelligence Layer            ║
╚══════════════════════════════════════════════════════════╝

👤 User: {message.from_user.full_name}
🆔 ID: {message.from_user.id}
🎭 Role: viewer
🕒 {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
🔒 Status: AUTHORIZED
"""
    await message.answer(banner.strip())


# ===================== COMMANDS =====================
@dp.message(Command("start"))
async def cmd_start(m: types.Message):
    await login_banner(m)
    await m.answer("✅ <b>SLH v53.2 ready.</b> Send /help for commands.")


@dp.message(Command("help"))
async def cmd_help(m: types.Message):
    await m.answer("""
<b>SLH Commands</b>

/tasks — כל המשימות
/task_new כותרת — יצירת משימה
/task_info ID — פרטי משימה
/task_pick ID — לקיחת משימה
/task_done ID — סימון כהושלמה
/task_comment ID טקסט — הוספת תגובה

/status — Docker status
/my_access — ההרשאות שלי
""")


@dp.message(Command("tasks"))
async def cmd_tasks(m: types.Message):
    await m.answer(task_board.get_all())


@dp.message(Command("task_new"))
async def cmd_task_new(m: types.Message):
    title = m.text.replace("/task_new", "").strip()
    if not title:
        return await m.answer("❌ דוגמה: /task_new לתקן את ה-Docker networking")
    tid = task_board.add_task(title, m.from_user.username or "user")
    await m.answer(f"✅ משימה נוצרה!\nID: <code>#{tid}</code>")


@dp.message(Command("task_info"))
async def cmd_task_info(m: types.Message):
    try:
        tid = int(m.text.replace("/task_info", "").strip())
        await m.answer(task_board.info(tid))
    except:
        await m.answer("❌ שימוש: /task_info 5")


@dp.message(Command("task_pick"))
async def cmd_task_pick(m: types.Message):
    try:
        tid = int(m.text.replace("/task_pick", "").strip())
        await m.answer(task_board.pick(tid, m.from_user.username or "user"))
    except:
        await m.answer("❌ שימוש: /task_pick 5")


@dp.message(Command("task_done"))
async def cmd_task_done(m: types.Message):
    try:
        tid = int(m.text.replace("/task_done", "").strip())
        await m.answer(task_board.done(tid))
    except:
        await m.answer("❌ שימוש: /task_done 5")


@dp.message(Command("task_comment"))
async def cmd_task_comment(m: types.Message):
    try:
        parts = m.text.replace("/task_comment", "").strip().split(maxsplit=1)
        tid = int(parts[0])
        comment = parts[1] if len(parts) > 1 else ""
        if not comment:
            return await m.answer("❌ שימוש: /task_comment 5 כאן כותבים תגובה")
        await m.answer(task_board.comment(tid, m.from_user.username or "user", comment))
    except:
        await m.answer("❌ שימוש: /task_comment 5 הטקסט")


@dp.message(Command("status"))
async def cmd_status(m: types.Message):
    await m.answer("<b>🐳 Docker Status</b>\n🟢 מערכת פועלת")


@dp.message(Command("my_access"))
async def cmd_my_access(m: types.Message):
    await m.answer("👤 Role: <b>viewer</b>\nTeam: general")


# ===================== MAIN =====================

@dp.message(Command("status"))
async def status_handler(m: types.Message):
    await cmd_status(m)

@dp.message(Command("system"))
async def system_handler(m: types.Message):
    await cmd_system(m)

@dp.message(Command("logs"))
async def logs_handler(m: types.Message):
    await cmd_logs(m)

@dp.message(Command("balance"))
async def balance_handler(m: types.Message):
    await cmd_balance(m)

async def main():
    logging.info("🚀 SLH Admin Bot v53.2 started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

