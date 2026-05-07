"""
SLH Spark — Main API (Railway entrypoint)
==========================================
uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}

Includes:
  - Health / system endpoints
  - ESP32 device endpoints (heartbeat, claim, commands, events)
  - Device inventory management
  - Wallet balance queries
"""
from __future__ import annotations

import os
import logging
import secrets
from contextlib import asynccontextmanager
from typing import Optional, Dict, Any

import asyncpg
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from routes.esp_events import router as esp_events_router, set_pool as esp_set_pool
from routes.device_inventory import router as device_inventory_router, set_pool as inv_set_pool

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("slh-api")

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:slh_secure_2026@postgres:5432/slh_main")

_pool: Optional[asyncpg.Pool] = None


async def _init_firmware_tables(pool: asyncpg.Pool):
    """Create tables the ESP32 firmware needs that aren't covered by esp_events.py."""
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS device_claims (
                id              BIGSERIAL PRIMARY KEY,
                device_id       TEXT UNIQUE NOT NULL,
                paired          BOOLEAN DEFAULT FALSE,
                user_id         BIGINT,
                signing_token   TEXT,
                claimed_at      TIMESTAMPTZ,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE TABLE IF NOT EXISTS esp_commands (
                id              BIGSERIAL PRIMARY KEY,
                device_id       TEXT NOT NULL,
                command         TEXT NOT NULL,
                executed        BOOLEAN DEFAULT FALSE,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_esp_commands_device
                ON esp_commands(device_id, executed, created_at DESC);
            CREATE TABLE IF NOT EXISTS wallet_balances (
                user_id         BIGINT PRIMARY KEY,
                slh             NUMERIC DEFAULT 0,
                mnh             NUMERIC DEFAULT 0,
                zvk             NUMERIC DEFAULT 0,
                rep             NUMERIC DEFAULT 0,
                zuz             NUMERIC DEFAULT 0,
                total_value_ils NUMERIC DEFAULT 0,
                total_value_usd NUMERIC DEFAULT 0,
                updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
    log.info("Firmware tables ready")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _pool
    log.info("Connecting to PostgreSQL...")
    _pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
    esp_set_pool(_pool)
    inv_set_pool(_pool)
    await _init_firmware_tables(_pool)
    log.info("DB pool ready")
    yield
    await _pool.close()
    log.info("DB pool closed")


app = FastAPI(
    title="SLH Spark API",
    version="2.0",
    description="SLH Ecosystem — ESP32 + Wallet + Admin",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(esp_events_router)
app.include_router(device_inventory_router)


# ── Health ─────────────────────────────────────────────────────────────────────

@app.get("/health")
@app.get("/api/health")
async def health():
    db_ok = False
    if _pool:
        try:
            async with _pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            db_ok = True
        except Exception:
            pass
    return {"status": "ok", "db": "ok" if db_ok else "error"}


# ── ESP32 Heartbeat (firmware calls POST /api/esp/heartbeat) ───────────────────

class HeartbeatIn(BaseModel):
    device_id: str
    fw: Optional[str] = None
    ssid: Optional[str] = None
    rssi: Optional[int] = None
    ip: Optional[str] = None
    uptime_seconds: Optional[int] = None
    free_heap: Optional[int] = None


@app.post("/api/esp/heartbeat")
async def esp_heartbeat(req: HeartbeatIn, authorization: Optional[str] = Header(None)):
    """ESP32 sends this every 30 seconds."""
    if not _pool:
        raise HTTPException(503, "DB not ready")
    async with _pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO esp_devices (device_id, status, last_seen)
            VALUES ($1, 'up', NOW())
            ON CONFLICT (device_id)
            DO UPDATE SET status='up', last_seen=NOW()
        """, req.device_id)
    return {"ok": True}


# ── Device Claim (firmware calls GET /api/device/claim/{deviceId}) ─────────────

@app.get("/api/device/claim/{device_id}")
async def device_claim(device_id: str):
    """ESP32 polls this during pairing. Returns paired=true + signing_token once claimed."""
    if not _pool:
        raise HTTPException(503, "DB not ready")
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM device_claims WHERE device_id=$1", device_id
        )
        if not row:
            await conn.execute("""
                INSERT INTO device_claims (device_id, paired)
                VALUES ($1, FALSE)
                ON CONFLICT (device_id) DO NOTHING
            """, device_id)
            return {"paired": False}
        if row["paired"]:
            return {
                "paired": True,
                "signing_token": row["signing_token"],
                "user_id": row["user_id"],
            }
    return {"paired": False}


@app.post("/api/device/claim/{device_id}")
async def device_claim_pair(device_id: str, request: Request):
    """Website calls this to pair a device to a user (from device-pair.html)."""
    if not _pool:
        raise HTTPException(503, "DB not ready")
    body = await request.json()
    user_id = body.get("user_id")
    if not user_id:
        raise HTTPException(400, "user_id required")
    token = secrets.token_urlsafe(48)
    async with _pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO device_claims (device_id, paired, user_id, signing_token, claimed_at)
            VALUES ($1, TRUE, $2, $3, NOW())
            ON CONFLICT (device_id)
            DO UPDATE SET paired=TRUE, user_id=$2, signing_token=$3, claimed_at=NOW()
        """, device_id, int(user_id), token)
    return {"ok": True, "signing_token": token}


# ── ESP Commands (firmware calls GET /api/esp/commands/{deviceId}) ──────────────

@app.get("/api/esp/commands/{device_id}")
async def esp_commands(device_id: str, authorization: Optional[str] = Header(None)):
    """ESP32 polls this every 15 seconds. Returns next unexecuted command."""
    if not _pool:
        raise HTTPException(503, "DB not ready")
    async with _pool.acquire() as conn:
        row = await conn.fetchrow("""
            UPDATE esp_commands
            SET executed = TRUE
            WHERE id = (
                SELECT id FROM esp_commands
                WHERE device_id=$1 AND executed=FALSE
                ORDER BY created_at ASC LIMIT 1
            )
            RETURNING command
        """, device_id)
    if row:
        return {"command": row["command"]}
    return {"command": ""}


@app.post("/api/esp/commands/{device_id}")
async def esp_send_command(device_id: str, request: Request,
                           x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key")):
    """Admin sends a command to an ESP32 device."""
    body = await request.json()
    cmd = body.get("command", "")
    if not cmd:
        raise HTTPException(400, "command required")
    if not _pool:
        raise HTTPException(503, "DB not ready")
    async with _pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO esp_commands (device_id, command)
            VALUES ($1, $2)
        """, device_id, cmd)
    return {"ok": True, "device_id": device_id, "command": cmd}


# ── Wallet Balances (firmware calls GET /api/wallet/{userId}/balances) ─────────

@app.get("/api/wallet/{user_id}/balances")
async def wallet_balances(user_id: int):
    """Return token balances for a user. ESP32 displays these on HOME/WALLET screens."""
    if not _pool:
        raise HTTPException(503, "DB not ready")
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM wallet_balances WHERE user_id=$1", user_id
        )
    if not row:
        return {
            "balances": {"SLH": 0, "MNH": 0, "ZVK": 0, "REP": 0, "ZUZ": 0},
            "total_value_ils": 0,
            "total_value_usd": 0,
        }
    return {
        "balances": {
            "SLH": float(row["slh"]),
            "MNH": float(row["mnh"]),
            "ZVK": float(row["zvk"]),
            "REP": float(row["rep"]),
            "ZUZ": float(row["zuz"]),
        },
        "total_value_ils": float(row["total_value_ils"]),
        "total_value_usd": float(row["total_value_usd"]),
    }


# ── Device Register (for device-pair.html flow) ───────────────────────────────

class DeviceRegisterIn(BaseModel):
    device_id: str
    mac_address: Optional[str] = None
    phone: Optional[str] = None


@app.post("/api/device/register")
async def device_register(req: DeviceRegisterIn):
    """First step of pairing: device registers itself with MAC."""
    if not _pool:
        raise HTTPException(503, "DB not ready")
    async with _pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO device_claims (device_id, paired)
            VALUES ($1, FALSE)
            ON CONFLICT (device_id) DO NOTHING
        """, req.device_id)
    return {"ok": True, "device_id": req.device_id}


@app.post("/api/device/verify")
async def device_verify(request: Request):
    """Verify step of pairing (simplified — no SMS for now)."""
    body = await request.json()
    device_id = body.get("device_id")
    code = body.get("code")
    if not device_id:
        raise HTTPException(400, "device_id required")
    if not _pool:
        raise HTTPException(503, "DB not ready")
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM device_claims WHERE device_id=$1", device_id
        )
    if not row:
        raise HTTPException(404, "Device not found")
    if row["paired"]:
        return {"verified": True, "signing_token": row["signing_token"], "user_id": row["user_id"]}
    return {"verified": False, "message": "Device not yet claimed by a user"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
