"""
IDO Mission Control API — Phase 1 (read-only, schema-aligned, deploy-ready).

Purpose: serve `/ido.html` (public) + `/admin/ido-mission-control.html` (admin)
with live BNB inflow data, sybil alerts, vesting milestones. Pairs with
the smart contract on PinkSale + the Postgres tables in `07_DB_SCHEMA.sql`.

Auth model:
- Public endpoints (status, top-contributors, milestones) — no auth, returns
  data safe for public eyes (anonymized wallets, aggregate numbers only).
- Admin endpoints (transactions, alerts, dashboard-summary, clusters) require
  X-Admin-Key header matching ADMIN_API_KEYS env var. Same pattern as
  ambassador_crm.py — see line 47 there.

Wiring (when FastAPI is deployed to Railway):
    # in api/main.py
    from routes import ido
    ido.set_pool(pg_pool)
    app.include_router(ido.router)

DB schema: tables ido_transactions, ido_participants, ido_milestones,
ido_alerts, ido_clusters + views ido_dashboard_summary, ido_top_contributors.
See ops/IDO_LAUNCH_2026-04-27/07_DB_SCHEMA.sql for full DDL.

Endpoints (9):
  PUBLIC
    GET  /api/ido/status                    — soft/hard cap progress, time left
    GET  /api/ido/top-contributors          — anonymized top 10 by BNB
    GET  /api/ido/milestones                — public milestones (LLC, audit, caps)

  ADMIN
    GET  /api/ido/dashboard-summary         — KPI tiles (uses view)
    GET  /api/ido/transactions              — paginated tx feed
    GET  /api/ido/alerts                    — list alerts with filters
    POST /api/ido/alerts/{alert_id}/approve — clear a flagged alert
    POST /api/ido/alerts/{alert_id}/reject  — mark for refund (does NOT trigger refund tx)
    GET  /api/ido/clusters                  — sybil cluster list

NOT INCLUDED in Phase 1 (deferred to Phase 2 — smart-contract + multisig wiring):
- Refund execution (must be admin tx through PinkSale + Gnosis Safe — manual)
- Emergency pause (must come from multisig, not API)
- Cluster auto-block (requires whitelist contract sync — design pending)
- Milestone telegram push (separate dispatcher; this module just records timestamps)
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel

router = APIRouter(prefix="/api/ido", tags=["ido"])

_pool = None


def set_pool(pool):
    global _pool
    _pool = pool


# ── DB init (idempotent, runs at startup AFTER set_pool) ──
async def init_ido_tables() -> None:
    """Create all IDO tables + views. Mirror of 07_DB_SCHEMA.sql.

    Safe to run on every boot (CREATE IF NOT EXISTS). Pre-seeds expected
    milestones so the dashboard renders the milestone list before any
    contributions arrive. Uses the global _pool set by set_pool().
    """
    if _pool is None:
        raise RuntimeError("init_ido_tables called before set_pool")
    async with _pool.acquire() as conn:
        # 1. ido_transactions
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS ido_transactions (
                id              BIGSERIAL PRIMARY KEY,
                tx_hash         VARCHAR(66) UNIQUE NOT NULL,
                wallet_address  VARCHAR(42) NOT NULL,
                amount_bnb      NUMERIC(36, 18) NOT NULL,
                amount_ils_at_time NUMERIC(12, 2),
                bnb_price_ils   NUMERIC(12, 2),
                block_number    BIGINT,
                status          VARCHAR(20) NOT NULL DEFAULT 'pending',
                confirmation_count INT DEFAULT 0,
                contract_address VARCHAR(42) NOT NULL,
                tokens_allocated NUMERIC(36, 18),
                vesting_schedule_id BIGINT,
                created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                confirmed_at    TIMESTAMP WITH TIME ZONE,
                refunded_at     TIMESTAMP WITH TIME ZONE,
                notes           TEXT
            )
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_ido_tx_wallet ON ido_transactions(wallet_address)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_ido_tx_status ON ido_transactions(status)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_ido_tx_created ON ido_transactions(created_at DESC)")

        # 2. ido_participants
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS ido_participants (
                id              BIGSERIAL PRIMARY KEY,
                wallet_address  VARCHAR(42) UNIQUE NOT NULL,
                total_contributed_bnb NUMERIC(36, 18) DEFAULT 0,
                total_tokens_allocated NUMERIC(36, 18) DEFAULT 0,
                contribution_count INT DEFAULT 0,
                first_contribution_at TIMESTAMP WITH TIME ZONE,
                last_contribution_at  TIMESTAMP WITH TIME ZONE,
                sybil_score     INT DEFAULT 0,
                cluster_id      BIGINT,
                ofac_flagged    BOOLEAN DEFAULT FALSE,
                chainabuse_flagged BOOLEAN DEFAULT FALSE,
                velocity_flagged BOOLEAN DEFAULT FALSE,
                zuz_score       INT DEFAULT 0,
                zvk_awarded     INT DEFAULT 0,
                rep_awarded     INT DEFAULT 0,
                founder_nft_eligible BOOLEAN DEFAULT FALSE,
                created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                updated_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_ido_participants_wallet ON ido_participants(wallet_address)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_ido_participants_total ON ido_participants(total_contributed_bnb DESC)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_ido_participants_sybil ON ido_participants(sybil_score DESC)")

        # 3. ido_milestones (with pre-seed)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS ido_milestones (
                id              BIGSERIAL PRIMARY KEY,
                milestone_type  VARCHAR(50) UNIQUE NOT NULL,
                reached_at      TIMESTAMP WITH TIME ZONE,
                block_number    BIGINT,
                details         JSONB,
                notified_telegram BOOLEAN DEFAULT FALSE,
                created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """)
        await conn.execute("""
            INSERT INTO ido_milestones (milestone_type, details) VALUES
                ('llc_certified',         '{"target_date": "2026-05-08"}'::jsonb),
                ('audit_passed',          '{"target_date": "2026-05-12"}'::jsonb),
                ('ido_started',           '{"target_date": "2026-05-14"}'::jsonb),
                ('soft_cap_reached',      '{"target_bnb": 20}'::jsonb),
                ('hard_cap_reached',      '{"target_bnb": 150}'::jsonb),
                ('ido_ended',             '{"target_date": "2026-06-13"}'::jsonb),
                ('lp_locked',             '{"lock_duration_days": 365}'::jsonb),
                ('first_vesting_release', '{"percent": 20}'::jsonb)
            ON CONFLICT (milestone_type) DO NOTHING
        """)

        # 4. ido_alerts
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS ido_alerts (
                id              BIGSERIAL PRIMARY KEY,
                alert_type      VARCHAR(50) NOT NULL,
                severity        VARCHAR(20) NOT NULL DEFAULT 'medium',
                wallet_address  VARCHAR(42),
                tx_hash         VARCHAR(66),
                cluster_id      BIGINT,
                description     TEXT NOT NULL,
                details         JSONB,
                auto_action_taken VARCHAR(50),
                admin_action    VARCHAR(50),
                admin_action_by VARCHAR(50),
                admin_action_at TIMESTAMP WITH TIME ZONE,
                notified_telegram BOOLEAN DEFAULT FALSE,
                created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_ido_alerts_severity ON ido_alerts(severity, created_at DESC)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_ido_alerts_wallet ON ido_alerts(wallet_address)")

        # 5. ido_vesting_schedules
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS ido_vesting_schedules (
                id              BIGSERIAL PRIMARY KEY,
                participant_id  BIGINT REFERENCES ido_participants(id),
                wallet_address  VARCHAR(42) NOT NULL,
                total_tokens    NUMERIC(36, 18) NOT NULL,
                tge_release_pct NUMERIC(5, 2) DEFAULT 20.00,
                cycle_release_pct NUMERIC(5, 2) DEFAULT 20.00,
                cycle_days      INT DEFAULT 30,
                cycles_total    INT DEFAULT 4,
                cycles_released INT DEFAULT 0,
                tokens_released NUMERIC(36, 18) DEFAULT 0,
                next_release_at TIMESTAMP WITH TIME ZONE,
                completed       BOOLEAN DEFAULT FALSE,
                created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                updated_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """)

        # 6. ido_clusters
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS ido_clusters (
                id              BIGSERIAL PRIMARY KEY,
                cluster_signature VARCHAR(64) UNIQUE NOT NULL,
                detection_method VARCHAR(50) NOT NULL,
                wallet_count    INT DEFAULT 1,
                total_contributed_bnb NUMERIC(36, 18) DEFAULT 0,
                risk_score      INT DEFAULT 0,
                confirmed_sybil BOOLEAN DEFAULT FALSE,
                notes           TEXT,
                created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                updated_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """)

        # Views
        await conn.execute("""
            CREATE OR REPLACE VIEW ido_dashboard_summary AS
            SELECT
                (SELECT COALESCE(SUM(amount_bnb),0) FROM ido_transactions WHERE status = 'confirmed') AS total_raised_bnb,
                (SELECT COUNT(DISTINCT wallet_address) FROM ido_transactions WHERE status = 'confirmed') AS unique_participants,
                (SELECT COUNT(*) FROM ido_transactions WHERE status = 'pending') AS pending_txs,
                (SELECT COUNT(*) FROM ido_alerts WHERE admin_action IS NULL AND severity IN ('high','critical')) AS open_critical_alerts,
                (SELECT COUNT(*) FROM ido_clusters WHERE confirmed_sybil = TRUE) AS confirmed_sybil_clusters,
                (SELECT reached_at FROM ido_milestones WHERE milestone_type = 'soft_cap_reached') AS soft_cap_at,
                (SELECT reached_at FROM ido_milestones WHERE milestone_type = 'hard_cap_reached') AS hard_cap_at
        """)
        await conn.execute("""
            CREATE OR REPLACE VIEW ido_top_contributors AS
            SELECT
                wallet_address,
                CONCAT(SUBSTRING(wallet_address FROM 1 FOR 6), '...', SUBSTRING(wallet_address FROM 39 FOR 4)) AS wallet_short,
                total_contributed_bnb,
                contribution_count,
                sybil_score,
                first_contribution_at
            FROM ido_participants
            WHERE sybil_score < 50
            ORDER BY total_contributed_bnb DESC
            LIMIT 10
        """)


# ── auth ──
_ADMIN_KEYS = {
    k.strip() for k in os.getenv("ADMIN_API_KEYS", "").split(",") if k.strip()
}


def _require_admin(x_admin_key: Optional[str]) -> None:
    if not x_admin_key or x_admin_key not in _ADMIN_KEYS:
        raise HTTPException(403, "Admin key required (X-Admin-Key header)")


# ── schema-aligned constants ──
SOFT_CAP_BNB = 20
HARD_CAP_BNB = 150
MIN_BUY_BNB = 0.05
MAX_BUY_BNB = 5
TGE_PCT = 20
VESTING_CYCLES = 4
LP_LOCK_DAYS = 365


# ── helpers ──
def _anonymize_wallet(addr: str) -> str:
    """0x1234567890abcdef... -> 0x1234...cdef"""
    if not addr or len(addr) < 10:
        return addr or ""
    return f"{addr[:6]}...{addr[-4:]}"


def _serialize_tx(row) -> dict:
    return {
        "id":               row["id"],
        "tx_hash":          row["tx_hash"],
        "wallet_address":   row["wallet_address"],
        "wallet_short":     _anonymize_wallet(row["wallet_address"]),
        "amount_bnb":       float(row["amount_bnb"]),
        "amount_ils":       float(row["amount_ils_at_time"]) if row["amount_ils_at_time"] else None,
        "status":           row["status"],
        "tokens_allocated": float(row["tokens_allocated"]) if row["tokens_allocated"] else None,
        "block_number":     row["block_number"],
        "created_at":       row["created_at"].isoformat() if row["created_at"] else None,
        "confirmed_at":     row["confirmed_at"].isoformat() if row["confirmed_at"] else None,
    }


def _serialize_alert(row) -> dict:
    return {
        "id":                row["id"],
        "alert_type":        row["alert_type"],
        "severity":          row["severity"],
        "wallet_address":    row["wallet_address"],
        "wallet_short":      _anonymize_wallet(row["wallet_address"] or ""),
        "tx_hash":           row["tx_hash"],
        "cluster_id":        row["cluster_id"],
        "description":       row["description"],
        "auto_action_taken": row["auto_action_taken"],
        "admin_action":      row["admin_action"],
        "admin_action_by":   row["admin_action_by"],
        "admin_action_at":   row["admin_action_at"].isoformat() if row["admin_action_at"] else None,
        "created_at":        row["created_at"].isoformat() if row["created_at"] else None,
    }


# ── PUBLIC endpoints ──

@router.get("/status")
async def get_status():
    """Public — soft/hard cap progress, ends_at, contributor count.

    Polled by /ido.html every 30s. Safe for public eyes — only aggregates.
    """
    if _pool is None:
        raise HTTPException(503, "DB pool not initialized")

    async with _pool.acquire() as conn:
        # Sum of confirmed contributions
        total_row = await conn.fetchrow("""
            SELECT
                COALESCE(SUM(amount_bnb), 0) AS total_bnb,
                COUNT(DISTINCT wallet_address) AS unique_participants,
                COUNT(*) AS confirmed_count
            FROM ido_transactions
            WHERE status = 'confirmed'
        """)

        # Milestone timestamps (for "soft cap met at" / "ends at")
        milestones = await conn.fetch("""
            SELECT milestone_type, reached_at, details
            FROM ido_milestones
            WHERE milestone_type IN
                ('soft_cap_reached', 'hard_cap_reached', 'ido_started', 'ido_ended')
        """)
        ms_map = {
            m["milestone_type"]: {
                "reached_at": m["reached_at"].isoformat() if m["reached_at"] else None,
                "target": (m["details"] or {}),
            }
            for m in milestones
        }

    total_bnb = float(total_row["total_bnb"])
    return {
        "total_raised_bnb":    total_bnb,
        "soft_cap_bnb":        SOFT_CAP_BNB,
        "hard_cap_bnb":        HARD_CAP_BNB,
        "soft_cap_pct":        round((total_bnb / SOFT_CAP_BNB) * 100, 1),
        "hard_cap_pct":        round((total_bnb / HARD_CAP_BNB) * 100, 1),
        "soft_cap_met":        total_bnb >= SOFT_CAP_BNB,
        "hard_cap_met":        total_bnb >= HARD_CAP_BNB,
        "unique_participants": total_row["unique_participants"],
        "confirmed_tx_count":  total_row["confirmed_count"],
        "milestones":          ms_map,
        "as_of":               datetime.now(timezone.utc).isoformat(),
    }


@router.get("/top-contributors")
async def get_top_contributors():
    """Public — top 10 contributors with anonymized wallets.

    Uses ido_top_contributors view (already filters sybil_score < 50).
    """
    if _pool is None:
        raise HTTPException(503, "DB pool not initialized")

    async with _pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM ido_top_contributors")

    return {
        "contributors": [
            {
                "rank":               i + 1,
                "wallet_short":       row["wallet_short"],
                "total_bnb":          float(row["total_contributed_bnb"]),
                "contribution_count": row["contribution_count"],
                "first_seen_at":      row["first_contribution_at"].isoformat() if row["first_contribution_at"] else None,
            }
            for i, row in enumerate(rows)
        ]
    }


@router.get("/milestones")
async def get_milestones():
    """Public — list of milestones with their reached_at timestamps."""
    if _pool is None:
        raise HTTPException(503, "DB pool not initialized")

    async with _pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT id, milestone_type, reached_at, details, notified_telegram, created_at
            FROM ido_milestones
            ORDER BY id
        """)

    return {
        "milestones": [
            {
                "id":                row["id"],
                "milestone_type":    row["milestone_type"],
                "reached_at":        row["reached_at"].isoformat() if row["reached_at"] else None,
                "details":           row["details"] or {},
                "notified_telegram": row["notified_telegram"],
            }
            for row in rows
        ]
    }


# ── ADMIN endpoints ──

@router.get("/dashboard-summary")
async def get_dashboard_summary(x_admin_key: Optional[str] = Header(None)):
    """Admin — KPI tiles for /admin/ido-mission-control.html.

    Wraps the ido_dashboard_summary view. Returns null-safe values.
    """
    _require_admin(x_admin_key)
    if _pool is None:
        raise HTTPException(503, "DB pool not initialized")

    async with _pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM ido_dashboard_summary")

    return {
        "total_raised_bnb":         float(row["total_raised_bnb"] or 0),
        "unique_participants":      row["unique_participants"] or 0,
        "pending_txs":              row["pending_txs"] or 0,
        "open_critical_alerts":     row["open_critical_alerts"] or 0,
        "confirmed_sybil_clusters": row["confirmed_sybil_clusters"] or 0,
        "soft_cap_at":              row["soft_cap_at"].isoformat() if row["soft_cap_at"] else None,
        "hard_cap_at":              row["hard_cap_at"].isoformat() if row["hard_cap_at"] else None,
        "as_of":                    datetime.now(timezone.utc).isoformat(),
    }


@router.get("/transactions")
async def get_transactions(
    x_admin_key: Optional[str] = Header(None),
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
    status: Optional[str] = Query(None, description="pending|confirmed|failed|refunded"),
    wallet: Optional[str] = Query(None, description="filter by wallet address"),
):
    """Admin — paginated tx feed for live monitoring."""
    _require_admin(x_admin_key)
    if _pool is None:
        raise HTTPException(503, "DB pool not initialized")

    where_clauses = []
    params: list = []
    if status:
        if status not in {"pending", "confirmed", "failed", "refunded"}:
            raise HTTPException(400, f"Invalid status: {status}")
        where_clauses.append(f"status = ${len(params) + 1}")
        params.append(status)
    if wallet:
        where_clauses.append(f"wallet_address = ${len(params) + 1}")
        params.append(wallet.lower())
    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT id, tx_hash, wallet_address, amount_bnb, amount_ils_at_time,
                   block_number, status, tokens_allocated, created_at, confirmed_at
            FROM ido_transactions
            {where_sql}
            ORDER BY created_at DESC
            LIMIT {limit} OFFSET {offset}
            """,
            *params,
        )
        total_row = await conn.fetchrow(
            f"SELECT COUNT(*) AS n FROM ido_transactions {where_sql}",
            *params,
        )

    return {
        "transactions": [_serialize_tx(r) for r in rows],
        "total":        total_row["n"],
        "limit":        limit,
        "offset":       offset,
    }


