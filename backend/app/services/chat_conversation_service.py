"""The user's chat conversations, kept on the server so every device shows the same history.

Each user has a database of their own (a schema per user in the cloud), so the conversations
here are only that user's. The chat page sends a whole conversation each time it changes and
keeps the newest MAX_CONVERSATIONS, like the history it showed from the browser before.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select

from app.db.models import ChatConversation
from app.db.session import Database

MAX_CONVERSATIONS = 50


class ChatConversationService:
    def __init__(self, database: Database) -> None:
        self._database = database

    def list(self) -> list[dict[str, Any]]:
        with self._database.session() as session:
            rows = session.scalars(
                select(ChatConversation)
                .order_by(ChatConversation.updated_at_ms.desc(), ChatConversation.id)
                .limit(MAX_CONVERSATIONS)
            )
            return [self._view(row) for row in rows]

    def save(
        self, conversation_id: str, title: str, updated_at_ms: int, data: dict[str, Any]
    ) -> dict[str, Any]:
        """Keep the conversation; a copy older than the one kept (sent late) does not replace it."""
        with self._database.transaction() as session:
            row = session.get(ChatConversation, conversation_id)
            if row is None:
                row = ChatConversation(
                    id=conversation_id, title=title, updated_at_ms=updated_at_ms, data=data
                )
                session.add(row)
            elif updated_at_ms >= row.updated_at_ms:
                row.title, row.updated_at_ms, row.data = title, updated_at_ms, data
            session.flush()
            view = self._view(row)
            kept = select(ChatConversation.id).order_by(
                ChatConversation.updated_at_ms.desc(), ChatConversation.id
            ).limit(MAX_CONVERSATIONS)
            session.execute(
                delete(ChatConversation).where(ChatConversation.id.not_in(kept.scalar_subquery()))
            )
        return view

    def remove(self, conversation_id: str) -> None:
        with self._database.transaction() as session:
            session.execute(delete(ChatConversation).where(ChatConversation.id == conversation_id))

    @staticmethod
    def _view(row: ChatConversation) -> dict[str, Any]:
        return {
            "id": row.id,
            "title": row.title,
            "updatedAt": row.updated_at_ms,
            "turns": row.data.get("turns", []),
            **({"images": row.data["images"]} if row.data.get("images") else {}),
        }
