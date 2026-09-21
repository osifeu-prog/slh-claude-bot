"""Editor commands for @SLH_Claude_bot — full website-building control from Telegram.

This module defines slash commands that let Osif (admin allowlist) inspect, edit,
commit, and deploy files in the SLH_ECOSYSTEM workspace from Telegram.

Security:
- All commands gated by auth.is_authorized() at the bot.py handler level
- _safe_path() prevents path traversal outside the workspace
- Subprocess calls have 30s timeout
- AI drafts require explicit /apply confirmation (no silent writes)

Available commands (all from Telegram):
  Inspection:
    /cat <file>            — read file (masked secrets, 4000 chars)
    /ls [dir]              — list directory
    /grep <pat> [file]     — search in file/dir
    /find <pat> [dir]      — find files by name pattern

  Edit (direct):
    /append <file> <text>  — append a line
    /replace <file> :: <old> :: <new>   — find/replace (use :: as separator)
    /newpage <name>        — scaffold a new website/<name>.html from template

  Git:
    /commit <msg>          — git add -A + commit (uses Osif's name/email)
    /push                  — git push current branch (after commit)
    /sync <msg>            — commit + push in one step

  AI-assisted:
    /draft <file> <instruction>  — AI generates a diff plan; bot shows preview
    /apply                 — apply last draft + commit + push
    /reject                — discard last draft

Public surface: register(dp, bot, auth, _chunks, _escape_md) — call from bot.py
"""
from __future__ import annotations

import os
import re
import asyncio
import subprocess
import logging
import html
import json
import time
from pathlib import Path
from typing import Optional

import httpx

from aiogram import Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message

log = logging.getLogger("slh-claude-bot.editor")

WORKSPACE = Path(os.getenv("WORKSPACE", "/workspace")).resolve()
WEBSITE_DIR = WORKSPACE / "website"
API_BASE = os.getenv("SLH_API_BASE", "https://slh-api-production.up.railway.app")
GIT_AUTHOR_NAME = os.getenv("GIT_AUTHOR_NAME", "Osif Kaufman Ungar")
GIT_AUTHOR_EMAIL = os.getenv("GIT_AUTHOR_EMAIL", "osif.erez.ungar@gmail.com")

# In-memory store of pending AI drafts per chat_id
_DRAFTS: dict[int, dict] = {}


# ──────────────── Safety ────────────────

def _safe_path(rel: str) -> Optional[Path]:
    """Resolve a path inside WORKSPACE, return None if it escapes."""
    rel = (rel or "").strip().lstrip("/").lstrip("\\")
    if not rel:
        return None
    try:
        candidate = (WORKSPACE / rel).resolve()
    except (OSError, ValueError):
        return None
    if WORKSPACE not in candidate.parents and candidate != WORKSPACE:
        return None
    return candidate


def _mask(text: str) -> str:
    """Mask common secret patterns before sending to Telegram."""
    if not isinstance(text, str):
        return str(text)
    patterns = [
        (r"(sk-ant-[A-Za-z0-9\-_]{20,})", r"sk-ant-***REDACTED***"),
        (r"(\d{6,}:[A-Za-z0-9_\-]{30,})", r"***BOT_TOKEN_REDACTED***"),
        (r"(BOT_TOKEN\s*=\s*)([^\s\"']+)", r"\1***REDACTED***"),
        (r"(API_KEY\s*=\s*)([^\s\"']+)", r"\1***REDACTED***"),
        (r"(DB_PASSWORD\s*=\s*)([^\s\"']+)", r"\1***REDACTED***"),
        (r"(0x[a-fA-F0-9]{40})", r"\1"),  # public addr ok
    ]
    for pat, repl in patterns:
        text = re.sub(pat, repl, text)
    return text


def _md_escape(text: str) -> str:
    """Escape MarkdownV1 chars for Telegram."""
    if not isinstance(text, str):
        text = str(text)
    return text.replace("_", "\\_").replace("*", "\\*").replace("`", "\\`").replace("[", "\\[")


