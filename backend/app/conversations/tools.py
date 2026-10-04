from typing import Literal

from app.conversations import DEMO_MEMBER_ID
from app.conversations.models import Recap
from app.conversations.repository import ConversationRepository
from app.models import StrictModel
from app.tools.base import Tool


class LatestRecapArguments(StrictModel):
    pass


class LatestRecapResult(StrictModel):
    status: Literal["success", "empty"]
    recap: Recap | None = None


class GetLatestRecapTool(Tool[LatestRecapArguments, LatestRecapResult]):
    name = "get_latest_recap"
    description = (
        "Return the stored recap (check origin: tool_result or sample) "
        "from the member's most recent completed voice call. "
        "Use this instead of guessing numbers, provider names, or procedures from chat history."
    )
    arguments_type = LatestRecapArguments
    result_type = LatestRecapResult

    def __init__(self, repository: ConversationRepository) -> None:
        self._repository = repository

    def execute(self, arguments: LatestRecapArguments) -> LatestRecapResult:
        recap = self._repository.latest_voice_recap(DEMO_MEMBER_ID)
        if recap is None:
            return LatestRecapResult(status="empty")
        return LatestRecapResult(status="success", recap=recap)
