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
from starlette.concurrency import run_in_threadpool

from app.agent.prompt import SYSTEM_PROMPT
from app.api.models import (
    CreateSessionRequest,
    CreateSessionResponse,
    InvokeToolRequest,
    InvokeToolResponse,
)
from app.benefits.models import UnavailableResult
from app.benefits.repository import LocalPlanRulesRepository, PlanRulesRepository
from app.config import Settings
from app.conversation.sessions import Session, SessionCapacityError, SessionStore
from app.members.repository import DemoMemberRepository, DynamoDBMemberRepository, MemberRepository
from app.providers.repository import ProviderRepository, SyntheticProviderRepository
from app.retrieval.repository import (
    BedrockKnowledgeBaseRetriever,
    LocalPlanDocumentRetriever,
    PlanDocumentRetriever,
)
from app.tools.benefits import BenefitsTools
from app.tools.get_member import GetMemberTool
from app.tools.resolve_member_id import ResolveMemberIdTool

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
    plans: PlanRulesRepository | None = None,
    retriever: PlanDocumentRetriever | None = None,
    providers: ProviderRepository | None = None,
) -> FastAPI:
    config = settings or Settings.from_environment()
    store = sessions or SessionStore()
    members = repository or (
        DemoMemberRepository()
        if config.member_repository == "synthetic"
        else DynamoDBMemberRepository(config.dynamodb_member_table, config.aws_region)
    )
    benefits = BenefitsTools(
        members,
        plans or LocalPlanRulesRepository(),
        retriever
        or (
            LocalPlanDocumentRetriever()
            if config.rag_provider == "local"
            else BedrockKnowledgeBaseRetriever(config.bedrock_knowledge_base_id, config.aws_region)
        ),
        providers or SyntheticProviderRepository(),
    )
    member_tool = GetMemberTool(members)
    resolver = ResolveMemberIdTool(members)

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
            tools=[resolver.definition, member_tool.definition, *benefits.definitions],
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
        return await run_in_threadpool(execute_tool, session, body)

    def execute_tool(session: Session, body: InvokeToolRequest) -> InvokeToolResponse:
        if not session.lock.acquire(blocking=False):
            raise HTTPException(409, "A tool is already running for this session")
        try:
            return execute_locked(session, body)
        finally:
            session.lock.release()

    def execute_locked(session: Session, body: InvokeToolRequest) -> InvokeToolResponse:
        if body.tool_name in benefits.arguments:
            log_event("tool_invoked", tool=body.tool_name)
            try:
                benefit_result = benefits.invoke(body.tool_name, body.arguments, session)
            except ValidationError:
                raise HTTPException(422, "Invalid tool input or result") from None
            except Exception:
                session.sources = []
                benefit_result = UnavailableResult(
                    status="unavailable",
                    reason="This information cannot currently be verified. Please try again.",
                )
                log_event("tool_unavailable", tool=body.tool_name)
            session.last_tool_call_id = body.tool_call_id
            return InvokeToolResponse(
                tool_name=body.tool_name, tool_call_id=body.tool_call_id, result=benefit_result
            )
        if body.tool_name == resolver.name:
            log_event("tool_invoked", tool=resolver.name)
            try:
                resolution = resolver.invoke(body.arguments)
            except ValidationError:
                raise HTTPException(422, "Invalid tool input or result") from None
            except Exception:
                session.clear_member()
                raise
            if resolution.member_id != session.member_id:
                session.clear_member()
            session.member_id = resolution.member_id
            session.last_tool_call_id = body.tool_call_id
            session.last_tool_result = None
            log_event("member_id_resolved", status=resolution.status)
            return InvokeToolResponse(
                tool_name=resolver.name, tool_call_id=body.tool_call_id, result=resolution
            )
        if body.tool_name != member_tool.name:
            raise HTTPException(400, "Unknown tool")
        log_event("tool_invoked", tool=member_tool.name)
        try:
            result = member_tool.invoke(body.arguments)
        except ValidationError:
            raise HTTPException(422, "Invalid tool input or result") from None
        except Exception:
            session.clear_member()
            raise
        new_member_id = result.member.member_id if result.member else None
        if new_member_id != session.member_id:
            session.context = {}
            session.sources = []
        session.member_id = new_member_id
        session.plan_id = result.member.plan_id if result.member else None
        session.last_tool_call_id = body.tool_call_id
        session.last_tool_result = result
        log_event("get_member_completed", status=result.status)
        return InvokeToolResponse(
            tool_name=member_tool.name,
            tool_call_id=body.tool_call_id,
            result=result,
        )

    return app