def _run(cmd: list[str] | str, cwd: Optional[Path] = None, timeout: int = 30) -> tuple[int, str]:
    """Run subprocess, return (rc, combined_output_truncated)."""
    try:
        result = subprocess.run(
            cmd,
            shell=isinstance(cmd, str),
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd) if cwd else str(WORKSPACE),
        )
        out = (result.stdout or "") + (result.stderr or "")
        return result.returncode, out[:3500]
    except subprocess.TimeoutExpired:
        return 124, f"⏱ command timed out after {timeout}s"
    except Exception as e:
        return 1, f"⚠️ {type(e).__name__}: {e}"


# ──────────────── Inspection commands ────────────────

async def _cmd_cat(msg: Message, parts: list[str], _chunks) -> None:
    if len(parts) < 2:
        await msg.answer("שימוש: `/cat \\<קובץ\\>`")
        return
    p = _safe_path(parts[1])
    if not p or not p.is_file():
        await msg.answer(f"קובץ לא נמצא: `{_md_escape(parts[1])}`")
        return
    try:
        content = p.read_text(encoding="utf-8", errors="replace")[:4000]
        content = _mask(content)
    except Exception as e:
        await msg.answer(f"שגיאה: `{_md_escape(str(e))}`")
        return
    rel = p.relative_to(WORKSPACE)
    header = f"📄 *{_md_escape(str(rel))}* \\({len(content)} chars\\)"
    body = f"```\n{content}\n```"
    for chunk in _chunks(header + "\n" + body):
        await msg.answer(chunk)


async def _cmd_ls(msg: Message, parts: list[str]) -> None:
    target = parts[1] if len(parts) >= 2 else "."
    p = _safe_path(target)
    if not p or not p.is_dir():
        await msg.answer(f"לא תיקייה: `{_md_escape(target)}`")
        return
    try:
        entries = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
    except Exception as e:
        await msg.answer(f"שגיאה: `{_md_escape(str(e))}`")
        return
    lines = [f"📁 *{_md_escape(str(p.relative_to(WORKSPACE)) or '.')}*"]
    for e in entries[:60]:
        icon = "📁" if e.is_dir() else "📄"
        lines.append(f"{icon} `{_md_escape(e.name)}`")
    if len(entries) > 60:
        lines.append(f"_\\+ {len(entries) - 60} נוספים_")
    await msg.answer("\n".join(lines))


async def _cmd_grep(msg: Message, parts: list[str]) -> None:
    if len(parts) < 2:
        await msg.answer("שימוש: `/grep \\<תבנית\\> \\[קובץ\\|תיקייה\\]`")
        return
    args = parts[1].strip().split(maxsplit=1)
    pattern = args[0]
    target = args[1] if len(args) >= 2 else "website"
    p = _safe_path(target)
    if not p or not p.exists():
        await msg.answer(f"לא נמצא: `{_md_escape(target)}`")
        return
    cmd = ["grep", "-rn", "-m", "20", "--include=*.html", "--include=*.js", "--include=*.css", "--include=*.md", pattern, str(p)]
    rc, out = _run(cmd, timeout=15)
    out = _mask(out)
    if not out.strip():
        await msg.answer(f"אין התאמות ל\\-`{_md_escape(pattern)}` ב\\-`{_md_escape(target)}`")
        return
    await msg.answer(f"🔍 *grep* `{_md_escape(pattern)}` @ `{_md_escape(target)}`\n```\n{out[:3500]}\n```")


async def _cmd_find(msg: Message, parts: list[str]) -> None:
    if len(parts) < 2:
        await msg.answer("שימוש: `/find \\<דפוס\\> \\[תיקייה\\]`")
        return
    args = parts[1].strip().split(maxsplit=1)
    pattern = args[0]
    target = args[1] if len(args) >= 2 else "website"
    p = _safe_path(target)
    if not p or not p.exists():
        await msg.answer(f"לא נמצא: `{_md_escape(target)}`")
        return
    cmd = ["find", str(p), "-type", "f", "-name", pattern, "-not", "-path", "*/node_modules/*", "-not", "-path", "*/.git/*"]
    rc, out = _run(cmd, timeout=10)
    if not out.strip():
        await msg.answer(f"אין קבצים תואמים ל\\-`{_md_escape(pattern)}`")
        return
    files = out.strip().split("\n")[:30]
    rels = [str(Path(f).relative_to(WORKSPACE)) for f in files if f]
    await msg.answer(f"📂 נמצאו {len(rels)} קבצים:\n```\n" + "\n".join(rels) + "\n```")


# ──────────────── Edit commands ────────────────

