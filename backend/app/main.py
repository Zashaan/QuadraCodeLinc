import hmac
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.agent.prompt import SYSTEM_PROMPT
from app.api.models import (
    CreateSessionRequest,
    CreateSessionResponse,
    InvokeToolRequest,
    InvokeToolResponse,
)
from app.config import Settings
from app.conversation.sessions import SessionCapacityError, SessionStore
from app.members.repository import DemoMemberRepository, MemberRepository
from app.tools.get_member import GetMemberTool

logger = logging.getLogger("abe")
logger.setLevel(logging.INFO)
logger.propagate = False
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)


def log_event(event: str, **fields: str) -> None:
    # Explicit allowlisted event fields only; no input arguments, headers, or member facts.
    logger.info(json.dumps({"event": event, **fields}))


def create_app(
    settings: Settings | None = None,
    repository: MemberRepository | None = None,
    sessions: SessionStore | None = None,
) -> FastAPI:
    config = settings or Settings.from_environment()
    store = sessions or SessionStore()
    member_tool = GetMemberTool(repository or DemoMemberRepository())

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        log_event("backend_started")
        yield
        store.clear()
        log_event("backend_stopped")

    app = FastAPI(
        title="Abe internal agent API",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    async def authenticate(authorization: Annotated[str | None, Header()] = None) -> None:
        expected = f"Bearer {config.internal_token}".encode()
        supplied = (authorization or "").encode()
        if not hmac.compare_digest(supplied, expected):
            raise HTTPException(401, "Unauthorized", headers={"WWW-Authenticate": "Bearer"})

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, __: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": "Invalid request"})

    @app.exception_handler(Exception)
    async def unexpected_error(_: Request, __: Exception) -> JSONResponse:
        log_event("backend_error")
        return JSONResponse(status_code=500, content={"detail": "Service unavailable"})

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post(
        "/sessions", response_model=CreateSessionResponse, dependencies=[Depends(authenticate)]
    )
    async def create_session(body: CreateSessionRequest) -> CreateSessionResponse:
        try:
            session = store.create(body.call_id)
        except SessionCapacityError:
            raise HTTPException(503, "Session capacity reached") from None
        log_event("session_created")
        return CreateSessionResponse(
            session_id=session.session_id,
            system_prompt=SYSTEM_PROMPT,
            tools=[member_tool.definition],
        )

    @app.delete("/sessions/{session_id}", status_code=204, dependencies=[Depends(authenticate)])
    async def delete_session(session_id: UUID) -> Response:
        store.delete(str(session_id))
        log_event("session_deleted")
        return Response(status_code=204)

    @app.post(
        "/sessions/{session_id}/tools",
        response_model=InvokeToolResponse,
        response_model_exclude_none=True,
        dependencies=[Depends(authenticate)],
    )
    async def invoke_tool(session_id: UUID, body: InvokeToolRequest) -> InvokeToolResponse:
        session = store.get(str(session_id))
        if session is None:
            raise HTTPException(404, "Session not found")
        if body.tool_name != member_tool.name:
            raise HTTPException(400, "Unknown tool")
        log_event("tool_invoked", tool=member_tool.name)
        try:
            result = member_tool.invoke(body.arguments)
        except ValidationError:
            raise HTTPException(422, "Invalid tool input or result") from None
        session.member_id = result.member.member_id if result.member else result.member_id
        session.last_tool_call_id = body.tool_call_id
        session.last_tool_result = result
        log_event("get_member_completed", status=result.status)
        return InvokeToolResponse(
            tool_name=member_tool.name,
            tool_call_id=body.tool_call_id,
            result=result,
        )

    return app
