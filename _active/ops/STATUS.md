# SLH Spark — Status (Single Source of Truth)

**Last updated:** 2026-04-28 by Claude (Opus 4.7)
**Replaces:** all `SESSION_HANDOFF_*.md` files (now in `_archive/handoffs_2026-04/`)

> Update this file at the END of every working session. Do NOT create new `SESSION_HANDOFF_*.md` — append to the **Recent Changes** section here instead.

---

## 💎 SLH Treasury (Gnosis Safe — created 2026-04-28)

**Address (BSC):** `0x9DD8aF7Ac0f601CD473422311b2942DAE9D0BD09`
**Network:** BNB Smart Chain
**Threshold:** 1/1 (Osif only — upgrade to 2-of-3 with Tzvika + recovery hardware before IDO launch)
**Balance:** 0 BNB (needs initial fund of 0.01-0.05 BNB for first activation tx)

This is the **on-chain treasury for SLH IDO proceeds**. PinkSale will be configured to send all raised BNB to this address.

[View on BscScan](https://bscscan.com/address/0x9DD8aF7Ac0f601CD473422311b2942DAE9D0BD09)
[View on Safe app](https://app.safe.global/home?safe=bnb:0x9DD8aF7Ac0f601CD473422311b2942DAE9D0BD09)

---

## 🧠 Command Brain — LIVE (Phase 2 deployed)

**Endpoints:**
- `GET /api/brain/state` — Decision Layer JSON (system_state, health_score, issues, actions, summary). Auto-logs to `brain_log`.
- `GET /api/brain/history?hours=24` — recent state snapshots + window stats
- `GET /api/brain/prompt` — LLM-ready structured prompt
- `GET /api/brain/health` — quick liveness probe

**Decision Strip** displayed at top of `/founder.html` (auto-refresh 30s).

---

## 🎛️ Founder Control Panel

**One-click access:** [slh-nft.com/founder.html](https://slh-nft.com/founder.html)
- **Decision Strip** (top): system_state, health_score, Next Best Action, raw JSON view
- Live system health tiles (auto-refresh 30s)
- IDO countdown to 2026-05-11
- Marshall Islands LLC + Safe + Audit as today's 3 actions
- Quick nav

---

## ✅ Verified Live (2026-04-27)

| Component | URL | Status |
|---|---|---|
| Website | https://slh-nft.com | LIVE (GitHub Pages, auto-deploy from `osifeu-prog.github.io` main) |
| Investors page | https://slh-nft.com/investors.html | LIVE but **STALE narrative** — still pitches Series A ₪50K min, needs IDO rewrite |
| Pitch deck | https://slh-nft.com/pitch.html | LIVE but **STALE narrative** — 18 slides on Series A, needs IDO rewrite |
| Founder panel | https://slh-nft.com/founder.html | LIVE — control center for Osif (now IDO-aligned) |
| IDO Mission Control | /admin/ido-mission-control.html | local file (16KB, untracked) — needs commit + push |
| API | https://slh-api-production.up.railway.app/api/health | `{"status":"ok","db":"connected","version":"1.1.0"}` |
| ESP firmware | v4.6 flashed | Heartbeats arriving, tap-anywhere bug fixed |

---

## 🔑 Authentication Reality

**Railway env (verified live):** admin key = `slh_admin_2026_rotated_04_20`
**Local .env:** has newer key `QVUvE_3Nv4YmJM0SPf512YeNBlj3kDt2XI2ix1sBfF3R8b5FfpI-kw` — **NOT pushed to Railway**.

⚠️ **Discrepancy:** local rotation never propagated to Railway. Either rotate Railway to match local, or revert local to match Railway. Current state confuses tooling.

Test:
```bash
curl -H "X-Admin-Key: slh_admin_2026_rotated_04_20" \
  https://slh-api-production.up.railway.app/api/admin/devices/list
# → 200 OK, returns 6 devices
```

---

## 🎯 Strategic Focus — IDO + Marshall Islands DAO (Approved 27.4 evening)

**Target:** ₪500K via on-chain IDO. NO Israeli regulator, NO bank, NO off-chain investors.
**Entity:** Marshall Islands DAO LLC ($1,500 setup, $1,000/year, MIDAO 2022 Act)
**Treasury:** Gnosis Safe 2-of-3 multisig (Osif + Tzvika + recovery hardware)
**Platform:** PinkSale.finance on BSC (uses existing SLH BEP-20)
**Audit:** CertiK Lite or SolidProof (~$3-5K)
**Token:** SLH on PancakeSwap V2 — `0xACb0A09414CEA1C879c67bB7A877E4e19480f022`

**IDO parameters (proposed):** Soft cap 20 BNB (~₪50K), Hard cap 150 BNB (~₪370K), Price 0.000004 BNB/SLH, Min/Max 0.05/5 BNB, Vesting 20% TGE + 20%/month × 4, LP Lock 365 days via PinkLock.

**Timeline:** 14-day build → IDO goes live ~2026-05-11 → 30-day collection window.

**Superseded narratives (deprecated 27.4 evening):**
- ❌ ~~$4.4M Series A via SAFE + Token Warrant~~
- ❌ ~~Eliezer's 130 CSV import to CRM~~ (those leads = Bitnest victims, do NOT approach without explicit warning)
- ❌ ~~Pitango/Pico VC pitches~~
- ❌ ~~Israeli Ltd / Tnufa entity~~

**Voice/Course/Marketplace CTAs remain deprioritized** until IDO closes.

`investors.html` and `pitch.html` need rewriting to match IDO narrative — currently still describe Series A path.

---

## 🛠️ Open Blockers (P0 only — see KNOWN_ISSUES.md for full list)

1. **Marshall Islands LLC application** — 7-10 days. Hasn't started yet. Blocks treasury, blocks IDO.
2. **Gnosis Safe 2-of-3** — needs Osif + Tzvika + recovery hardware wallet. Hasn't been set up.
3. **Audit firm selection** — CertiK Lite vs SolidProof. 5-7 days lead time. Required pre-PinkSale.
4. **FastAPI deploy on Railway** — `/api/ido/*` endpoints can't go live until FastAPI service is up. Per CLAUDE.md, current `slh-api-production.up.railway.app` IS responding 200, but CLAUDE.md claims it's not — discrepancy needs resolving.
5. **Railway env vars discrepancy** — admin key mismatch between local `.env` (`QVUvE_…`) and Railway (`slh_admin_2026_rotated_04_20`). Pick one and align.
6. **investors.html + pitch.html narrative drift** — still pitch Series A. Need IDO rewrite OR explicit dual-narrative.
7. **30 unrotated bot tokens** — only GAME_BOT_TOKEN rotated. Background risk.
8. **Binance live trading creds in `.env`** — rotate or move to vault.
9. **/ido.html** — public-facing IDO landing page does NOT exist yet. Listed in IDO plan as required.

---

## 📁 Repo Layout (canonical)

```
D:\SLH_ECOSYSTEM\
├── api/main.py          # FastAPI ~11,765 lines, single source of truth
├── main.py              # 3-line shim: `from api.main import app`
├── website/             # Separate git → osifeu-prog.github.io
│   ├── investors.html   # Investor one-pager
│   ├── pitch.html       # 18-slide deck
│   ├── founder.html     # ← THIS PAGE'S DASHBOARD
│   ├── admin/mission-control.html
│   └── ...60+ pages
├── ops/
│   ├── STATUS.md        # ← YOU ARE HERE (single source of truth)
│   ├── OPS_RUNBOOK.md   # Detailed ops procedures
│   ├── KNOWN_ISSUES.md  # Verified bug backlog
│   └── _archive/        # Old SESSION_HANDOFF_*.md files
├── device-registry/esp32-cyd-work/firmware/slh-device-v4/
└── docker-compose.yml   # 25 bots
```

**Build:** Dockerfile runs `uvicorn main:app` from root. Edit `api/main.py` and push — Railway auto-deploys from `master` branch of `osifeu-prog/slh-api`.

---

## 📨 Recent Changes

### 2026-04-27 evening (IDO pivot)
- 🟡 **Strategic pivot:** Series A path → IDO + Marshall Islands DAO LLC (₪500K target). See `project_ido_marshall_plan.md` in memory.
- ✅ `website/admin/ido-mission-control.html` — internal IDO dashboard (parallel session, untracked, 16KB)
- ✅ `website/founder.html` — single-page founder dashboard, **updated to IDO narrative** (countdown to 11.5.26, Marshall Islands + Gnosis Safe + audit as today's 3 actions)
- ✅ `ops/_archive/handoffs_2026-04/` — 59 stale `SESSION_HANDOFF_*.md` files moved here
- ✅ `ops/STATUS.md` (this file) — replaces the handoff churn
- ✅ ESP firmware v4.6 — fixed tap-anywhere navigation bug + accidental-reboot bug

### 2026-04-27 day (now-deprecated Series A work — kept for traceability)
- ⚠️ `website/investors.html` v2 — ₪50K Series A narrative · **needs IDO rewrite**
- ⚠️ `website/pitch.html` — 18-slide Series A deck · **needs IDO rewrite**
- ⚠️ `ops/financial-model-3yr.csv` — built for Series A scenario · re-evaluate for IDO
- ⚠️ `ops/DATA_ROOM_STRUCTURE.md` — Tier 1/2/3 access scheme · re-evaluate
- ⚠️ `ops/STRATEGY_LUMINA_INTEGRATION_20260427.md` — Lumina vision (still relevant, just delayed)
- ✅ `website/assets/logo/` — 3 SVG logo variants + favicon (still relevant)
- ✅ `api/main.py` — verify-bug self-healing + SMS-parallel fixes (Railway deployed)
- ✅ Nav reorder — `/investors` surfaced above sales pages

---

## 🔄 Session Workflow (going forward)

1. **At session start:** read this file. Skim KNOWN_ISSUES.md.
2. **During session:** edit code. Don't create new SESSION_HANDOFF_*.md files.
3. **At session end:**
   - Append a dated bullet to **Recent Changes** above (most recent at top of section).
   - If you discovered a new P0 blocker, add it to **Open Blockers**.
   - If a blocker resolved, remove it.
   - Update **Last updated** at top.
4. **Telegram broadcast:** if commits affect users, send via `/api/broadcast/send` with the broadcast key from `.env`.

---

## 🚦 Health Matrix

```bash
# Run from anywhere (uses public endpoints):
curl -s https://slh-api-production.up.railway.app/api/health
curl -sI https://slh-nft.com/investors.html | head -1
curl -sI https://slh-nft.com/pitch.html | head -1
curl -sI https://slh-nft.com/founder.html | head -1

# Admin (requires Railway key):
curl -H "X-Admin-Key: slh_admin_2026_rotated_04_20" \
  https://slh-api-production.up.railway.app/api/admin/devices/list | jq '.devices | length'
```

Expect: all `200 OK`, devices count ≥ 6.