async def _cmd_append(msg: Message, parts: list[str]) -> None:
    if len(parts) < 2:
        await msg.answer("שימוש: `/append \\<קובץ\\> \\<טקסט\\>`")
        return
    args = parts[1].strip().split(maxsplit=1)
    if len(args) < 2:
        await msg.answer("חסר טקסט להוספה")
        return
    p = _safe_path(args[0])
    if not p or not p.is_file():
        await msg.answer(f"קובץ לא קיים: `{_md_escape(args[0])}`")
        return
    text = args[1]
    try:
        with p.open("a", encoding="utf-8") as f:
            f.write(("\n" if not p.read_text(encoding="utf-8", errors="replace").endswith("\n") else "") + text + "\n")
    except Exception as e:
        await msg.answer(f"שגיאה: `{_md_escape(str(e))}`")
        return
    await msg.answer(f"✅ נוסף ל\\-`{_md_escape(str(p.relative_to(WORKSPACE)))}`:\n`{_md_escape(text[:200])}`")


async def _cmd_replace(msg: Message, parts: list[str]) -> None:
    if len(parts) < 2 or "::" not in parts[1]:
        await msg.answer("שימוש: `/replace \\<קובץ\\> :: \\<old\\> :: \\<new\\>`")
        return
    chunks = parts[1].split("::")
    if len(chunks) < 3:
        await msg.answer("חסר :: \\(צריך שני\\)")
        return
    file_part = chunks[0].strip()
    old = chunks[1].strip()
    new = "::".join(chunks[2:]).strip()
    p = _safe_path(file_part)
    if not p or not p.is_file():
        await msg.answer(f"קובץ לא קיים: `{_md_escape(file_part)}`")
        return
    try:
        content = p.read_text(encoding="utf-8")
        if old not in content:
            await msg.answer(f"לא נמצא טקסט: `{_md_escape(old[:80])}`")
            return
        count = content.count(old)
        new_content = content.replace(old, new)
        p.write_text(new_content, encoding="utf-8")
    except Exception as e:
        await msg.answer(f"שגיאה: `{_md_escape(str(e))}`")
        return
    await msg.answer(f"✅ הוחלף ב\\-`{_md_escape(file_part)}`: {count} מופעים")


async def _cmd_newpage(msg: Message, parts: list[str]) -> None:
    if len(parts) < 2:
        await msg.answer("שימוש: `/newpage \\<שם\\>` \\(ללא \\.html\\)")
        return
    name = parts[1].strip().split()[0]
    name = re.sub(r"[^a-z0-9_-]", "", name.lower())
    if not name:
        await msg.answer("שם לא חוקי \\(אנגלית בלבד\\)")
        return
    target = WEBSITE_DIR / f"{name}.html"
    if target.exists():
        await msg.answer(f"קיים: `{name}.html`")
        return
    template = f"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SLH · {name.title()}</title>
