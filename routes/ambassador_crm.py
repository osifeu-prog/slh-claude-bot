"""
Ambassador CRM — Phase 0 (MVP).

Purpose: give SLH ambassadors (users who recruit investors to the platform)
a first-party CRM for their pipeline. Think Eliezer with 130 contacts —
he needs names, phones, notes, status, last-contact date, commitment amount.

Scope: contact management only. NO money flow through SLH for now (requires
legal entity + licensing before ambassador-mediated investments). When legal
clears → add deposits/commissions/payouts as a separate module.

Table: ambassador_contacts — created idempotently on first API call.

Auth: X-Admin-Key header (admin-only). Each ambassador is identified by
their Telegram ID as a query/body param; admin (Osif) can query any
ambassador's data. Future: per-ambassador JWT for self-service web dashboard.

Endpoints:
  POST   /api/ambassador/contacts             — create a single contact
  GET    /api/ambassador/contacts             — list with filters + pagination
  PATCH  /api/ambassador/contacts/{id}        — update fields
  POST   /api/ambassador/contacts/import      — bulk CSV import
  GET    /api/ambassador/stats/{amb_id}       — pipeline summary
"""
from __future__ import annotations

import csv
import io
import os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

router = APIRouter(prefix="/api/ambassador", tags=["ambassador-crm"])

_pool = None


def set_pool(pool):
    global _pool
    _pool = pool


# ── auth (same ADMIN_API_KEYS env pattern used across the repo) ──
_ADMIN_KEYS = {
    k.strip() for k in os.getenv("ADMIN_API_KEYS", "").split(",") if k.strip()
}


def _require_admin_key(x_admin_key: Optional[str]) -> None:
    if not x_admin_key or x_admin_key not in _ADMIN_KEYS:
        raise HTTPException(403, "Admin key required (X-Admin-Key header)")


# ── DB init (idempotent) ──
async def _ensure_table(conn) -> None:
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS ambassador_contacts (
            id             BIGSERIAL PRIMARY KEY,
            ambassador_id  BIGINT    NOT NULL,
            name           TEXT      NOT NULL,
            phone          TEXT,
            telegram_id    BIGINT,
            email          TEXT,
            status         TEXT      NOT NULL DEFAULT 'lead',
            notes          TEXT,
            last_contact   TIMESTAMP,
            amount_ils     NUMERIC(14,2) DEFAULT 0,
            tags           TEXT[]    DEFAULT ARRAY[]::TEXT[],
            deleted_at     TIMESTAMP,
            created_at     TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at     TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_amb_contacts_owner "
        "ON ambassador_contacts (ambassador_id) WHERE deleted_at IS NULL"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_amb_contacts_status "
        "ON ambassador_contacts (ambassador_id, status) WHERE deleted_at IS NULL"
    )


VALID_STATUSES = {"lead", "qualified", "committed", "funded", "lost"}


def _serialize(row) -> dict:
    return {
        "id":            row["id"],
        "ambassador_id": row["ambassador_id"],
        "name":          row["name"],
        "phone":         row["phone"],
        "telegram_id":   row["telegram_id"],
        "email":         row["email"],
        "status":        row["status"],
        "notes":         row["notes"],
        "last_contact":  row["last_contact"].isoformat() if row["last_contact"] else None,
        "amount_ils":    float(row["amount_ils"]) if row["amount_ils"] is not None else 0.0,
        "tags":          list(row["tags"] or []),
        "created_at":    row["created_at"].isoformat(),
        "updated_at":    row["updated_at"].isoformat(),
    }


# ── models ──
class ContactCreate(BaseModel):
    ambassador_id: int
    name: str
    phone: Optional[str] = None
    telegram_id: Optional[int] = None
    email: Optional[str] = None
    status: Optional[str] = "lead"
    notes: Optional[str] = None
    amount_ils: Optional[float] = 0


class ContactUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    telegram_id: Optional[int] = None
    email: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None
    last_contact: Optional[datetime] = None
    amount_ils: Optional[float] = None


# ── endpoints ──
@router.post("/contacts")
async def create_contact(
    req: ContactCreate,
    x_admin_key: Optional[str] = Header(None),
):
    """Create a single contact for an ambassador's pipeline."""
    _require_admin_key(x_admin_key)
    if req.status and req.status not in VALID_STATUSES:
        raise HTTPException(400, f"status must be one of {sorted(VALID_STATUSES)}")

    async with _pool.acquire() as conn:
        await _ensure_table(conn)
        row = await conn.fetchrow("""
            INSERT INTO ambassador_contacts
                (ambassador_id, name, phone, telegram_id, email, status, notes, amount_ils)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING *
        """,
            req.ambassador_id, req.name, req.phone, req.telegram_id, req.email,
            req.status or "lead", req.notes, req.amount_ils or 0,
        )
    return {"ok": True, "contact": _serialize(row)}


