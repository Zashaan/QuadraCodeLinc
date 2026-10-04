from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Lock
from time import monotonic
from uuid import uuid4

from pydantic import JsonValue

from app.benefits.models import BenefitEstimate, Source
from app.providers.repository import Provider
from app.tools.get_member import GetMemberResult


@dataclass
class Session:
    session_id: str
    call_id: str
    created_at: float
    last_activity_at: float
    member_id: str | None = None
    plan_id: str | None = None
    channel: str = "voice"
    conversation_id: str | None = None
    lock: Lock = field(default_factory=Lock, repr=False)
    context: dict[str, JsonValue] = field(default_factory=dict)
    sources: list[Source] = field(default_factory=list)
    last_tool_call_id: str | None = None
    last_tool_result: GetMemberResult | None = None
    last_estimate: BenefitEstimate | None = None
    last_providers: list[Provider] = field(default_factory=list)

    def clear_member(self) -> None:
        self.member_id = None
        self.plan_id = None
        self.context = {}
        self.sources = []
        self.last_tool_result = None
        self.last_estimate = None
        self.last_providers = []


class SessionCapacityError(Exception):
    pass


class SessionStore:
    """Single-worker store; call synchronously inside the async request handlers.

    Retains only the latest tool result. TTL is lazy-pruned on every operation;
    capacity also bounds memory when no requests arrive to trigger expiry.
    """

    def __init__(
        self,
        *,
        capacity: int = 100,
        idle_ttl_seconds: float = 900,
        maximum_age_seconds: float = 3600,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if capacity < 1 or idle_ttl_seconds <= 0 or maximum_age_seconds <= 0:
            raise ValueError("Session limits must be positive")
        self._sessions: dict[str, Session] = {}
        self._capacity = capacity
        self._idle_ttl = idle_ttl_seconds
        self._maximum_age = maximum_age_seconds
        self._clock = clock

    def _prune(self) -> float:
        now = self._clock()
        expired = [
            key
            for key, session in self._sessions.items()
            if now - session.last_activity_at >= self._idle_ttl
            or now - session.created_at >= self._maximum_age
        ]
        for key in expired:
            del self._sessions[key]
        return now

    def create(self, call_id: str) -> Session:
        now = self._prune()
        # Repeated initialization of the same live call is idempotent.
        for session in self._sessions.values():
            if session.call_id == call_id:
                session.last_activity_at = now
                return session
        if len(self._sessions) >= self._capacity:
            raise SessionCapacityError
        session = Session(str(uuid4()), call_id, now, now)
        self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> Session | None:
        now = self._prune()
        session = self._sessions.get(session_id)
        if session is not None:
            session.last_activity_at = now
        return session

    def delete(self, session_id: str) -> None:
        self._prune()
        self._sessions.pop(session_id, None)

    def clear(self) -> None:
        self._sessions.clear()
