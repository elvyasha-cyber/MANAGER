from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.schemas import ClientRecord, TicketRecord, TriageIn, TriageOut
from app.seed import DEMO_CUSTOMERS


class Database:
    def __init__(self, path: str, seed_demo: bool = True) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self.init(seed_demo=seed_demo)

    def init(self, seed_demo: bool = True) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS clients (
                    client_id TEXT PRIMARY KEY,
                    name TEXT,
                    email TEXT,
                    phone TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tickets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    client_id TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    client_name TEXT,
                    client_email TEXT,
                    client_phone TEXT,
                    order_number TEXT,
                    text TEXT,
                    category TEXT,
                    confidence TEXT,
                    escalate INTEGER,
                    draft_reply TEXT,
                    error TEXT
                )
                """
            )
            ticket_cols = {
                row[1]
                for row in self._conn.execute("PRAGMA table_info(tickets)").fetchall()
            }
            for name in ("text", "client_name", "client_email", "client_phone", "order_number"):
                if name not in ticket_cols:
                    self._conn.execute(f"ALTER TABLE tickets ADD COLUMN {name} TEXT")
            if seed_demo:
                self._seed_demo_locked()
            self._conn.commit()

    def _seed_demo_locked(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        for item in DEMO_CUSTOMERS:
            exists = self._conn.execute(
                "SELECT 1 FROM clients WHERE client_id = ?",
                (item["client_id"],),
            ).fetchone()
            if exists:
                continue
            self._conn.execute(
                """
                INSERT INTO clients (client_id, name, email, phone, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (item["client_id"], item["name"], item["email"], item["phone"], now),
            )
            self._conn.execute(
                """
                INSERT INTO tickets (
                    created_at, client_id, channel,
                    client_name, client_email, client_phone, order_number, text,
                    category, confidence, escalate, draft_reply, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now,
                    item["client_id"],
                    item["channel"],
                    item["name"],
                    item["email"],
                    item["phone"],
                    item["order_number"],
                    item["text"],
                    item["category"],
                    "high",
                    0,
                    item["draft_reply"],
                    None,
                ),
            )

    @staticmethod
    def _digits(value: str | None) -> str:
        if not value:
            return ""
        return "".join(ch for ch in value if ch.isdigit())

    @staticmethod
    def _norm_name(value: str | None) -> str:
        if not value:
            return ""
        text = value.replace("ё", "е").replace("Ё", "Е").casefold()
        return " ".join(text.split())

    def _names_match(self, left: str | None, right: str | None) -> bool:
        a = self._norm_name(left)
        b = self._norm_name(right)
        if not a or not b:
            return False
        if a == b:
            return True
        ta, tb = a.split(), b.split()
        return len(ta) >= 2 and len(tb) >= 2 and set(ta) == set(tb)

    def _client_from_row(self, row: sqlite3.Row | None) -> ClientRecord | None:
        if row is None:
            return None
        return ClientRecord(
            client_id=row["client_id"],
            name=row["name"],
            email=row["email"],
            phone=row["phone"],
            updated_at=row["updated_at"],
        )

    def _last_order_for(self, client_id: str) -> str | None:
        row = self._conn.execute(
            """
            SELECT order_number FROM tickets
            WHERE client_id = ? AND order_number IS NOT NULL AND trim(order_number) != ''
            ORDER BY id DESC LIMIT 1
            """,
            (client_id,),
        ).fetchone()
        return row["order_number"] if row else None

    def find_related_client(self, payload: TriageIn) -> ClientRecord | None:
        with self._lock:
            rows = self._conn.execute(
                "SELECT client_id, name, email, phone, updated_at FROM clients"
            ).fetchall()
            clients = [self._client_from_row(row) for row in rows]
            clients = [item for item in clients if item is not None]

            if payload.client_email:
                want = payload.client_email.casefold()
                for item in clients:
                    if (item.email or "").casefold() == want:
                        return item
            if payload.client_phone:
                want = self._digits(payload.client_phone)
                if want:
                    for item in clients:
                        if self._digits(item.phone) == want:
                            return item
            if payload.order_number:
                ticket = self._conn.execute(
                    """
                    SELECT client_id FROM tickets
                    WHERE order_number = ?
                    ORDER BY id DESC LIMIT 1
                    """,
                    (payload.order_number,),
                ).fetchone()
                if ticket:
                    for item in clients:
                        if item.client_id == ticket["client_id"]:
                            return item
            if payload.client_name:
                for item in clients:
                    if self._names_match(payload.client_name, item.name):
                        return item
                ticket = self._conn.execute(
                    """
                    SELECT client_id, client_name FROM tickets
                    WHERE client_name IS NOT NULL
                    ORDER BY id DESC
                    """
                ).fetchall()
                for row in ticket:
                    if self._names_match(payload.client_name, row["client_name"]):
                        for item in clients:
                            if item.client_id == row["client_id"]:
                                return item
            if payload.client_id:
                for item in clients:
                    if item.client_id == payload.client_id:
                        return item
        return None

    def infer_client_id(self, payload: TriageIn) -> str:
        if payload.client_email:
            return payload.client_email.casefold()
        if self._digits(payload.client_phone):
            return self._digits(payload.client_phone)
        if payload.order_number:
            return f"order:{payload.order_number}"
        if payload.client_name:
            return f"name:{self._norm_name(payload.client_name)}"
        if payload.client_id:
            return payload.client_id
        return "аноним"

    def enrich_payload(self, payload: TriageIn) -> TriageIn:
        found = self.find_related_client(payload)
        if not found:
            return payload.model_copy(update={"client_id": self.infer_client_id(payload)})
        order_number = payload.order_number
        if not order_number:
            with self._lock:
                order_number = self._last_order_for(found.client_id)
        return payload.model_copy(
            update={
                "client_id": found.client_id,
                "client_name": payload.client_name or found.name,
                "client_email": payload.client_email or found.email,
                "client_phone": payload.client_phone or found.phone,
                "order_number": order_number,
            }
        )

    def upsert_client(self, payload: TriageIn) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO clients (client_id, name, email, phone, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(client_id) DO UPDATE SET
                    name = COALESCE(excluded.name, clients.name),
                    email = COALESCE(excluded.email, clients.email),
                    phone = COALESCE(excluded.phone, clients.phone),
                    updated_at = excluded.updated_at
                """,
                (
                    payload.client_id,
                    payload.client_name,
                    payload.client_email,
                    payload.client_phone,
                    now,
                ),
            )
            self._conn.commit()

    def insert_ticket(
        self,
        *,
        payload: TriageIn,
        result: TriageOut,
        error: str | None = None,
    ) -> int:
        self.upsert_client(payload)
        created_at = datetime.now(timezone.utc).isoformat()
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO tickets (
                    created_at, client_id, channel,
                    client_name, client_email, client_phone, order_number, text,
                    category, confidence, escalate, draft_reply, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    created_at,
                    payload.client_id,
                    payload.channel,
                    payload.client_name,
                    payload.client_email,
                    payload.client_phone,
                    payload.order_number,
                    payload.text,
                    result.category,
                    result.confidence,
                    int(result.escalate),
                    result.draft_reply,
                    error,
                ),
            )
            self._conn.commit()
            return int(cursor.lastrowid)

    def list_tickets(self, limit: int = 50) -> list[TicketRecord]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, created_at, client_id, channel,
                       client_name, client_email, client_phone, order_number, text,
                       category, confidence, escalate, draft_reply, error
                FROM tickets
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._ticket_from_row(row) for row in rows]

    def list_clients(self, limit: int = 50) -> list[ClientRecord]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT client_id, name, email, phone, updated_at
                FROM clients
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            ClientRecord(
                client_id=row["client_id"],
                name=row["name"],
                email=row["email"],
                phone=row["phone"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def count_tickets(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM tickets").fetchone()
            return int(row["n"])

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @staticmethod
    def _ticket_from_row(row: sqlite3.Row) -> TicketRecord:
        keys = row.keys()
        return TicketRecord(
            id=row["id"],
            created_at=row["created_at"],
            client_id=row["client_id"],
            channel=row["channel"],
            client_name=row["client_name"] if "client_name" in keys else None,
            client_email=row["client_email"] if "client_email" in keys else None,
            client_phone=row["client_phone"] if "client_phone" in keys else None,
            order_number=row["order_number"] if "order_number" in keys else None,
            text=row["text"] if "text" in keys else None,
            category=row["category"],
            confidence=row["confidence"],
            escalate=bool(row["escalate"]) if row["escalate"] is not None else None,
            draft_reply=row["draft_reply"],
            error=row["error"],
        )
