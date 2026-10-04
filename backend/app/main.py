import hmac
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field, ValidationError
from starlette.concurrency import run_in_threadpool

from app.agent.prompt import SYSTEM_PROMPT
from app.api.models import (
    CreateSessionRequest,
    CreateSessionResponse,
    InvokeToolRequest,
    InvokeToolResponse,
)
from app.benefits.models import BenefitEstimate, UnavailableResult
from app.benefits.repository import LocalPlanRulesRepository, PlanRulesRepository
from app.config import Settings
from app.conversation.sessions import Session, SessionCapacityError, SessionStore
from app.conversations.api import app_router
from app.conversations.chat import BedrockTextModel, TextChat, TextModel
from app.conversations.inbox import Inbox
from app.conversations.recap import build_actions, build_recap
from app.conversations.repository import (
    ConversationRepository,
    DynamoDBConversationRepository,
    SyntheticConversationRepository,
)
from app.conversations.sqlite_repository import SQLiteConversationRepository
from app.members.repository import DemoMemberRepository, DynamoDBMemberRepository, MemberRepository
from app.models import StrictModel
from app.providers.repository import ProviderRepository, SyntheticProviderRepository
from app.retrieval.repository import (
    BedrockKnowledgeBaseRetriever,
    LocalPlanDocumentRetriever,
    PlanDocumentRetriever,
)
from app.tools.benefits import BenefitsTools, ProviderResult
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
    conversations: ConversationRepository | None = None,
    text_model: TextModel | None = None,
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
    if conversations is None:
        if config.conversation_repository == "sqlite":
            db_path = Path(config.conversation_db_path)
            if not db_path.is_absolute():
                db_path = Path(__file__).resolve().parents[2] / db_path
            conversations = SQLiteConversationRepository(db_path)
        elif config.conversation_repository == "dynamodb":
            conversations = DynamoDBConversationRepository(
                config.dynamodb_conversation_table, config.aws_region
            )
        else:
            conversations = SyntheticConversationRepository()
    inbox = Inbox(conversations)
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
        await run_in_threadpool(inbox.start_voice, session)
        log_event("session_created")
        return CreateSessionResponse(
            session_id=session.session_id,
            system_prompt=SYSTEM_PROMPT,
            tools=[resolver.definition, member_tool.definition, *benefits.definitions],
        )

    @app.delete("/sessions/{session_id}", status_code=204, dependencies=[Depends(authenticate)])
    async def delete_session(session_id: UUID) -> Response:
        session = store.get(str(session_id))
        if session:

            def finish_voice() -> None:
                with session.lock:
                    recap = build_recap(session)
                    inbox.complete_voice(session, recap, build_actions(recap))

            await run_in_threadpool(finish_voice)
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
            if body.tool_name in {
                "calculate_benefit",
                "update_conversation_context",
                "resolve_member_id",
            }:
                session.last_estimate = None
            result = execute_locked(session, body)
            if isinstance(result.result, BenefitEstimate):
                session.last_estimate = result.result
                provider_id = session.context.get("provider_id")
                provider = (
                    benefits.providers.get_provider(str(provider_id), result.result.plan_id)
                    if provider_id
                    else None
                )
                if provider:
                    session.last_providers = [provider]
            elif isinstance(result.result, ProviderResult):
                session.last_providers = result.result.providers
            return result
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
            session.clear_member()
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

    @app.post(
        "/sessions/{session_id}/transcripts", status_code=204, dependencies=[Depends(authenticate)]
    )
    async def transcripts(session_id: UUID, body: TranscriptBatch) -> Response:
        session = store.get(str(session_id))
        if session is None:
            raise HTTPException(404, "Session not found")

        def persist() -> None:
            for event in body.events:
                inbox.append_transcript(session, event.role, event.text, event.event_id)
            if body.incomplete and session.conversation_id:
                current = inbox.repository.get_conversation("DEMO001", session.conversation_id)
                if current:
                    inbox.repository.put_conversation(
                        current.model_copy(update={"status": "partial"})
                    )

        await run_in_threadpool(persist)
        return Response(status_code=204)

    chat = TextChat(
        inbox,
        text_model or BedrockTextModel(config.nova_text_model_id, config.aws_region),
        [resolver.definition, member_tool.definition, *benefits.definitions],
        execute_tool,
    )
    app.include_router(app_router(config, inbox, chat))
    frontend = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if frontend.is_dir():
        app.mount("/", StaticFiles(directory=frontend, html=True), name="companion")
    return app


class TranscriptEvent(StrictModel):
    event_id: Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]
    role: Literal["user", "assistant"]
    text: Annotated[str, Field(min_length=1, max_length=4000)]


class TranscriptBatch(StrictModel):
    events: Annotated[list[TranscriptEvent], Field(max_length=20)]
    incomplete: bool = False
