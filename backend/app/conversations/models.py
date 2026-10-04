from typing import Annotated, Literal

from pydantic import Field

from app.benefits.models import Source
from app.models import StrictModel

ConversationId = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-z0-9-]+$")]
ShortPreview = Annotated[str, Field(min_length=1, max_length=240)]
Usd = Annotated[str, Field(pattern=r"^\d{1,7}\.\d{2}$")]
IsoTime = Annotated[str, Field(min_length=20, max_length=40)]
Channel = Literal["text", "voice"]
MessageRole = Literal["user", "assistant"]
MessageSource = Literal["voice", "text", "seed"]
IconName = Literal[
    "abe",
    "hospital",
    "spark",
    "tooth",
    "heartbeat",
    "braces",
    "wallet",
    "shield",
]


class Recap(StrictModel):
    origin: Literal["tool_result", "sample"] = "tool_result"
    synthetic: Literal[True] = True
    sources: Annotated[list[Source], Field(max_length=4)] = []
    provider_id: str | None = None
    provider_name: str | None = None
    procedure: str | None = None
    constraints: Annotated[list[str], Field(max_length=5)] = []
    plan_payment: Usd | None = None
    estimated_member_payment: Usd | None = None
    deductible_applied: Usd | None = None
    annual_maximum_remaining: Usd | None = None
    annual_maximum_remaining_afterward: Usd | None = None
    fsa_balance: Usd | None = None
    network_status: str | None = None


class SuggestedAction(StrictModel):
    id: Literal["A", "B", "C", "D"]
    label: str
    prompt: str


class Conversation(StrictModel):
    conversation_id: ConversationId
    member_id: str
    channel: Channel
    status: Literal["in_progress", "completed", "partial"] = "completed"
    pinned: bool = False
    title: Annotated[str, Field(min_length=1, max_length=80)]
    tag: Annotated[str, Field(min_length=1, max_length=40)]
    icon: IconName
    preview: ShortPreview
    updated_at: IsoTime
    recap: Recap | None = None
    actions: Annotated[list[SuggestedAction], Field(max_length=4)] = []
    synthetic: Literal[True] = True


class Message(StrictModel):
    message_id: Annotated[str, Field(min_length=1, max_length=64)]
    conversation_id: ConversationId
    role: MessageRole
    text: Annotated[str, Field(min_length=1, max_length=4000)]
    source: MessageSource
    created_at: IsoTime


class ConversationDetail(StrictModel):
    conversation: Conversation
    messages: Annotated[list[Message], Field(max_length=500)]


class MemberProfile(StrictModel):
    member_id: str
    display_name: str
    call_number: str
    synthetic: Literal[True] = True


class ConversationList(StrictModel):
    profile: MemberProfile
    conversations: list[Conversation]