<link rel="icon" type="image/png" href="/favicon-32.png">
<link href="https://fonts.googleapis.com/css2?family=Heebo:wght@400;500;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/css/shared.css?v=20260424a">
<style>
  body{{font-family:'Heebo',system-ui,sans-serif;background:#0a0f1c;color:#e8f4ff;margin:0;line-height:1.7}}
  .wrap{{max-width:980px;margin:0 auto;padding:80px 20px}}
  h1{{font-size:32px;font-weight:900;background:linear-gradient(135deg,#00b4d8,#5ba3ff);-webkit-background-clip:text;background-clip:text;color:transparent}}
</style>
</head>
<body>
<div id="topnav-root"></div>

<main class="wrap">
  <h1>{name.title()}</h1>
  <p>תוכן חדש — נוצר דרך @SLH_Claude_bot</p>
</main>

<div id="bottomnav-root"></div>
<div id="footer-root"></div>

<script src="/js/translations.js?v=20260424a"></script>
<script src="/js/shared.js?v=20260424a"></script>
<script>initShared({{activePage:'{name}',showBottomNav:true}});</script>
</body>
</html>
"""
    try:
        target.write_text(template, encoding="utf-8")
    except Exception as e:
        await msg.answer(f"שגיאה: `{_md_escape(str(e))}`")
        return
    await msg.answer(
        f"✅ נוצר `website/{name}.html` \\({len(template)} chars\\)\n\n"
        f"לראות: `/cat website/{name}.html`\n"
        f"להוסיף לגיט: `/sync \"feat: add {name} page\"`\n"
        f"אחרי push יראה ב\\-https://slh\\-nft\\.com/{name}\\.html"
    )


# ──────────────── Git commands ────────────────

async def _cmd_commit(msg: Message, parts: list[str]) -> None:
    if len(parts) < 2:
        await msg.answer("שימוש: `/commit \\<הודעה\\>`")
        return
    commit_msg = parts[1].strip()
    cwd = WEBSITE_DIR if (WEBSITE_DIR / ".git").exists() else WORKSPACE

    rc1, status = _run(["git", "status", "--short"], cwd=cwd, timeout=10)
    if not status.strip():
        await msg.answer("אין שינויים לcommit")
        return

    rc2, _ = _run(["git", "add", "-A"], cwd=cwd, timeout=15)
    if rc2 != 0:
        await msg.answer(f"git add נכשל")
        return

    env_args = [
        "-c", f"user.name={GIT_AUTHOR_NAME}",
        "-c", f"user.email={GIT_AUTHOR_EMAIL}",
    ]
    rc3, out = _run(["git"] + env_args + ["commit", "-m", commit_msg], cwd=cwd, timeout=20)
    if rc3 != 0:
        await msg.answer(f"commit נכשל:\n```\n{out[-2000:]}\n```")
        return

    rc4, sha_line = _run(["git", "log", "-1", "--oneline"], cwd=cwd, timeout=5)
    await msg.answer(
        f"✅ commit הוצלח ב\\-`{cwd.name}`\n"
        f"```\n{sha_line.strip()[:300]}\n```\n"
        f"להעלות לגיט\\-האב: `/push`"
    )


async def _cmd_push(msg: Message, parts: list[str]) -> None:
    cwd = WEBSITE_DIR if (WEBSITE_DIR / ".git").exists() else WORKSPACE
    rc, out = _run(["git", "push"], cwd=cwd, timeout=60)
    if rc != 0:
        await msg.answer(f"push נכשל:\n```\n{_md_escape(out[-2000:])}\n```")
        return
    await msg.answer(f"✅ push הוצלח \\(`{cwd.name}`\\)\nGitHub Pages יעדכן בעוד 30\\-60 שניות.")


async def _cmd_sync(msg: Message, parts: list[str]) -> None:
    """Combined commit + push in one step."""
    if len(parts) < 2:
        await msg.answer("שימוש: `/sync \\<הודעה\\>`")
        return
    # Reuse commit logic
    await _cmd_commit(msg, parts)
    # Wait briefly then push
    await asyncio.sleep(1)
    await _cmd_push(msg, parts)


# ──────────────── AI-assisted draft flow ────────────────

async def _cmd_draft(msg: Message, parts: list[str]) -> None:
    """User: /draft <file> <instruction in Hebrew/English>
    Bot uses /api/ai/chat to generate a proposed change. Stores draft
    in _DRAFTS[chat_id]. User then runs /apply or /reject."""
    if len(parts) < 2:
        await msg.answer(
            "שימוש: `/draft \\<קובץ\\> \\<הוראה\\>`\n"
            "לדוגמה: `/draft website/voice\\.html שנה את הכותרת לMicroskick`"
        )
        return
    args = parts[1].strip().split(maxsplit=1)
    if len(args) < 2:
        await msg.answer("חסרה הוראה")
        return
    file_arg, instruction = args
    p = _safe_path(file_arg)
    if not p or not p.is_file():
        await msg.answer(f"קובץ לא קיים: `{_md_escape(file_arg)}`")
        return

    content = p.read_text(encoding="utf-8", errors="replace")
    if len(content) > 8000:
        await msg.answer(
            f"⚠️ הקובץ גדול \\({len(content)} chars\\) \\— תאר בדיוק *איזה חלק* לשנות, "
            "או השתמש ב\\-`/replace` לשינוי מדויק במקום AI draft\\."
        )
        return

    await msg.bot.send_chat_action(msg.chat.id, "typing")

    prompt = (
        "אתה עוזר עריכה לאתר. תקבל קובץ HTML/JS/CSS וההוראה לשינוי. "
        "תחזיר בדיוק את הזוג: OLD: (טקסט קיים מהקובץ) ו-NEW: (החלפה). "
        "ללא הסברים מסביב. ללא תגיות markdown. רק 2 בלוקים: OLD: ... NEW: ...\n\n"
        f"--- INSTRUCTION ---\n{instruction}\n\n"
        f"--- FILE: {file_arg} ---\n{content}\n--- END FILE ---"
    )
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{API_BASE}/api/ai/chat",
                json={"message": prompt, "user_id": "claude_bot_editor", "lang": "he"},
            )
            data = resp.json()
            reply = data.get("reply", "")
    except Exception as e:
        await msg.answer(f"AI נכשל: `{_md_escape(str(e))}`")
        return

    # Parse OLD: / NEW: blocks
    old_match = re.search(r"OLD:\s*(.+?)(?=NEW:|$)", reply, re.DOTALL)
    new_match = re.search(r"NEW:\s*(.+?)$", reply, re.DOTALL)
    if not old_match or not new_match:
        await msg.answer(
            f"AI לא החזיר OLD:/NEW: בפורמט תקין:\n```\n{reply[:800]}\n```\n"
            "נסה הוראה ספציפית יותר."
        )
        return

    old = old_match.group(1).strip().strip("`").strip()
    new = new_match.group(1).strip().strip("`").strip()

    if old not in content:
        await msg.answer(
            f"⚠️ ה-OLD שה-AI הציע לא נמצא בקובץ. אולי הוא המציא טקסט\\.\n"
            f"AI proposed OLD:\n```\n{old[:500]}\n```\n"
            "נסה הוראה מדויקת יותר \\(ציין את הטקסט המדויק\\)."
        )
        return

    _DRAFTS[msg.chat.id] = {
        "file": file_arg,
        "old": old,
        "new": new,
        "instruction": instruction,
        "ts": time.time(),
    }

    preview = (
        f"📋 *Draft מוכן ל\\-{_md_escape(file_arg)}:*\n\n"
        f"*OLD:*\n```\n{old[:600]}\n```\n\n"
        f"*NEW:*\n```\n{new[:600]}\n```\n\n"
        f"לאשר ולעלות: `/apply`\n"
        f"לבטל: `/reject`"
    )
    await msg.answer(preview)


async def _cmd_apply(msg: Message, parts: list[str]) -> None:
    draft = _DRAFTS.get(msg.chat.id)
    if not draft:
        await msg.answer("אין draft פתוח. הרץ `/draft` קודם.")
        return
    if time.time() - draft["ts"] > 600:
        await msg.answer("Draft פג תוקף \\(>10 דק'\\). הרץ `/draft` שוב.")
        _DRAFTS.pop(msg.chat.id, None)
        return

    p = _safe_path(draft["file"])
    if not p or not p.is_file():
        await msg.answer(f"קובץ נעלם: `{_md_escape(draft['file'])}`")
        _DRAFTS.pop(msg.chat.id, None)
        return

    content = p.read_text(encoding="utf-8")
    if draft["old"] not in content:
        await msg.answer("הקובץ השתנה מאז ה\\-draft. הרץ שוב\\.")
        _DRAFTS.pop(msg.chat.id, None)
        return

    new_content = content.replace(draft["old"], draft["new"], 1)
    p.write_text(new_content, encoding="utf-8")
    _DRAFTS.pop(msg.chat.id, None)

    await msg.answer(f"✅ הוחל\\. עכשיו commit\\+push:")
    # Auto commit + push
    fake_parts = ["/sync", f"edit({draft['file']}): {draft['instruction'][:60]}"]
    await _cmd_sync(msg, fake_parts)


async def _cmd_reject(msg: Message, parts: list[str]) -> None:
    draft = _DRAFTS.pop(msg.chat.id, None)
    if not draft:
        await msg.answer("אין draft פתוח\\.")
        return
    await msg.answer(f"❌ Draft ל\\-`{_md_escape(draft['file'])}` בוטל\\.")


# ──────────────── Help ────────────────

EDITOR_HELP = (
    "*🛠 פקודות עורך \\(שליטה מלאה באתר\\):*\n\n"
    "*Inspection:*\n"
    "`/cat <file>` \\- קרא קובץ\n"
    "`/ls [dir]` \\- רשימת תיקייה\n"
    "`/grep <pat> [target]` \\- חיפוש בתוכן\n"
    "`/find <pat> [dir]` \\- חיפוש שמות קבצים\n\n"
    "*Edit:*\n"
    "`/append <file> <text>` \\- הוסף שורה\n"
    "`/replace <file> :: <old> :: <new>` \\- החלף\n"
    "`/newpage <name>` \\- צור דף חדש\n\n"
    "*Git:*\n"
    "`/commit <msg>` \\- commit שינויים\n"
    "`/push` \\- push לגיט\\-האב\n"
    "`/sync <msg>` \\- commit \\+ push בבת אחת\n\n"
    "*AI\\-assisted:*\n"
    "`/draft <file> <instruction>` \\- AI מציע שינוי\n"
    "`/apply` \\- אשר \\+ commit \\+ push\n"
    "`/reject` \\- בטל draft\n\n"
    "*דוגמה מלאה:*\n"
    "1\\. `/draft website/voice\\.html שנה את הכותרת ל\\-Voice 2025`\n"
    "2\\. בדוק את הdiff\n"
    "3\\. `/apply` → אוטומטי עולה לפרודקשן"
)


# ──────────────── Public registration ────────────────

def register(dp: Dispatcher, auth_module, _chunks_fn) -> None:
    """Wire up all editor commands. Call from bot.py main()."""

    def _gate(handler):
        async def wrapper(msg: Message, *args, **kwargs):
            if not auth_module.is_authorized(msg.from_user.id):
                await msg.answer(auth_module.unauthorized_reply_he(msg.from_user.id))
                return
            return await handler(msg)
        return wrapper

    @dp.message(Command("cat"))
    @_gate
    async def h_cat(msg: Message):
        parts = (msg.text or "").split(maxsplit=1)
        await _cmd_cat(msg, parts, _chunks_fn)

    @dp.message(Command("ls"))
    @_gate
    async def h_ls(msg: Message):
        parts = (msg.text or "").split(maxsplit=1)
        await _cmd_ls(msg, parts)

    @dp.message(Command("grep"))
    @_gate
    async def h_grep(msg: Message):
        parts = (msg.text or "").split(maxsplit=1)
        await _cmd_grep(msg, parts)

    @dp.message(Command("find"))
    @_gate
    async def h_find(msg: Message):
        parts = (msg.text or "").split(maxsplit=1)
        await _cmd_find(msg, parts)

    @dp.message(Command("append"))
    @_gate
    async def h_append(msg: Message):
        parts = (msg.text or "").split(maxsplit=1)
        await _cmd_append(msg, parts)

    @dp.message(Command("replace"))
    @_gate
    async def h_replace(msg: Message):
        parts = (msg.text or "").split(maxsplit=1)
        await _cmd_replace(msg, parts)

    @dp.message(Command("newpage"))
    @_gate
    async def h_newpage(msg: Message):
        parts = (msg.text or "").split(maxsplit=1)
        await _cmd_newpage(msg, parts)

    @dp.message(Command("commit"))
    @_gate
    async def h_commit(msg: Message):
        parts = (msg.text or "").split(maxsplit=1)
        await _cmd_commit(msg, parts)

    @dp.message(Command("push"))
    @_gate
    async def h_push(msg: Message):
        parts = (msg.text or "").split(maxsplit=1)
        await _cmd_push(msg, parts)

    @dp.message(Command("sync"))
    @_gate
    async def h_sync(msg: Message):
        parts = (msg.text or "").split(maxsplit=1)
        await _cmd_sync(msg, parts)

    @dp.message(Command("draft"))
    @_gate
    async def h_draft(msg: Message):
        parts = (msg.text or "").split(maxsplit=1)
        await _cmd_draft(msg, parts)

    @dp.message(Command("apply"))
    @_gate
    async def h_apply(msg: Message):
        parts = (msg.text or "").split(maxsplit=1)
        await _cmd_apply(msg, parts)

    @dp.message(Command("reject"))
    @_gate
    async def h_reject(msg: Message):
        parts = (msg.text or "").split(maxsplit=1)
        await _cmd_reject(msg, parts)

    @dp.message(Command("editor"))
    @_gate
    async def h_editor(msg: Message):
        await msg.answer(EDITOR_HELP)

    log.info("editor_commands registered: cat ls grep find append replace newpage commit push sync draft apply reject editor")
