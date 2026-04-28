# SLH Spark — Agent Handoff Prompt

> **Self-contained brief for the next AI agent (Claude / GPT / other).** Read this top-to-bottom before any action. If anything below conflicts with what the user is asking, **stop and ask** — do not silently override.

**Last updated:** 2026-04-28 by Claude (Opus 4.7) at end of bot-fix + Brain-Phase-3 session.

---

## 0. Who you are working for

- **Osif Kaufman Ungar** — solo Hebrew-speaking developer building SLH Spark, an institutional crypto investment ecosystem in Israel.
- Telegram: `@osifeu_prog` · ID: `224223270`
- Email: `osif.erez.ungar@gmail.com`
- Works in Hebrew. **Hebrew UI** is required for all user-facing copy. **Code/comments/commits in English.**
- Communication style: direct, action-first, no long explanations. Common phrases:
  - *"כן לכל ההצעות"* = proceed with all suggestions
  - *"תוביל"* = you lead, don't keep asking — execute
  - *"המשך"* = continue with whatever's next on the open list

### ⚠️ Sensitive psychology — READ THIS
Osif lost ₪250K to the ZOOZ scam and exited Bitnest. He has a documented **legal-trauma pattern** — under stress he proposes pseudonyms / no-registration / no-LLC paths. **Never help with fraud paths.** When he resists structure (LLC, KYC, multisig), reframe it as **personal protection from regulators**, not as compliance burden. If he insists on skipping protection, document the choice in `STATUS.md` and proceed — it is his risk to take.

