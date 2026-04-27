"""
SLH Command Brain — Decision Layer
=====================================

Aggregates signals from across the SLH ecosystem and outputs a single
"BrainState" JSON. UI panels and prompt engines consume this endpoint
to know what's happening and what to do next.

Schema:
  - system_state: "HEALTHY" | "DEGRADED" | "CRITICAL"
  - health_score: 0..100
  - summary: one-line "System Intelligence" sentence
  - signals: {name -> {status, value, detail}}
  - critical_issues: [{id, severity, title, detail, evidence_url}]
  - recommended_actions: [{id, label, impact, confidence, issue_id, execution}]
  - confidence: 0..1
  - checked_at: ISO timestamp
  - age_ms: how long this response took to compute

Endpoints:
  GET /api/brain/state    — full BrainState (no auth, read-only)
  GET /api/brain/health   — quick liveness probe
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/brain", tags=["Brain"])

_pool = None


def set_pool(pool):
    """Wire the asyncpg pool from main.py at startup."""
    global _pool
    _pool = pool


# ─────────────────────────────────────────────────────────────────────────
# Data models
# ─────────────────────────────────────────────────────────────────────────


class Signal(BaseModel):
    name: str
    status: str  # "ok" | "warn" | "error"
    value: Optional[Any] = None
    detail: Optional[str] = None


class Issue(BaseModel):
    id: str
    severity: str  # "INFO" | "WARN" | "CRITICAL"
    title: str
    detail: str
    evidence_url: Optional[str] = None


class Action(BaseModel):
    id: str
    label: str
    impact: str  # "LOW" | "MEDIUM" | "HIGH"
    confidence: float  # 0.0..1.0
    issue_id: Optional[str] = None
    # execution.type: "manual" | "link" | "api"
    # execution.instruction: human-readable / URL / endpoint
    execution: Dict[str, Any]


# ─────────────────────────────────────────────────────────────────────────
# Signal collectors — each returns one Signal, never raises
# ─────────────────────────────────────────────────────────────────────────


async def _collect_api_signal() -> Signal:
    # If this code is running, the API is up by definition.
    return Signal(
        name="api",
        status="ok",
        value="1.1.0",
        detail="this module is responding == API up",
    )


async def _collect_db_signal() -> Signal:
    if not _pool:
        return Signal(name="db", status="error", detail="pool not initialized")
    try:
        async with _pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return Signal(name="db", status="ok", value="connected")
    except Exception as e:
        return Signal(name="db", status="error", detail=str(e)[:200])


async def _collect_devices_signal() -> Signal:
    if not _pool:
        return Signal(name="devices", status="error", detail="pool not initialized")
    try:
        async with _pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    device_id,
                    last_seen,
                    EXTRACT(EPOCH FROM (NOW() - last_seen))::int AS age_s
                FROM devices
                WHERE is_active = TRUE
                """
            )
        total = len(rows)
        fresh = sum(1 for r in rows if r["age_s"] is not None and r["age_s"] < 90)
        stale = total - fresh
        ages = [r["age_s"] for r in rows if r["age_s"] is not None]
        stalest = max(ages) if ages else 0
        if total == 0:
            status = "warn"
        elif fresh > 0:
            status = "ok"
        else:
            status = "warn"
        return Signal(
            name="devices",
            status=status,
            value={
                "total": total,
                "fresh": fresh,
                "stale": stale,
                "stalest_age_s": stalest,
            },
            detail=f"{fresh}/{total} devices alive (last 90s)",
        )
    except Exception as e:
        return Signal(name="devices", status="error", detail=str(e)[:200])


async def _collect_users_signal() -> Signal:
    if not _pool:
        return Signal(name="users", status="error", detail="pool not initialized")
    try:
        async with _pool.acquire() as conn:
            total = await conn.fetchval("SELECT COUNT(*) FROM users")
        return Signal(name="users", status="ok", value=int(total or 0))
    except Exception:
        return Signal(name="users", status="warn", detail="users table missing")


async def _collect_heartbeats_24h() -> Signal:
    if not _pool:
        return Signal(name="heartbeats_24h", status="error", detail="pool not initialized")
    try:
        async with _pool.acquire() as conn:
            count = await conn.fetchval(
                """
                SELECT COUNT(*) FROM device_heartbeats
                WHERE received_at > NOW() - INTERVAL '24 hours'
                """
            )
        return Signal(name="heartbeats_24h", status="ok", value=int(count or 0))
    except Exception:
        return Signal(name="heartbeats_24h", status="warn", detail="device_heartbeats unavailable")


async def _collect_ido_signal() -> Signal:
    """IDO planning state — placeholder until contract deploys."""
    # Target launch ~2026-05-11 per IDO + Marshall Islands plan
    target = datetime(2026, 5, 11, 12, 0, tzinfo=timezone.utc)
    days_left = (target - datetime.now(timezone.utc)).days
    return Signal(
        name="ido",
        status="warn" if days_left < 0 else "ok",
        value={"phase": "planning", "days_to_target": days_left},
        detail=f"IDO planning · {days_left}d to target launch",
    )


# ─────────────────────────────────────────────────────────────────────────
# Rules engine
# ─────────────────────────────────────────────────────────────────────────


