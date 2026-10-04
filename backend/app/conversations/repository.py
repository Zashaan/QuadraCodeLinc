from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from threading import Lock
from typing import Any, Protocol
from uuid import uuid4

from pydantic import TypeAdapter

from app.benefits.repository import DEMO_PATH
from app.conversations import DEMO_MEMBER_ID
from app.conversations.models import (
    Conversation,
    ConversationDetail,
    Message,
    Recap,
    SuggestedAction,
)
from app.models import StrictModel


class SeedMessage(StrictModel):
    role: str
    text: str
    source: str = "seed"


class SeedConversation(StrictModel):
    conversation_id: str
    channel: str
    pinned: bool = False
    title: str
    tag: str
    icon: str
    preview: str
    updated_at: str
    recap: Recap | None = None
    actions: list[SuggestedAction] = []
    messages: list[SeedMessage] = []


class ConversationRepository(Protocol):
    def list_conversations(self, member_id: str) -> list[Conversation]: ...
    def get_detail(self, member_id: str, conversation_id: str) -> ConversationDetail | None: ...
    def get_conversation(self, member_id: str, conversation_id: str) -> Conversation | None: ...
    def put_conversation(self, conversation: Conversation) -> None: ...
    def append_message(self, message: Message) -> None: ...
    def latest_voice_recap(self, member_id: str) -> Recap | None: ...


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_message(
    conversation_id: str,
    role: str,
    text: str,
    source: str,
    *,
    created_at: str | None = None,
) -> Message:
    return Message(
        message_id=str(uuid4()),
        conversation_id=conversation_id,
        role=role,  # type: ignore[arg-type]
        text=text,
        source=source,  # type: ignore[arg-type]
        created_at=created_at or now_iso(),
    )


class SyntheticConversationRepository:
    def __init__(self, path: Path | None = None) -> None:
        source = path or DEMO_PATH / "conversations.json"
        seeds = TypeAdapter(list[SeedConversation]).validate_json(
            source.read_text(encoding="utf-8")
        )
        self._lock = Lock()
        self._conversations: dict[tuple[str, str], Conversation] = {}
        self._messages: dict[tuple[str, str], list[Message]] = {}
        for seed in seeds:
            conversation = Conversation(
                conversation_id=seed.conversation_id,
                member_id=DEMO_MEMBER_ID,
                channel=seed.channel,  # type: ignore[arg-type]
                pinned=seed.pinned,
                title=seed.title,
                tag=seed.tag,
                icon=seed.icon,  # type: ignore[arg-type]
                preview=seed.preview,
                updated_at=seed.updated_at,
                recap=seed.recap,
                actions=seed.actions,
            )
            key = (DEMO_MEMBER_ID, seed.conversation_id)
            self._conversations[key] = conversation
            self._messages[key] = [
                new_message(
                    seed.conversation_id,
                    item.role,
                    item.text,
                    item.source,
                    created_at=seed.updated_at,
                )
                for item in seed.messages
            ]

    def list_conversations(self, member_id: str) -> list[Conversation]:
        with self._lock:
            rows = [row for (owner, _), row in self._conversations.items() if owner == member_id]
        return sorted(
            sorted(rows, key=lambda row: row.updated_at, reverse=True),
            key=lambda row: not row.pinned,
        )

    def get_conversation(self, member_id: str, conversation_id: str) -> Conversation | None:
        with self._lock:
            return self._conversations.get((member_id, conversation_id))

    def get_detail(self, member_id: str, conversation_id: str) -> ConversationDetail | None:
        with self._lock:
            conversation = self._conversations.get((member_id, conversation_id))
            if conversation is None:
                return None
            messages = list(self._messages.get((member_id, conversation_id), []))[-500:]
        return ConversationDetail(conversation=conversation, messages=messages)

    def put_conversation(self, conversation: Conversation) -> None:
        key = (conversation.member_id, conversation.conversation_id)
        with self._lock:
            self._conversations[key] = conversation
            self._messages.setdefault(key, [])

    def append_message(self, message: Message) -> None:
        key = (DEMO_MEMBER_ID, message.conversation_id)
        with self._lock:
            conversation = self._conversations.get(key)
            if conversation is None:
                raise KeyError("conversation_not_found")
            history = self._messages.setdefault(key, [])
            if any(item.message_id == message.message_id for item in history):
                return
            history.append(message)
            if len(history) > 500:
                del history[:-500]
            preview = message.text[:240]
            self._conversations[key] = conversation.model_copy(
                update={"preview": preview, "updated_at": message.created_at}
            )

    def latest_voice_recap(self, member_id: str) -> Recap | None:
        voices = [
            row
            for row in self.list_conversations(member_id)
            if row.channel == "voice" and row.status == "completed" and row.recap is not None
        ]
        if not voices:
            return None
        return voices[0].recap