@router.get("/alerts")
async def get_alerts(
    x_admin_key: Optional[str] = Header(None),
    severity: Optional[str] = Query(None, description="comma-separated: low,medium,high,critical"),
    actioned: Optional[bool] = Query(None, description="true=actioned, false=unactioned, null=all"),
    limit: int = Query(50, ge=1, le=200),
):
    """Admin — list alerts with severity / actioned filters."""
    _require_admin(x_admin_key)
    if _pool is None:
        raise HTTPException(503, "DB pool not initialized")

    where_clauses = []
    params: list = []
    if severity:
        sevs = [s.strip() for s in severity.split(",") if s.strip()]
        valid = {"low", "medium", "high", "critical"}
        bad = [s for s in sevs if s not in valid]
        if bad:
            raise HTTPException(400, f"Invalid severity: {bad}")
        where_clauses.append(f"severity = ANY(${len(params) + 1}::text[])")
        params.append(sevs)
    if actioned is True:
        where_clauses.append("admin_action IS NOT NULL")
    elif actioned is False:
        where_clauses.append("admin_action IS NULL")
    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT id, alert_type, severity, wallet_address, tx_hash, cluster_id,
                   description, auto_action_taken, admin_action, admin_action_by,
                   admin_action_at, created_at
            FROM ido_alerts
            {where_sql}
            ORDER BY
                CASE severity
                    WHEN 'critical' THEN 0
                    WHEN 'high'     THEN 1
                    WHEN 'medium'   THEN 2
                    WHEN 'low'      THEN 3
                END,
                created_at DESC
            LIMIT {limit}
            """,
            *params,
        )

    return {"alerts": [_serialize_alert(r) for r in rows]}


class AlertActionBody(BaseModel):
    admin_user_id: str
    note: Optional[str] = None


@router.post("/alerts/{alert_id}/approve")
async def approve_alert(
    alert_id: int,
    body: AlertActionBody,
    x_admin_key: Optional[str] = Header(None),
):
    """Admin — clear an alert (approve the flagged tx as legit).

    Updates ido_alerts.admin_action='approved'. Does NOT modify the underlying
    tx — that already settled on-chain. Just records the human decision.
    """
    _require_admin(x_admin_key)
    if _pool is None:
        raise HTTPException(503, "DB pool not initialized")

    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE ido_alerts
            SET admin_action = 'approved',
                admin_action_by = $2,
                admin_action_at = NOW()
            WHERE id = $1 AND admin_action IS NULL
            RETURNING id, admin_action, admin_action_by, admin_action_at
            """,
            alert_id, body.admin_user_id,
        )
        if not row:
            raise HTTPException(404, f"Alert {alert_id} not found or already actioned")

    return {
        "ok":              True,
        "alert_id":        row["id"],
        "admin_action":    row["admin_action"],
        "admin_action_by": row["admin_action_by"],
        "admin_action_at": row["admin_action_at"].isoformat(),
        "note":            body.note,
    }