def _evaluate(signals: Dict[str, Signal]) -> Tuple[str, int, List[Issue], List[Action]]:
    """Pure function: signals → (state, health_score, issues, actions)."""
    issues: List[Issue] = []
    actions: List[Action] = []
    score = 100

    api = signals.get("api")
    if not api or api.status != "ok":
        score -= 40
        issues.append(
            Issue(
                id="api_down",
                severity="CRITICAL",
                title="API not responding",
                detail="The brain endpoint cannot reach the API itself.",
                evidence_url="/api/health",
            )
        )
        actions.append(
            Action(
                id="restart_railway",
                label="Restart Railway slh-api service",
                impact="HIGH",
                confidence=0.9,
                issue_id="api_down",
                execution={
                    "type": "manual",
                    "instruction": "Open https://railway.app → slh-api → Restart",
                },
            )
        )

    db = signals.get("db")
    if not db or db.status != "ok":
        score -= 30
        issues.append(
            Issue(
                id="db_down",
                severity="CRITICAL",
                title="Database unreachable",
                detail=(db.detail if db else "Connection pool not initialized"),
                evidence_url="/api/health",
            )
        )
        actions.append(
            Action(
                id="check_railway_pg",
                label="Check Railway Postgres add-on",
                impact="HIGH",
                confidence=0.85,
                issue_id="db_down",
                execution={"type": "link", "instruction": "https://railway.app/dashboard"},
            )
        )

    dev = signals.get("devices")
    if dev and isinstance(dev.value, dict):
        total = int(dev.value.get("total", 0))
        fresh = int(dev.value.get("fresh", 0))
        stalest = int(dev.value.get("stalest_age_s", 0) or 0)
        if total == 0:
            score -= 8
            issues.append(
                Issue(
                    id="no_devices",
                    severity="INFO",
                    title="No active devices in DB",
                    detail="No ESP32 device rows found.",
                    evidence_url="/api/admin/devices/list",
                )
            )
        elif fresh == 0 and total > 0:
            score -= 12
            ago_h = stalest / 3600
            issues.append(
                Issue(
                    id="all_devices_offline",
                    severity="WARN",
                    title=f"All {total} devices offline",
                    detail=f"No heartbeat within 90s. Stalest is {ago_h:.1f}h old.",
                    evidence_url="/api/admin/devices/list",
                )
            )
            actions.append(
                Action(
                    id="reconnect_esp",
                    label="Reconnect ESP USB or check WiFi",
                    impact="MEDIUM",
                    confidence=0.7,
                    issue_id="all_devices_offline",
                    execution={
                        "type": "manual",
                        "instruction": "Plug in ESP, watch for HB recovery within 60s",
                    },
                )
            )
        elif fresh < total:
            score -= 4  # some stale, some live — minor

    # IDO countdown — informational, doesn't dent score
    ido = signals.get("ido")
    if ido and isinstance(ido.value, dict):
        days = int(ido.value.get("days_to_target", 0))
        if 0 < days <= 14:
            actions.append(
                Action(
                    id="prepare_ido",
                    label=f"IDO target in {days} days — finalize Marshall Islands LLC + audit",
                    impact="HIGH",
                    confidence=0.6,
                    issue_id=None,
                    execution={
                        "type": "link",
                        "instruction": "/admin/ido-mission-control.html",
                    },
                )
            )

    score = max(0, min(100, score))
    if score >= 85:
        state = "HEALTHY"
    elif score >= 60:
        state = "DEGRADED"
    else:
        state = "CRITICAL"
    return state, score, issues, actions


def _summary(state: str, score: int, issues: List[Issue]) -> str:
    if state == "HEALTHY":
        return f"All systems operational · health {score}/100 · no action required."
    if state == "CRITICAL":
        crit = ", ".join(i.title for i in issues if i.severity == "CRITICAL")
        return f"CRITICAL · health {score}/100 · {crit or 'multiple failures'}"
    titles = ", ".join(i.title for i in issues[:2])
    return f"Degraded · health {score}/100 · {titles or 'minor issues'}"


# ─────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────


@router.get("/state")
async def get_brain_state():
    """Full BrainState JSON. No auth required (read-only system status).

    UI panels and prompt engines call this every 30s to know what to do.
    """
    started = time.monotonic()
    api_s, db_s, dev_s, usr_s, hb_s, ido_s = await asyncio.gather(
        _collect_api_signal(),
        _collect_db_signal(),
        _collect_devices_signal(),
        _collect_users_signal(),
        _collect_heartbeats_24h(),
        _collect_ido_signal(),
    )
    signals = {
        "api": api_s,
        "db": db_s,
        "devices": dev_s,
        "users": usr_s,
        "heartbeats_24h": hb_s,
        "ido": ido_s,
    }
    state, score, issues, actions = _evaluate(signals)
    summary = _summary(state, score, issues)
    confidence = (
        0.85 if state == "HEALTHY" else (0.70 if state == "DEGRADED" else 0.50)
    )
    age_ms = int((time.monotonic() - started) * 1000)

    return {
        "system_state": state,
        "health_score": score,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "signals": {k: v.model_dump() for k, v in signals.items()},
        "critical_issues": [i.model_dump() for i in issues],
        "recommended_actions": [a.model_dump() for a in actions],
        "confidence": confidence,
        "age_ms": age_ms,
    }


@router.get("/health")
async def brain_health():
    """Quick liveness probe."""
    return {"ok": True, "module": "brain", "version": "1.0"}