def _decimal_to_json(value: Any) -> Any:
    if isinstance(value, Decimal) and value == value.to_integral_value():
        return int(value)
    if isinstance(value, Decimal):
        return format(value, ".2f")
    return value


class DynamoDBConversationRepository:
    """Existing table only. Never CreateTable. No Scan."""

    def __init__(self, table_name: str, region: str = "us-east-1", *, table: Any = None) -> None:
        if not table_name:
            raise ValueError("DYNAMODB_CONVERSATION_TABLE is required")
        if table is None:
            from app.aws import aws_client

            table = aws_client("dynamodb", region, resource=True).Table(table_name)
        self._table = table

    def _pk(self, member_id: str) -> str:
        return f"MEMBER#{member_id}"

    def _conversation_sk(self, conversation_id: str) -> str:
        return f"CONV#{conversation_id}"

    def _message_sk(self, conversation_id: str, created_at: str, message_id: str) -> str:
        return f"CONV#{conversation_id}#MSG#{created_at}#{message_id}"

    def list_conversations(self, member_id: str) -> list[Conversation]:
        response = self._table.query(
            KeyConditionExpression="pk = :pk AND begins_with(sk, :sk)",
            FilterExpression="entity = :entity",
            ExpressionAttributeValues={
                ":pk": self._pk(member_id),
                ":sk": "CONV#",
                ":entity": "conversation",
            },
            ConsistentRead=True,
        )
        rows = [
            Conversation.model_validate(self._decode(item)) for item in response.get("Items", [])
        ]
        return sorted(
            sorted(rows, key=lambda row: row.updated_at, reverse=True),
            key=lambda row: not row.pinned,
        )

    def get_conversation(self, member_id: str, conversation_id: str) -> Conversation | None:
        item = self._table.get_item(
            Key={"pk": self._pk(member_id), "sk": self._conversation_sk(conversation_id)},
            ConsistentRead=True,
        ).get("Item")
        if item is None:
            return None
        return Conversation.model_validate(self._decode(item))

    def get_detail(self, member_id: str, conversation_id: str) -> ConversationDetail | None:
        conversation = self.get_conversation(member_id, conversation_id)
        if conversation is None:
            return None
        response = self._table.query(
            KeyConditionExpression="pk = :pk AND begins_with(sk, :sk)",
            ExpressionAttributeValues={
                ":pk": self._pk(member_id),
                ":sk": f"CONV#{conversation_id}#MSG#",
            },
            ConsistentRead=True,
        )
        messages = [
            Message.model_validate(self._decode(item)) for item in response.get("Items", [])
        ]
        return ConversationDetail(conversation=conversation, messages=messages[-500:])

    def put_conversation(self, conversation: Conversation) -> None:
        item = conversation.model_dump(mode="json")
        item.update(
            {
                "pk": self._pk(conversation.member_id),
                "sk": self._conversation_sk(conversation.conversation_id),
                "entity": "conversation",
            }
        )
        self._table.put_item(Item=item)

    def append_message(self, message: Message) -> None:
        conversation = self.get_conversation(DEMO_MEMBER_ID, message.conversation_id)
        if conversation is None:
            raise KeyError("conversation_not_found")
        item = message.model_dump(mode="json")
        item.update(
            {
                "pk": self._pk(DEMO_MEMBER_ID),
                "sk": self._message_sk(
                    message.conversation_id, message.created_at, message.message_id
                ),
                "entity": "message",
            }
        )
        self._table.put_item(Item=item)
        updated = conversation.model_copy(
            update={"preview": message.text[:240], "updated_at": message.created_at}
        )
        self.put_conversation(updated)

    def latest_voice_recap(self, member_id: str) -> Recap | None:
        voices = [
            row
            for row in self.list_conversations(member_id)
            if row.channel == "voice" and row.status == "completed" and row.recap is not None
        ]
        return voices[0].recap if voices else None

    def _decode(self, item: dict[str, Any]) -> dict[str, Any]:
        cleaned = {
            key: _decimal_to_json(value)
            for key, value in item.items()
            if key not in {"pk", "sk", "entity"}
        }
        return cleaned