@router.post("/alerts/{alert_id}/reject")
async def reject_alert(
    alert_id: int,
    body: AlertActionBody,
    x_admin_key: Optional[str] = Header(None),
):
    """Admin — mark alert as rejected (queued for refund).

    Sets admin_action='rejected'. Refund execution is NOT done here — must be
    triggered by Osif manually via PinkSale dashboard or Gnosis Safe transaction.
    This endpoint just records the decision and creates an audit trail.
    """
    _require_admin(x_admin_key)
    if _pool is None:
        raise HTTPException(503, "DB pool not initialized")

    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE ido_alerts
            SET admin_action = 'rejected',
                admin_action_by = $2,
                admin_action_at = NOW()
            WHERE id = $1 AND admin_action IS NULL
            RETURNING id, admin_action, admin_action_by, admin_action_at,
                      wallet_address, tx_hash
            """,
            alert_id, body.admin_user_id,
        )
        if not row:
            raise HTTPException(404, f"Alert {alert_id} not found or already actioned")

    return {
        "ok":               True,
        "alert_id":         row["id"],
        "admin_action":     row["admin_action"],
        "admin_action_by":  row["admin_action_by"],
        "admin_action_at":  row["admin_action_at"].isoformat(),
        "wallet_address":   row["wallet_address"],
        "tx_hash":          row["tx_hash"],
        "next_step":        "refund_required: trigger refund via PinkSale dashboard or Gnosis Safe",
        "note":             body.note,
    }


@router.get("/clusters")
async def get_clusters(
    x_admin_key: Optional[str] = Header(None),
    confirmed_sybil: Optional[bool] = Query(None),
    min_risk: int = Query(0, ge=0, le=100),
):
    """Admin — sybil cluster list. Confirmed clusters get auto-blocked downstream."""
    _require_admin(x_admin_key)
    if _pool is None:
        raise HTTPException(503, "DB pool not initialized")

    where_clauses = ["risk_score >= $1"]
    params: list = [min_risk]
    if confirmed_sybil is not None:
        where_clauses.append(f"confirmed_sybil = ${len(params) + 1}")
        params.append(confirmed_sybil)
    where_sql = "WHERE " + " AND ".join(where_clauses)

    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT id, cluster_signature, detection_method, wallet_count,
                   total_contributed_bnb, risk_score, confirmed_sybil, notes,
                   created_at, updated_at
            FROM ido_clusters
            {where_sql}
            ORDER BY risk_score DESC, total_contributed_bnb DESC
            """,
            *params,
        )

    return {
        "clusters": [
            {
                "id":                    r["id"],
                "cluster_signature":     r["cluster_signature"],
                "detection_method":      r["detection_method"],
                "wallet_count":          r["wallet_count"],
                "total_contributed_bnb": float(r["total_contributed_bnb"] or 0),
                "risk_score":            r["risk_score"],
                "confirmed_sybil":       r["confirmed_sybil"],
                "notes":                 r["notes"],
                "created_at":            r["created_at"].isoformat() if r["created_at"] else None,
                "updated_at":            r["updated_at"].isoformat() if r["updated_at"] else None,
            }
            for r in rows
        ]
    }