@router.get("/contacts")
async def list_contacts(
    ambassador_id: int = Query(..., description="Ambassador's Telegram ID"),
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None, description="Substring match on name/phone/notes"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    x_admin_key: Optional[str] = Header(None),
):
    """List an ambassador's contacts with optional filters + pagination."""
    _require_admin_key(x_admin_key)

    conds = ["ambassador_id = $1", "deleted_at IS NULL"]
    params: list = [ambassador_id]

    if status:
        params.append(status)
        conds.append(f"status = ${len(params)}")

    if search:
        params.append(f"%{search}%")
        like_placeholder = f"${len(params)}"
        conds.append(
            f"(name ILIKE {like_placeholder} OR phone ILIKE {like_placeholder} "
            f"OR notes ILIKE {like_placeholder})"
        )

    where_sql = " AND ".join(conds)
    filter_params = params.copy()
    params.extend([limit, offset])

    async with _pool.acquire() as conn:
        await _ensure_table(conn)
        rows = await conn.fetch(
            f"SELECT * FROM ambassador_contacts WHERE {where_sql} "
            f"ORDER BY created_at DESC "
            f"LIMIT ${len(params) - 1} OFFSET ${len(params)}",
            *params,
        )
        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM ambassador_contacts WHERE {where_sql}",
            *filter_params,
        )

    return {
        "contacts": [_serialize(r) for r in rows],
        "total":    total,
        "limit":    limit,
        "offset":   offset,
    }


@router.patch("/contacts/{contact_id}")
async def update_contact(
    contact_id: int,
    req: ContactUpdate,
    x_admin_key: Optional[str] = Header(None),
):
    """Update specific fields. Only non-null fields are touched."""
    _require_admin_key(x_admin_key)
    if req.status and req.status not in VALID_STATUSES:
        raise HTTPException(400, f"status must be one of {sorted(VALID_STATUSES)}")

    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(400, "no fields to update")

    set_parts = [f"{col} = ${i+1}" for i, col in enumerate(updates.keys())]
    set_parts.append("updated_at = NOW()")
    set_sql = ", ".join(set_parts)
    params = list(updates.values()) + [contact_id]

    async with _pool.acquire() as conn:
        await _ensure_table(conn)
        row = await conn.fetchrow(
            f"UPDATE ambassador_contacts SET {set_sql} "
            f"WHERE id = ${len(params)} AND deleted_at IS NULL RETURNING *",
            *params,
        )
    if not row:
        raise HTTPException(404, "contact not found or already deleted")
    return {"ok": True, "contact": _serialize(row)}


