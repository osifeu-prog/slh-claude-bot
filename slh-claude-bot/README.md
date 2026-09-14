# @SLH_Claude_bot

Telegram-native executor bot for the SLH Spark ecosystem.
Uses Anthropic Claude API to answer Hebrew messages and execute real actions
on the SLH_ECOSYSTEM workspace: read/write files, run git, docker, curl.

## Quick start (local Docker)

```bash
# 1. Configure tokens
cp .env.example .env
# Edit .env: paste SLH_CLAUDE_BOT_TOKEN + ANTHROPIC_API_KEY

# 2. Build + run
docker build -t slh-claude-bot .
docker run -d --name slh-claude-bot \
  --env-file .env \
  -v "D:/SLH_ECOSYSTEM:/workspace" \
  slh-claude-bot

# 3. Open Telegram, message @SLH_Claude_bot
```

## Security model

- **Allowlist:** only `ADMIN_TELEGRAM_ID` may send messages
- **Scope lock:** all file/command tools refuse paths outside `/workspace`
- **Destructive gate:** `ALLOW_DESTRUCTIVE=false` blocks `rm`, `git push --force`, `reset --hard`
- **Secret masking:** `.env` reads are redacted in logs
- **Audit log:** every tool call appended to `audit.log`

## Tools exposed to Claude

| Tool | What it does |
|------|-------------|
| `read_file` | Read file from workspace |
| `write_file` | Write file to workspace |
| `list_dir` | List directory |
| `bash` | Run allowlisted shell command |
| `git` | git status / add / commit / push |
| `http_get` | HTTP GET (for API health checks) |

## Files

```
slh-claude-bot/
├── bot.py                 # aiogram entrypoint
├── claude_client.py       # Anthropic wrapper + tool registry
├── auth.py                # Telegram ID allowlist
├── session.py             # SQLite conversation memory
├── tools/
│   ├── filesystem.py
│   ├── git_ops.py
│   ├── bash_ops.py
│   └── http_ops.py
├── Dockerfile
├── requirements.txt
└── .env.example
```

<!-- redeploy trigger 2026-09-14 14:53 -->