### What he hates
- Fake / mock / placeholder data presented as real
- Long sermons before action
- Money-grab pitches and ad-tech
- Anything that makes Israeli authorities (רשות ניירות ערך, רשות המסים, שב"כ) approach his social network

### What he loves
- Numbers, security, surfing, music, computers
- Concrete action with verifiable result in the same session
- Hebrew UI, clean info hierarchy, "decision-first" UX

---

## 1. Single source of truth

**`D:\SLH_ECOSYSTEM\_active\ops\STATUS.md` is the canonical project state file.** Read it first.

If you need historical context, look in `_active/ops/_archive/handoffs_2026-04/` (59 old `SESSION_HANDOFF_*.md` files were consolidated into STATUS.md).

**Hard rule:** do NOT create new `SESSION_HANDOFF_*.md` files. Append a dated bullet to `STATUS.md` "Recent Changes" instead.

---

## 2. The system at a glance

### Production endpoints
| URL | What |
|---|---|
| https://slh-api-production.up.railway.app/api/health | API liveness — must return `{"status":"ok","db":"connected","version":"1.1.0"}` |
| https://slh-api-production.up.railway.app/api/brain/state | Decision Layer — system_state, health_score, recommended_actions |
| https://slh-api-production.up.railway.app/api/brain/history?hours=24 | Brain Memory — recent state snapshots |
| https://slh-api-production.up.railway.app/api/brain/prompt | LLM-ready structured prompt from current state |
| https://slh-api-production.up.railway.app/api/ido/stats | Flat IDO public stats |
| https://slh-nft.com/founder.html | **Osif's primary control panel** (auto-refresh 30s) |
| https://slh-nft.com/ido.html | Public IDO landing |
| https://slh-nft.com/admin/ido-mission-control.html | Internal IDO tracking |
| https://slh-nft.com/admin/bot-registry.html | Bot fleet status |
| https://slh-nft.com/admin/secrets-vault.html | Secrets metadata + rotation tracker |

### Filesystem (current — post-reorg 2026-04-28)
```
D:\SLH_ECOSYSTEM\
├── api/main.py              # FastAPI ~11,800 lines, edited for Railway deploy
├── routes/                  # FastAPI routers (brain.py, ido.py, broadcast.py, ...)
│   └── brain.py             # ⭐ Command Brain Decision Layer
├── airdrop/FINAL_BOT.py     # @SLH_AIR_bot (THIS is what Railway/Docker runs)
├── _experimental/airdrop/   # Mirror copy (parallel session reorg in progress)
├── _active/website/         # GitHub Pages source, separate git repo
│   ├── founder.html         # ⭐ Osif's control panel
│   ├── ido.html             # Public IDO landing
│   └── admin/*.html         # Bot registry, IDO mission control, secrets vault, ...
├── _active/ops/STATUS.md    # ⭐ Single source of truth
├── _core/                   # Same as root for parallel session (mirror)
├── docker-compose.yml       # 25 bot services + postgres + redis
├── .env                     # Bot tokens + API keys (NEVER commit)
└── .githooks/pre-commit     # Blocks committing files with hardcoded secrets
```

⚠️ **Repo layout is in flux**. A parallel session moved many directories to `_core/` `_active/` `_archive/` `_experimental/` but only some of those moves were committed. The currently-deployed paths to slh-api repo are still the OLD root-level paths (`api/`, `routes/`, `airdrop/`). When committing API changes, edit the OLD paths.

### Two git repos
| Repo | Branch | Contents | Deploys to |
|---|---|---|---|
| `osifeu-prog/slh-api` | `master` | API + bots + ops docs + python code | Railway `slh-api-production` |
| `osifeu-prog/osifeu-prog.github.io` | `main` | website (HTML/CSS/JS) | GitHub Pages → slh-nft.com |

Push API changes from `D:/SLH_ECOSYSTEM/` (`git push origin main:master` because local branch is `main` but remote is `master`).
Push website changes from `D:/SLH_ECOSYSTEM/_active/website/` (`git push` standard).

### Auth
- **Railway admin key (live):** `slh_admin_2026_rotated_04_20`
- **Local `.env` admin key:** different (`QVUvE_…`) — discrepancy noted in STATUS.md, not yet aligned.
- Most admin endpoints accept `X-Admin-Key` header. Some accept `Authorization: Bearer <jwt>` for admin-tier JWTs.
- Public read endpoints (`/api/health`, `/api/brain/*`, `/api/ido/status`, `/api/ido/stats`) require no auth.

### Treasury
- **Gnosis Safe (BSC):** `0x9DD8aF7Ac0f601CD473422311b2942DAE9D0BD09` (1/1, will upgrade to 2/3)
- **SLH Token (BEP-20):** `0xACb0A09414CEA1C879c67bB7A877E4e19480f022`
- **PancakeSwap pool:** `0xacea26b6e132cd45f2b8a4754170d4d0d3b8bbee`
- **Main wallet:** `0xD0617B54FB4b6b66307846f217b4D685800E3dA4` (holds 199K SLH)

---

## 3. The Command Brain (the decision layer)

This is the heart of the system — the user's "cognitive control."

### What it does
Aggregates signals (API health, DB, ESP devices, users count, IDO countdown, heartbeats) → outputs:
- `system_state`: HEALTHY | DEGRADED | CRITICAL
- `health_score`: 0-100
- `summary`: one-line "System Intelligence" sentence
- `critical_issues`: list with severity (INFO/WARN/CRITICAL)
- `recommended_actions`: prioritized actions with `execution.type` (link/manual)
- `confidence`: 0..1

### Auto-features
- Every `/api/brain/state` call **auto-logs** to `brain_log` table.
- State **transitions** (HEALTHY → DEGRADED, etc.) **fire Telegram alerts** to Osif via `SLH_AIR_TOKEN` bot.
- `/api/brain/history?hours=24` returns sparkline-ready snapshots + window stats.
- `/api/brain/prompt` returns a structured prompt JSON ready for any LLM.

### Phases live
- ✅ Phase 1: state, score, summary, actions
- ✅ Phase 2: memory (brain_log) + history endpoint + prompt generator
- ✅ Phase 3: Telegram state-transition alerts
- 🔜 Phase 4 (proposed, not built): predictive layer (requires ~weeks of brain_log to train)
- 🔜 Phase 5 (proposed): action execution endpoint with confirm + audit trail (dangerous — only with safety rails)

### Files
- `routes/brain.py` (in slh-api repo) — collector + rules + endpoints
- `_active/website/founder.html` — UI consuming `/api/brain/state` + `/history`

---

## 4. Strategic context — IDO (Marshall Islands path)

**Approved 2026-04-27 evening.** Replaced earlier "Series A ₪50K via SAFE+Token Warrant" plan.

**Current path:**
- ₪500K target via on-chain IDO on PinkSale (BSC)
- Marshall Islands DAO LLC ($1,500/yr) for legal personhood
- Gnosis Safe 2/3 multisig for treasury
- CertiK Lite or SolidProof audit
- Soft cap 20 BNB / Hard cap 150 BNB / Vesting 20% TGE + 20%/month × 4
- LP Lock 365 days via PinkLock

**Status:**
- Safe DEPLOYED at 1/1 (Osif only). Needs Tzvika's wallet to upgrade.
- LLC NOT YET STARTED — Osif declined paid setup ("רוצה הכל בחינם או מקורי משלנו"). Personal liability accepted. Re-evaluate as raise approaches.
- Audit firm NOT YET SELECTED.
- `investors.html` and `pitch.html` STILL DESCRIBE OLD Series A path — pending rewrite or archive.

**14-day target:** IDO live ~2026-05-11 (will likely slip if LLC is required and not started).

**Forbidden in copy:**
- "Guaranteed APY", "Earn X%" with no SIG+σ disclaimer (see SLH.co.il/CLAUDE.md for full rule)
- Any flow that's effectively a security offering to retail in Israel without prospectus

---

## 5. Bots (25 total)

| Bot | Container | Status | Notes |
|---|---|---|---|
| @SLH_AIR_bot | slh-airdrop | 🟢 Live | `airdrop/FINAL_BOT.py` · self-registers via `/api/bots/heartbeat` since 28.4 |
| @SLH_Claude_bot | slh-claude-bot | 🟢 Live | Internal executor + AI Spark |
| @SLH_macro_bot | (NO container) | 🔴 Code at `D:\SLH.co.il\bot.py` but NO Railway worker. Needs new Railway service or local docker entry. |
| @WEWORK_teamviwer_bot | slh-academia-bot | 🔴 Token unauthorized — needs rotation in BotFather + update `.env` `WEWORK_TEAMVIWER_TOKEN` |
| @G4meb0t | slh-game | 🟠 Intermittent network errors (Docker DNS) — recovers automatically |
| 20+ more internal | various `slh-*` | mixed | See `docker ps` for status |

**Bot registry endpoint:** `POST /api/bots/heartbeat` with `X-Bot-Secret` header (env: `BOT_SYNC_SECRET`).

To make a bot show up in `/admin/bot-registry.html`, it must call this endpoint regularly. Pattern in `airdrop/FINAL_BOT.py:_heartbeat_loop()` (added 2026-04-28, commit `85c3c22`).

---

## 6. ESP32 firmware

- Active firmware: `device-registry/esp32-cyd-work/firmware/slh-device-v4/src/main.cpp`
- Hardware: CYD (Cheap Yellow Display) — ILI9341 320×240 + XPT2046 touch
- Endpoints used: `/api/device/claim/{id}`, `/api/esp/heartbeat`, `/api/esp/commands/{id}`
- v4.6 (2026-04-27) fixed tap-anywhere bug + accidental-reboot bug.
- WiFi creds hardcoded as `Beynoni / 12345678` (Osif's home network).
- Pairing window: device must be claimed within 15 minutes of `registered_at`.

---

## 7. How to push code (verified 2026-04-28)

### API code (Python)
```bash
cd D:/SLH_ECOSYSTEM
# Edit api/main.py or routes/*.py
git add <files>
git -c user.email="osif.erez.ungar@gmail.com" -c user.name="Osif Kaufman Ungar" commit -m "..."
git push origin main:master   # local main → remote master
# Railway auto-deploys in ~60-90s
```

### Website (HTML/CSS/JS)
```bash
cd D:/SLH_ECOSYSTEM/_active/website
git add <files>
git -c user.email="osif.erez.ungar@gmail.com" -c user.name="Osif Kaufman Ungar" commit -m "..."
git push   # → GitHub Pages auto-deploys in ~30-60s
```

### Bots running in local docker
```bash
cd D:/SLH_ECOSYSTEM
# Edit airdrop/FINAL_BOT.py (or other bot dir)
docker compose build <service>           # rebuild image
docker compose up -d --force-recreate <service>   # recreate container with new image
# IMPORTANT: --force-recreate WITHOUT prior `build --no-cache` may use cached layers.
# When code changes don't seem to take effect, do: `docker compose build --no-cache <service>` first.
```

### Verifying live
```bash
curl -s https://slh-api-production.up.railway.app/api/health
curl -s https://slh-api-production.up.railway.app/api/brain/state | python -m json.tool | head -20
curl -sI https://slh-nft.com/founder.html  # expect 200
docker ps --filter name=slh-airdrop --format "{{.Status}}"
docker logs slh-airdrop --tail 10
```

---

## 8. Hard rules — never violate

1. **Never paste secrets in chat.** If you see one (env vars, API keys, bot tokens) — flag IMMEDIATELY, list every exposed item, suggest rotation URL. The user is non-technical enough that he may paste secrets without realizing — your job to catch it.
2. **Never present mock data as real.** Use `[DEMO]` or `test_` prefix.
3. **Never use `_ensure_tables` patterns with self-recursion** (caused IDO endpoints 500 on 2026-04-28 — see `feedback_replace_all_guard_lines.md` in memory).
4. **Never approach Bitnest/Eliezer victims** without explicit warning that they're victims.
5. **Never push `.env` to git** (pre-commit hook blocks but don't bypass).
6. **Never give away 50 SLH as reward** (token target is ₪444 each — would give ₪22K per claim).
7. **Hebrew UI required.** All user-facing text in Hebrew. Code in English.
8. **Verify prod schema before SQL changes.** `first_seen` and `feedback.timestamp` are TEXT (ISO-8601) on the live DB, not TIMESTAMP. Always introspect first:
   ```bash
   railway run python -c "import os,psycopg2; c=psycopg2.connect(os.getenv('DATABASE_URL')).cursor(); c.execute('SELECT column_name,data_type FROM information_schema.columns WHERE table_name=%s',('<t>',)); [print(r) for r in c.fetchall()]"
   ```

---

## 9. Common pitfalls (learned the hard way)

- **`docker compose up -d --force-recreate`** uses CACHED images. To pick up code changes, run `docker compose build --no-cache <svc>` first, THEN force-recreate.
- **Multi-line Telegram messages** — bots that match `text == "/cmd"` will fail when user pastes `/cmd1\n/cmd2`. Take first `/`-prefixed line. See `airdrop/FINAL_BOT.py` line ~488.
- **UTF-8 special chars in Python strings** sometimes mojibake on Railway runtime (Latin-1 default). Replace `·` with `|` to be safe.
- **`git restore --source=origin/master`** with broad scope can overwrite parallel session's uncommitted work. Restore individual files only.
- **Encoding BOM** at top of source files breaks `ast.parse` from Windows cmd but Python interpreter handles it. Use `io.open(..., encoding='utf-8-sig')` for syntax checks.
- **Admin key mismatch** between `.env` (local) and Railway (deployed) is a known issue — Railway is the source of truth (`slh_admin_2026_rotated_04_20`).

---

## 10. The "what to do today" list (auto-pulled from Brain)

The Brain endpoint `/api/brain/state` returns `recommended_actions` — the live answer to "what should I do right now". Founder.html displays the top one as **Next Best Action**.

Today, the typical recommendation is:
> **IDO target in N days — finalize Marshall Islands LLC + audit**

If the recommendation surprises you, fetch the raw JSON to see all signals:
```bash
curl -s https://slh-api-production.up.railway.app/api/brain/state | python -m json.tool
```

---

## 11. When the user says "תוביל" or "המשך"

Translation: **don't keep asking. Pick a concrete next thing from the open list and ship it.**

Open list (as of 2026-04-28 morning, in priority order):
1. Rotate `WEWORK_TEAMVIWER_TOKEN` (BotFather → revoke + new token → `.env` → `docker compose up -d --force-recreate slh-academia-bot`)
2. Get Tzvika's wallet address → upgrade Safe 1/1 → 2/3
3. Pick audit firm (CertiK Lite vs SolidProof)
4. Rewrite or archive `investors.html` + `pitch.html` (stale Series A copy)
5. Rotate Binance EXCHANGE_API_KEY/SECRET (live trading creds in .env — risk)
6. Rotate remaining 30 Telegram bot tokens
7. Add bot heartbeat to remaining bots (slh-claude-bot, slh-game, etc) so registry shows full fleet
8. Investigate slh-fun and slh-game intermittent network DNS failures (Docker)
9. Set up Cloudflare Email Routing for `founder@slh-nft.com` etc (Osif likes this idea)
10. Decide what to do with `@SLH_macro_bot` (no Railway worker — either spin up or retire)

Pick what's high-impact and concrete. Ship it. Update STATUS.md. Don't write a new SESSION_HANDOFF file.

---

## 12. Closing meta-rule

**The system is a nervous system, not a dashboard collection.** Information that doesn't lead to action is noise. Every panel/endpoint/bot you build should answer ONE of these for Osif:
- What is the system state right now?
- What is broken?
- What is the most important next action?

If you find yourself building "more visibility" without "more decision," stop and ask why. The Brain (Phase 1+2+3) is the reference architecture: signal → state → action.

Good luck. Keep the velocity.