@router.post("/contacts/import")
async def import_contacts_csv(
    ambassador_id: int = Form(..., description="Ambassador's Telegram ID"),
    file: UploadFile = File(..., description="CSV with columns: name (required), phone, telegram_id, email, status, notes, amount_ils"),
    x_admin_key: Optional[str] = Header(None),
):
    """Bulk-import contacts from a CSV. Only 'name' is required per row."""
    _require_admin_key(x_admin_key)

    body = await file.read()
    if not body:
        raise HTTPException(400, "empty file")
    text = body.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))

    if not reader.fieldnames:
        raise HTTPException(400, "CSV has no header row")
    fieldnames_lower = {c.strip().lower() for c in reader.fieldnames}
    if "name" not in fieldnames_lower:
        raise HTTPException(400, "CSV must have a 'name' column")

    inserted = 0
    errors: list[dict] = []

    async with _pool.acquire() as conn:
        await _ensure_table(conn)
        async with conn.transaction():
            for idx, raw in enumerate(reader, start=2):  # row 1 = headers
                row = {
                    (k or "").strip().lower(): (v or "").strip()
                    for k, v in raw.items()
                }
                name = row.get("name", "").strip()
                if not name:
                    errors.append({"row": idx, "error": "missing name"})
                    continue

                status = row.get("status") or "lead"
                if status not in VALID_STATUSES:
                    status = "lead"

                try:
                    tg = int(row["telegram_id"]) if row.get("telegram_id") else None
                except (ValueError, TypeError):
                    tg = None
                try:
                    amt = float(row["amount_ils"]) if row.get("amount_ils") else 0
                except (ValueError, TypeError):
                    amt = 0

                await conn.execute("""
                    INSERT INTO ambassador_contacts
                        (ambassador_id, name, phone, telegram_id, email, status, notes, amount_ils)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                    ambassador_id, name, row.get("phone") or None, tg,
                    row.get("email") or None, status, row.get("notes") or None, amt,
                )
                inserted += 1

    return {"ok": True, "inserted": inserted, "errors": errors}


@router.get("/stats/{ambassador_id}")
async def pipeline_stats(
    ambassador_id: int,
    x_admin_key: Optional[str] = Header(None),
):
    """Pipeline summary — count + total amount per status."""
    _require_admin_key(x_admin_key)

    async with _pool.acquire() as conn:
        await _ensure_table(conn)
        rows = await conn.fetch("""
            SELECT status,
                   COUNT(*)                     AS cnt,
                   COALESCE(SUM(amount_ils), 0) AS total
            FROM ambassador_contacts
            WHERE ambassador_id = $1 AND deleted_at IS NULL
            GROUP BY status
        """, ambassador_id)

    by_status = {
        r["status"]: {"count": r["cnt"], "total_ils": float(r["total"])}
        for r in rows
    }
    total_cnt = sum(v["count"] for v in by_status.values())
    total_amt = sum(v["total_ils"] for v in by_status.values())
    return {
        "ambassador_id":  ambassador_id,
        "by_status":      by_status,
        "total_contacts": total_cnt,
        "total_ils":      total_amt,
    }


# ════════════════════════════════════════════════════════════════════════════
# ESP pre-sale tracking
#
# Eliezer (and any ambassador) sells ESP32 devices pre-launch in cash. This
# module tracks each sale: who bought, how many devices, how much paid,
# when delivered. CSV export is available for offline reconciliation.
#
# Distinct from ambassador_contacts (lead pipeline) — these are settled
# cash transactions, not lead-stage data.
# ════════════════════════════════════════════════════════════════════════════


async def _ensure_esp_presale_table(conn) -> None:
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS esp_presales (
            id              BIGSERIAL PRIMARY KEY,
            ambassador_id   BIGINT     NOT NULL,
            customer_name   TEXT       NOT NULL,
            customer_phone  TEXT,
            customer_telegram_id BIGINT,
            devices_sold    INT        NOT NULL DEFAULT 1,
            amount_paid_ils NUMERIC(12,2) NOT NULL,
            payment_method  TEXT       NOT NULL DEFAULT 'cash',
            payment_date    DATE       NOT NULL DEFAULT CURRENT_DATE,
            delivered       BOOLEAN    NOT NULL DEFAULT FALSE,
            delivered_at    TIMESTAMP,
            device_serials  TEXT[]     DEFAULT ARRAY[]::TEXT[],
            notes           TEXT,
            deleted_at      TIMESTAMP,
            created_at      TIMESTAMP  NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMP  NOT NULL DEFAULT NOW()
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_esp_presales_ambassador "
        "ON esp_presales (ambassador_id) WHERE deleted_at IS NULL"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_esp_presales_date "
        "ON esp_presales (payment_date DESC) WHERE deleted_at IS NULL"
    )


VALID_PAYMENT_METHODS = {"cash", "bit", "paybox", "bank_transfer", "crypto", "other"}


def _serialize_esp(row) -> dict:
    return {
        "id":                   row["id"],
        "ambassador_id":        row["ambassador_id"],
        "customer_name":        row["customer_name"],
        "customer_phone":       row["customer_phone"],
        "customer_telegram_id": row["customer_telegram_id"],
        "devices_sold":         row["devices_sold"],
        "amount_paid_ils":      float(row["amount_paid_ils"]),
        "payment_method":       row["payment_method"],
        "payment_date":         row["payment_date"].isoformat() if row["payment_date"] else None,
        "delivered":            row["delivered"],
        "delivered_at":         row["delivered_at"].isoformat() if row["delivered_at"] else None,
        "device_serials":       list(row["device_serials"] or []),
        "notes":                row["notes"],
        "created_at":           row["created_at"].isoformat(),
        "updated_at":           row["updated_at"].isoformat(),
    }


class EspPresaleCreate(BaseModel):
    ambassador_id:   int
    customer_name:   str
    customer_phone:  Optional[str] = None
    customer_telegram_id: Optional[int] = None
    devices_sold:    int = 1
    amount_paid_ils: float
    payment_method:  Optional[str] = "cash"
    payment_date:    Optional[str] = None  # ISO date; defaults to today
    delivered:       Optional[bool] = False
    device_serials:  Optional[list] = None
    notes:           Optional[str] = None


@router.post("/esp-presale")
async def create_esp_presale(
    req: EspPresaleCreate,
    x_admin_key: Optional[str] = Header(None),
):
    """Record a single ESP32 pre-sale. Cash + receipt-style data only."""
    _require_admin_key(x_admin_key)

    if req.payment_method and req.payment_method not in VALID_PAYMENT_METHODS:
        raise HTTPException(
            400, f"payment_method must be one of {sorted(VALID_PAYMENT_METHODS)}"
        )
    if req.devices_sold < 1:
        raise HTTPException(400, "devices_sold must be >= 1")
    if req.amount_paid_ils <= 0:
        raise HTTPException(400, "amount_paid_ils must be > 0")

    pay_date = req.payment_date or datetime.now().date().isoformat()
    serials = req.device_serials or []

    async with _pool.acquire() as conn:
        await _ensure_esp_presale_table(conn)
        row = await conn.fetchrow(
            """
            INSERT INTO esp_presales (
                ambassador_id, customer_name, customer_phone, customer_telegram_id,
                devices_sold, amount_paid_ils, payment_method, payment_date,
                delivered, device_serials, notes
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::date, $9, $10, $11)
            RETURNING *
            """,
            req.ambassador_id, req.customer_name, req.customer_phone,
            req.customer_telegram_id, req.devices_sold, req.amount_paid_ils,
            req.payment_method or "cash", pay_date, bool(req.delivered),
            serials, req.notes,
        )
    return {"ok": True, "presale": _serialize_esp(row)}


@router.get("/esp-presales/{ambassador_id}")
async def list_esp_presales(
    ambassador_id: int,
    delivered: Optional[bool] = Query(None, description="filter delivered status"),
    x_admin_key: Optional[str] = Header(None),
):
    """List all ESP pre-sales for an ambassador, with summary totals."""
    _require_admin_key(x_admin_key)

    conds = ["ambassador_id = $1", "deleted_at IS NULL"]
    params: list = [ambassador_id]
    if delivered is not None:
        conds.append(f"delivered = ${len(params) + 1}")
        params.append(delivered)
    where_sql = " AND ".join(conds)

    async with _pool.acquire() as conn:
        await _ensure_esp_presale_table(conn)
        rows = await conn.fetch(
            f"""
            SELECT * FROM esp_presales WHERE {where_sql}
            ORDER BY payment_date DESC, created_at DESC
            """,
            *params,
        )
        totals = await conn.fetchrow(
            f"""
            SELECT COALESCE(SUM(devices_sold), 0)    AS devices_total,
                   COALESCE(SUM(amount_paid_ils), 0) AS amount_total,
                   COUNT(*)                          AS sale_count,
                   COUNT(*) FILTER (WHERE delivered) AS delivered_count
            FROM esp_presales WHERE {where_sql}
            """,
            *params,
        )

    return {
        "ambassador_id": ambassador_id,
        "presales": [_serialize_esp(r) for r in rows],
        "summary": {
            "sale_count":      totals["sale_count"],
            "devices_total":   totals["devices_total"],
            "amount_total_ils": float(totals["amount_total"]),
            "delivered_count": totals["delivered_count"],
            "pending_delivery": totals["sale_count"] - totals["delivered_count"],
        },
    }


@router.get("/esp-presales/{ambassador_id}/export.csv")
async def export_esp_presales_csv(
    ambassador_id: int,
    x_admin_key: Optional[str] = Header(None),
):
    """Export this ambassador's ESP pre-sales as a CSV file.

    Returns text/csv with proper Content-Disposition for download. Useful for
    offline reconciliation, accountant handoff, tax reporting.
    """
    _require_admin_key(x_admin_key)

    async with _pool.acquire() as conn:
        await _ensure_esp_presale_table(conn)
        rows = await conn.fetch(
            """
            SELECT * FROM esp_presales
            WHERE ambassador_id = $1 AND deleted_at IS NULL
            ORDER BY payment_date DESC, created_at DESC
            """,
            ambassador_id,
        )

    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
    writer.writerow([
        "id", "payment_date", "customer_name", "customer_phone",
        "customer_telegram_id", "devices_sold", "amount_paid_ils",
        "payment_method", "delivered", "delivered_at",
        "device_serials", "notes", "created_at",
    ])
    for r in rows:
        writer.writerow([
            r["id"],
            r["payment_date"].isoformat() if r["payment_date"] else "",
            r["customer_name"] or "",
            r["customer_phone"] or "",
            r["customer_telegram_id"] or "",
            r["devices_sold"],
            f"{float(r['amount_paid_ils']):.2f}",
            r["payment_method"] or "",
            "yes" if r["delivered"] else "no",
            r["delivered_at"].isoformat() if r["delivered_at"] else "",
            ";".join(r["device_serials"] or []),
            (r["notes"] or "").replace("\n", " "),
            r["created_at"].isoformat(),
        ])

    csv_body = buf.getvalue()
    # UTF-8 BOM so Excel opens Hebrew correctly
    csv_with_bom = "﻿" + csv_body
    filename = f"esp-presales-{ambassador_id}-{datetime.now().date().isoformat()}.csv"
    return Response(
        content=csv_with_bom.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
