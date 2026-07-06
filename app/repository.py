"""SQLite-Datenzugriff über aiosqlite."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import aiosqlite

from app.exceptions import DatabaseError
from app.logging_config import get_logger, truncate_id

logger = get_logger(__name__)

MESSAGE_STATUSES = frozenset({"seen", "queued", "processing", "completed", "failed", "ignored"})


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


class Repository:
    """Datenzugriffsschicht für SQLite."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        """Stellt die Datenbankverbindung her und initialisiert Tabellen."""
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._initialize_schema()

    async def close(self) -> None:
        """Schließt die Datenbankverbindung."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    def _get_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise DatabaseError("Datenbankverbindung ist nicht hergestellt.")
        return self._conn

    async def _initialize_schema(self) -> None:
        conn = self._get_conn()
        await conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS processed_messages (
                message_id TEXT PRIMARY KEY,
                root_message_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                sender_id TEXT,
                sender_name TEXT,
                status TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                graph_reply_id TEXT,
                error_message TEXT,
                retry_count INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS conversation_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                root_message_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS service_state (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_processed_status
                ON processed_messages(status);
            CREATE INDEX IF NOT EXISTS idx_conversation_root
                ON conversation_messages(root_message_id);
            """
        )
        await conn.commit()

    async def is_message_known(self, message_id: str) -> bool:
        """Prüft, ob eine Nachricht bereits bekannt ist."""
        conn = self._get_conn()
        cursor = await conn.execute(
            "SELECT 1 FROM processed_messages WHERE message_id = ?",
            (message_id,),
        )
        row = await cursor.fetchone()
        return row is not None

    async def get_message_status(self, message_id: str) -> str | None:
        """Gibt den Status einer Nachricht zurück."""
        conn = self._get_conn()
        cursor = await conn.execute(
            "SELECT status FROM processed_messages WHERE message_id = ?",
            (message_id,),
        )
        row = await cursor.fetchone()
        return str(row["status"]) if row else None

    async def insert_message(
        self,
        message_id: str,
        root_message_id: str,
        created_at: str,
        sender_id: str,
        sender_name: str,
        status: str,
    ) -> bool:
        """Fügt eine Nachricht ein. Gibt False zurück bei Duplikat."""
        if status not in MESSAGE_STATUSES:
            raise DatabaseError(f"Ungültiger Status: {status}")

        conn = self._get_conn()
        try:
            await conn.execute(
                """
                INSERT INTO processed_messages
                    (message_id, root_message_id, created_at, sender_id,
                     sender_name, status, started_at, completed_at,
                     graph_reply_id, error_message, retry_count)
                VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, 0)
                """,
                (
                    message_id,
                    root_message_id,
                    created_at,
                    sender_id,
                    sender_name,
                    status,
                ),
            )
            await conn.commit()
            logger.info(
                "message_status_changed",
                message_id=truncate_id(message_id),
                status=status,
            )
            return True
        except aiosqlite.IntegrityError:
            return False

    async def try_claim_message(self, message_id: str) -> bool:
        """Versucht atomar den Status von queued auf processing zu setzen."""
        conn = self._get_conn()
        now = _utc_now()
        cursor = await conn.execute(
            """
            UPDATE processed_messages
            SET status = 'processing', started_at = ?
            WHERE message_id = ? AND status = 'queued'
            """,
            (now, message_id),
        )
        await conn.commit()
        claimed = cursor.rowcount == 1
        if claimed:
            logger.info(
                "message_status_changed",
                message_id=truncate_id(message_id),
                status="processing",
            )
        return claimed

    async def update_message_completed(
        self,
        message_id: str,
        graph_reply_id: str,
    ) -> None:
        """Setzt den Status auf completed."""
        conn = self._get_conn()
        now = _utc_now()
        await conn.execute(
            """
            UPDATE processed_messages
            SET status = 'completed', completed_at = ?, graph_reply_id = ?
            WHERE message_id = ?
            """,
            (now, graph_reply_id, message_id),
        )
        await conn.commit()
        logger.info(
            "message_status_changed",
            message_id=truncate_id(message_id),
            status="completed",
        )

    async def update_message_failed(
        self,
        message_id: str,
        error_message: str,
    ) -> None:
        """Setzt den Status auf failed."""
        conn = self._get_conn()
        now = _utc_now()
        truncated_error = error_message[:500]
        await conn.execute(
            """
            UPDATE processed_messages
            SET status = 'failed', completed_at = ?, error_message = ?,
                retry_count = retry_count + 1
            WHERE message_id = ?
            """,
            (now, truncated_error, message_id),
        )
        await conn.commit()
        logger.info(
            "message_status_changed",
            message_id=truncate_id(message_id),
            status="failed",
        )

    async def count_by_status(self, status: str) -> int:
        """Zählt Nachrichten nach Status."""
        conn = self._get_conn()
        cursor = await conn.execute(
            "SELECT COUNT(*) as cnt FROM processed_messages WHERE status = ?",
            (status,),
        )
        row = await cursor.fetchone()
        return int(row["cnt"]) if row else 0

    async def get_service_state(self, key: str) -> str | None:
        """Liest einen Service-State-Wert."""
        conn = self._get_conn()
        cursor = await conn.execute(
            "SELECT value FROM service_state WHERE key = ?",
            (key,),
        )
        row = await cursor.fetchone()
        return str(row["value"]) if row and row["value"] is not None else None

    async def set_service_state(self, key: str, value: str) -> None:
        """Setzt einen Service-State-Wert."""
        conn = self._get_conn()
        now = _utc_now()
        await conn.execute(
            """
            INSERT INTO service_state (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, value, now),
        )
        await conn.commit()

    async def is_initial_poll_done(self) -> bool:
        """Prüft, ob der erste Poll bereits erfolgt ist."""
        value = await self.get_service_state("initial_poll_done")
        return value == "true"

    async def mark_initial_poll_done(self) -> None:
        """Markiert den ersten Poll als abgeschlossen."""
        await self.set_service_state("initial_poll_done", "true")

    async def add_conversation_message(
        self,
        root_message_id: str,
        role: str,
        content: str,
    ) -> None:
        """Fügt eine Nachricht zum Gesprächskontext hinzu."""
        conn = self._get_conn()
        now = _utc_now()
        await conn.execute(
            """
            INSERT INTO conversation_messages (root_message_id, role, content, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (root_message_id, role, content, now),
        )
        await conn.commit()

    async def get_conversation_messages(
        self,
        root_message_id: str,
        limit: int = 10,
    ) -> list[dict[str, str]]:
        """Gibt den Gesprächskontext für eine Root-Nachricht zurück."""
        conn = self._get_conn()
        cursor = await conn.execute(
            """
            SELECT role, content FROM conversation_messages
            WHERE root_message_id = ?
            ORDER BY id ASC
            LIMIT ?
            """,
            (root_message_id, limit),
        )
        rows = await cursor.fetchall()
        return [{"role": str(r["role"]), "content": str(r["content"])} for r in rows]

    async def get_recent_errors(self, limit: int = 10) -> list[dict[str, Any]]:
        """Gibt kürzliche Fehler zurück (ohne sensible Inhalte)."""
        conn = self._get_conn()
        cursor = await conn.execute(
            """
            SELECT message_id, status, error_message, completed_at
            FROM processed_messages
            WHERE status = 'failed' AND error_message IS NOT NULL
            ORDER BY completed_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
        return [
            {
                "message_id": truncate_id(str(r["message_id"])),
                "status": str(r["status"]),
                "error_message": str(r["error_message"])[:200] if r["error_message"] else "",
                "completed_at": str(r["completed_at"]) if r["completed_at"] else "",
            }
            for r in rows
        ]

    async def reset_watermark(self) -> None:
        """Setzt den Polling-Startpunkt zurück."""
        conn = self._get_conn()
        await conn.execute("DELETE FROM service_state WHERE key = 'initial_poll_done'")
        await conn.commit()

    async def get_queued_message_ids(self) -> list[str]:
        """Gibt IDs aller queued Nachrichten in chronologischer Reihenfolge zurück."""
        conn = self._get_conn()
        cursor = await conn.execute(
            "SELECT message_id FROM processed_messages WHERE status = 'queued' ORDER BY created_at"
        )
        rows = await cursor.fetchall()
        return [str(r["message_id"]) for r in rows]

    async def health_check(self) -> bool:
        """Prüft die Datenbankverbindung."""
        try:
            conn = self._get_conn()
            cursor = await conn.execute("SELECT 1")
            await cursor.fetchone()
            return True
        except Exception:
            return False
