"""Persistent simulated cash ledger for BTC paper trades."""
from __future__ import annotations

import json
import math
from typing import Literal

import aiosqlite

from db import connect, utc_now_iso

TransactionType = Literal["BUY", "SELL", "SETTLEMENT"]


async def apply_cash_flow(
    db: aiosqlite.Connection,
    *,
    position_id: int,
    window_slug: str,
    transaction_type: TransactionType,
    amount_usd: float,
    details: dict[str, object] | None = None,
) -> float | None:
    """Apply one cash movement inside the caller's SQLite transaction.

    BUY amounts are debited; SELL and SETTLEMENT amounts are credited. ``None``
    means the wallet is missing, cash is insufficient, or the position has no
    wallet-funded BUY.
    """
    amount = round(float(amount_usd), 4)
    if not math.isfinite(amount) or amount < 0:
        raise ValueError("paper wallet cash flow must be a finite non-negative amount")
    if transaction_type == "BUY" and amount <= 0:
        raise ValueError("paper wallet BUY debit must be greater than zero")
    if transaction_type not in {"BUY", "SELL", "SETTLEMENT"}:
        raise ValueError(f"unsupported paper wallet transaction: {transaction_type}")

    if transaction_type == "BUY":
        cursor = await db.execute(
            """
            UPDATE paper_wallet
               SET cash_usd = ROUND(cash_usd - ?, 4)
             WHERE wallet_id = 1 AND ROUND(cash_usd, 4) >= ?
            """,
            (amount, amount),
        )
    else:
        async with db.execute(
            """
            SELECT 1 FROM paper_wallet_transactions
             WHERE position_id = ? AND transaction_type = 'BUY'
            """,
            (position_id,),
        ) as cursor:
            if await cursor.fetchone() is None:
                return None
        cursor = await db.execute(
            """
            UPDATE paper_wallet
               SET cash_usd = ROUND(cash_usd + ?, 4)
             WHERE wallet_id = 1
            """,
            (amount,),
        )

    if cursor.rowcount != 1:
        return None
    async with db.execute(
        "SELECT cash_usd FROM paper_wallet WHERE wallet_id = 1"
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return None
    cash_after = float(row["cash_usd"])
    await db.execute(
        """
        INSERT INTO paper_wallet_transactions(
          created_at, position_id, window_slug, transaction_type,
          amount_usd, cash_after_usd, details_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            utc_now_iso(),
            position_id,
            window_slug,
            transaction_type,
            -amount if transaction_type == "BUY" else amount,
            cash_after,
            json.dumps(details or {}, sort_keys=True),
        ),
    )
    return cash_after


async def has_entry_transaction(
    db: aiosqlite.Connection, position_id: int
) -> bool:
    async with db.execute(
        """
        SELECT 1 FROM paper_wallet_transactions
         WHERE position_id = ? AND transaction_type = 'BUY'
        """,
        (position_id,),
    ) as cursor:
        return await cursor.fetchone() is not None


async def cash_balance() -> float:
    async with connect() as db:
        async with db.execute(
            "SELECT cash_usd FROM paper_wallet WHERE wallet_id = 1"
        ) as cursor:
            row = await cursor.fetchone()
    if row is None:
        raise RuntimeError("paper wallet is not initialized; initialize the database first")
    return float(row["cash_usd"])
