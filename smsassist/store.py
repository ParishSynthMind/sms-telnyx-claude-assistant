"""SQLite: contacts (opt-out state), messages (both directions, with status), webhook
deliveries (for idempotency), spend and an event trail."""
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
    phone TEXT PRIMARY KEY,
    opted_out INTEGER NOT NULL DEFAULT 0,
    opted_out_at TEXT,
    handover INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telnyx_id TEXT UNIQUE,
    direction TEXT NOT NULL,          -- outbound | inbound
    phone TEXT NOT NULL,              -- the other party
    text TEXT NOT NULL,
    kind TEXT NOT NULL,               -- template:<name> | ai | keyword | manual | inbound | handover
    segments INTEGER NOT NULL,
    status TEXT NOT NULL,             -- queued, sent, delivered, delivery_failed, blocked, received ...
    reason TEXT,                      -- why it was blocked, or the carrier error
    cost_usd REAL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS webhook_events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    received_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trail (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    event TEXT NOT NULL,
    detail TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()  # one connection shared by the web threads: reads lock too
        with self.lock:
            self.db.executescript(SCHEMA)

    def _all(self, sql: str, args=()) -> list:
        with self.lock:
            return self.db.execute(sql, args).fetchall()

    def _one(self, sql: str, args=()):
        with self.lock:
            return self.db.execute(sql, args).fetchone()

    # contacts
    def contact(self, phone: str) -> dict:
        row = self._one("SELECT * FROM contacts WHERE phone=?", (phone,))
        return dict(row) if row else {"phone": phone, "opted_out": 0, "opted_out_at": None, "handover": 0}

    def is_opted_out(self, phone: str) -> bool:
        return bool(self.contact(phone)["opted_out"])

    def set_opt_out(self, phone: str, opted_out: bool) -> None:
        with self.lock:
            self.db.execute(
                "INSERT INTO contacts(phone, opted_out, opted_out_at, updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(phone) DO UPDATE SET opted_out=excluded.opted_out, "
                "opted_out_at=excluded.opted_out_at, updated_at=excluded.updated_at",
                (phone, int(opted_out), now_iso() if opted_out else None, now_iso()),
            )
            self.db.commit()

    def set_handover(self, phone: str, on: bool) -> None:
        with self.lock:
            self.db.execute(
                "INSERT INTO contacts(phone, handover, updated_at) VALUES(?,?,?) "
                "ON CONFLICT(phone) DO UPDATE SET handover=excluded.handover, updated_at=excluded.updated_at",
                (phone, int(on), now_iso()),
            )
            self.db.commit()

    def contacts(self) -> List[dict]:
        return [dict(r) for r in self._all("SELECT * FROM contacts ORDER BY phone")]

    # messages
    def add_message(self, direction: str, phone: str, text: str, kind: str, segments: int, status: str,
                    telnyx_id: Optional[str] = None, reason: Optional[str] = None,
                    cost_usd: Optional[float] = None) -> int:
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO messages(telnyx_id, direction, phone, text, kind, segments, status, reason, cost_usd, "
                "created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (telnyx_id, direction, phone, text, kind, segments, status, reason, cost_usd, now_iso(), now_iso()),
            )
            self.db.commit()
            return cur.lastrowid

    def message_by_telnyx_id(self, telnyx_id: str) -> Optional[dict]:
        row = self._one("SELECT * FROM messages WHERE telnyx_id=?", (telnyx_id,))
        return dict(row) if row else None

    def update_status(self, telnyx_id: str, status: str, reason: Optional[str] = None,
                      cost_usd: Optional[float] = None, final_statuses: Iterable[str] = ()) -> bool:
        """Set a message's delivery status. A non-final status never overwrites a final
        one: the check and the write are one SQL statement, so two receipts processed at
        the same moment (Telnyx can send them out of order) can't race each other."""
        final = sorted(final_statuses)
        guard = ""
        args = [status, reason, cost_usd, now_iso(), telnyx_id]
        if final and status not in final:
            guard = f" AND status NOT IN ({','.join('?' * len(final))})"
            args += final
        with self.lock:
            cur = self.db.execute(
                "UPDATE messages SET status=?, reason=COALESCE(?, reason), cost_usd=COALESCE(?, cost_usd), "
                "updated_at=? WHERE telnyx_id=?" + guard, args,
            )
            self.db.commit()
            return cur.rowcount > 0

    def messages(self, phone: Optional[str] = None, limit: int = 50) -> List[dict]:
        if phone:
            rows = self._all("SELECT * FROM messages WHERE phone=? ORDER BY id DESC LIMIT ?", (phone, limit))
        else:
            rows = self._all("SELECT * FROM messages ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    def history(self, phone: str, limit: int = 8) -> List[dict]:
        """Recent conversation with one contact, oldest first, for the AI prompt."""
        rows = self._all(
            "SELECT direction, text FROM messages WHERE phone=? AND status != 'blocked' ORDER BY id DESC LIMIT ?",
            (phone, limit),
        )
        return [dict(r) for r in reversed(rows)]

    def outbound_since(self, since_iso: str, phone: Optional[str] = None) -> int:
        q = "SELECT COUNT(*) FROM messages WHERE direction='outbound' AND status != 'blocked' AND created_at >= ?"
        args = [since_iso]
        if phone:
            q += " AND phone=?"
            args.append(phone)
        return self._one(q, args)[0]

    def spend_since(self, since_iso: str, estimate_per_segment: float) -> float:
        """Real cost where Telnyx reported one, otherwise the per-segment estimate."""
        rows = self._all(
            "SELECT segments, cost_usd FROM messages WHERE direction='outbound' AND status != 'blocked' "
            "AND created_at >= ?", (since_iso,),
        )
        return round(sum(r["cost_usd"] if r["cost_usd"] is not None else r["segments"] * estimate_per_segment
                         for r in rows), 6)

    # webhook idempotency
    def first_delivery(self, event_id: str, event_type: str) -> bool:
        """True the first time an event id is seen, False for a retry of the same event."""
        with self.lock:
            try:
                self.db.execute("INSERT INTO webhook_events(event_id, event_type, received_at) VALUES(?,?,?)",
                                (event_id, event_type, now_iso()))
                self.db.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    # trail
    def log(self, _event: str, **detail) -> None:
        with self.lock:
            self.db.execute("INSERT INTO trail(at, event, detail) VALUES(?,?,?)",
                            (now_iso(), _event, json.dumps(detail, sort_keys=True)))
            self.db.commit()

    def trail(self, limit: int = 100) -> List[dict]:
        rows = self._all("SELECT * FROM trail ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) | {"detail": json.loads(r["detail"])} for r in reversed(rows)]
