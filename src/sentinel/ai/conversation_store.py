"""Local conversation history (SQLite). Used only when the user turns history on. No keys are ever stored."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Optional

from ..storage.database import Database

MAX_CONVERSATIONS = 50
MAX_MESSAGES = 200
MAX_CONTENT = 12000


@dataclass(frozen=True)
class StoredMessage:
    id: int
    conversation_id: int
    role: str
    content: str            # what was sent (user) or the sanitised answer (assistant)
    display: str            # what the chat shows
    ts: float
    provider: str
    model: str
    meta: dict


@dataclass(frozen=True)
class StoredConversation:
    id: int
    title: str
    created_ts: float
    updated_ts: float
    messages: int = 0


class ConversationStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    def create(self, title: str) -> int:
        now = time.time()
        rid = self._db.execute("INSERT INTO ai_conversations(title, created_ts, updated_ts) VALUES(?,?,?)",
                               (title.strip()[:80] or "Conversation", now, now))
        self._prune()
        return int(rid)

    def add_message(self, conversation_id: int, role: str, content: str, display: str = "", *, provider: str = "",
                    model: str = "", meta: Optional[dict] = None) -> int:
        if role not in ("user", "assistant"):
            raise ValueError("role must be user or assistant")
        now = time.time()
        rid = self._db.execute(
            "INSERT INTO ai_messages(conversation_id, role, content, display, ts, provider, model, meta) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (conversation_id, role, content[:MAX_CONTENT], (display or content)[:MAX_CONTENT], now, provider[:40],
             model[:120], json.dumps(meta or {})[:2000]))
        self._db.execute("UPDATE ai_conversations SET updated_ts=? WHERE id=?", (now, conversation_id))
        self._db.execute(
            "DELETE FROM ai_messages WHERE conversation_id=? AND id NOT IN "
            "(SELECT id FROM ai_messages WHERE conversation_id=? ORDER BY id DESC LIMIT ?)",
            (conversation_id, conversation_id, MAX_MESSAGES))
        return int(rid)

    def messages(self, conversation_id: int) -> list[StoredMessage]:
        rows = self._db.query("SELECT * FROM ai_messages WHERE conversation_id=? ORDER BY id", (conversation_id,))
        out = []
        for r in rows:
            try:
                meta = json.loads(r["meta"] or "{}")
            except ValueError:
                meta = {}
            out.append(StoredMessage(r["id"], r["conversation_id"], r["role"], r["content"], r["display"], r["ts"],
                                     r["provider"], r["model"], meta if isinstance(meta, dict) else {}))
        return out

    def list(self, limit: int = MAX_CONVERSATIONS) -> list[StoredConversation]:
        rows = self._db.query(
            "SELECT c.id, c.title, c.created_ts, c.updated_ts, "
            "(SELECT COUNT(*) FROM ai_messages m WHERE m.conversation_id=c.id) AS n "
            "FROM ai_conversations c ORDER BY c.updated_ts DESC, c.id DESC LIMIT ?", (int(limit),))
        return [StoredConversation(r["id"], r["title"], r["created_ts"], r["updated_ts"], r["n"]) for r in rows]

    def count(self) -> int:
        return int(self._db.query_one("SELECT COUNT(*) FROM ai_conversations")[0])

    def exists(self, conversation_id: int) -> bool:
        return self._db.query_one("SELECT 1 FROM ai_conversations WHERE id=?", (conversation_id,)) is not None

    def delete(self, conversation_id: int) -> bool:
        return self._db.execute_rowcount("DELETE FROM ai_conversations WHERE id=?", (conversation_id,)) > 0

    def clear_all(self) -> int:
        n = self.count()
        self._db.execute("DELETE FROM ai_conversations")
        return n

    def _prune(self) -> None:
        self._db.execute(
            "DELETE FROM ai_conversations WHERE id NOT IN "
            "(SELECT id FROM ai_conversations ORDER BY updated_ts DESC, id DESC LIMIT ?)", (MAX_CONVERSATIONS,))
