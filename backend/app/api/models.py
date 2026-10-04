from typing import Annotated

from pydantic import Field, JsonValue

from app.models import StrictModel
from app.tools.base import ToolDefinition
from app.tools.get_member import GetMemberResult


class CreateSessionRequest(StrictModel):
    call_id: Annotated[str, Field(pattern=r"^CA[0-9a-fA-F]{32}$")]


class CreateSessionResponse(StrictModel):
    session_id: str
    system_prompt: str
    tools: list[ToolDefinition]


class InvokeToolRequest(StrictModel):
    tool_name: Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-z_]+$")]
    tool_call_id: Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")]
    arguments: dict[str, JsonValue]


class InvokeToolResponse(StrictModel):
    tool_name: str
    tool_call_id: str
    result: GetMemberResult
