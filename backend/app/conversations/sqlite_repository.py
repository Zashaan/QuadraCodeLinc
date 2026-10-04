"""Private local persistence for the single synthetic demo member. No cloud resources."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.conversations import DEMO_MEMBER_ID
from app.conversations.models import Conversation, ConversationDetail, Message, Recap
from app.conversations.repository import SyntheticConversationRepository


class SQLiteConversationRepository:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS messages (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL, data TEXT NOT NULL,
                    UNIQUE(conversation_id,id));
            """)
            empty = db.execute("SELECT count(*) FROM conversations").fetchone()[0] == 0
        path.chmod(0o600)
        # An unfinished session cannot survive a process restart.
        for row in self.list_conversations(DEMO_MEMBER_ID):
            if row.status == "in_progress":
                self.put_conversation(row.model_copy(update={"status": "partial"}))
        if empty:
            seeds = SyntheticConversationRepository()
            for row in seeds.list_conversations(DEMO_MEMBER_ID):
                self.put_conversation(row)
                detail = seeds.get_detail(DEMO_MEMBER_ID, row.conversation_id)
                if detail:
                    for message in detail.messages:
                        self.append_message(message)
                    self.put_conversation(row)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=2)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def list_conversations(self, member_id: str) -> list[Conversation]:
        with self.connect() as db:
            records = db.execute(
                "SELECT data FROM conversations WHERE owner=?", (member_id,)
            ).fetchall()
        rows = [Conversation.model_validate_json(row[0]) for row in records]
        return sorted(
            sorted(rows, key=lambda r: r.updated_at, reverse=True), key=lambda r: not r.pinned
        )[:100]

    def get_conversation(self, member_id: str, conversation_id: str) -> Conversation | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT data FROM conversations WHERE owner=? AND id=?",
                (member_id, conversation_id),
            ).fetchone()
        return Conversation.model_validate_json(row[0]) if row else None

    def put_conversation(self, conversation: Conversation) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO conversations VALUES (?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET data=excluded.data WHERE owner=excluded.owner",
                (
                    conversation.conversation_id,
                    conversation.member_id,
                    conversation.model_dump_json(),
                ),
            )

    def append_message(self, message: Message) -> None:
        with self.connect() as db:
            row = db.execute(
                "SELECT data FROM conversations WHERE id=? AND owner=?",
                (message.conversation_id, DEMO_MEMBER_ID),
            ).fetchone()
            if row is None:
                raise KeyError("conversation_not_found")
            current = Conversation.model_validate_json(row[0])
            inserted = db.execute(
                "INSERT OR IGNORE INTO messages(id,conversation_id,data) VALUES (?,?,?)",
                (message.message_id, message.conversation_id, message.model_dump_json()),
            )
            if inserted.rowcount:
                current = current.model_copy(
                    update={"preview": message.text[:240], "updated_at": message.created_at}
                )
                db.execute(
                    "UPDATE conversations SET data=? WHERE id=?",
                    (current.model_dump_json(), message.conversation_id),
                )
            # Storage is bounded too, not just the API response.
            db.execute(
                "DELETE FROM messages WHERE conversation_id=? AND seq NOT IN "
                "(SELECT seq FROM messages WHERE conversation_id=? ORDER BY seq DESC LIMIT 500)",
                (message.conversation_id, message.conversation_id),
            )

    def get_detail(self, member_id: str, conversation_id: str) -> ConversationDetail | None:
        conversation = self.get_conversation(member_id, conversation_id)
        if conversation is None:
            return None
        with self.connect() as db:
            rows = db.execute(
                "SELECT data FROM messages WHERE conversation_id=? ORDER BY seq DESC LIMIT 500",
                (conversation_id,),
            ).fetchall()
        return ConversationDetail(
            conversation=conversation,
            messages=[Message.model_validate_json(row[0]) for row in reversed(rows)],
        )

    def latest_voice_recap(self, member_id: str) -> Recap | None:
        return next(
            (
                row.recap
                for row in self.list_conversations(member_id)
                if row.channel == "voice" and row.status == "completed" and row.recap
            ),
            None,
        )
