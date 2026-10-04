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
    ToolResult,
)
from app.calculator.benefits import BenefitCalculator
from app.config import Settings
from app.conversation.sessions import SessionCapacityError, SessionStore
from app.members.repository import (
    DynamoDBMemberRepository,
    MemberRepository,
    SyntheticMemberRepository,
)
from app.plans.repository import LocalPlanRulesRepository, PlanRulesRepository
from app.providers.repository import ProviderRepository, SyntheticProviderRepository
from app.retrieval.repository import (
    BedrockKnowledgeBaseRetriever,
    LocalPlanDocumentRetriever,
    PlanDocumentRetriever,
)
from app.tools.calculate_benefit import CalculateBenefitTool
from app.tools.get_member import GetMemberTool
from app.tools.resolve_member_id import ResolveMemberIdTool
from app.tools.retrieve_plan_context import RetrievePlanContextTool
from app.tools.search_providers import SearchProvidersTool

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
    plan_repository: PlanRulesRepository | None = None,
    retriever: PlanDocumentRetriever | None = None,
    provider_repository: ProviderRepository | None = None,
    calculator: BenefitCalculator | None = None,
) -> FastAPI:
    config = settings or Settings.from_environment()
    store = sessions or SessionStore()
    if repository is not None:
        members = repository
    elif config.member_repository == "dynamodb":
        members = DynamoDBMemberRepository(config.dynamodb_member_table)
    else:
        members = SyntheticMemberRepository()
    plans = plan_repository or LocalPlanRulesRepository()
    providers = provider_repository or SyntheticProviderRepository()
    if retriever is not None:
        documents = retriever
    elif config.rag_provider == "bedrock":
        documents = BedrockKnowledgeBaseRetriever(
            config.bedrock_knowledge_base_id, region=config.aws_region
        )
    else:
        documents = LocalPlanDocumentRetriever()
    member_tool = GetMemberTool(members)
    resolver = ResolveMemberIdTool(members)
    plan_context_tool = RetrievePlanContextTool(documents, members)
    provider_tool = SearchProvidersTool(providers, plans, members)
    calculator_tool = CalculateBenefitTool(
        calculator or BenefitCalculator(), members, plans, providers
    )

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
            tools=[
                resolver.definition,
                member_tool.definition,
                plan_context_tool.definition,
                provider_tool.definition,
                calculator_tool.definition,
            ],
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
        if body.tool_name == resolver.name:
            log_event("tool_invoked", tool=resolver.name)
            try:
                resolution = resolver.invoke(body.arguments)
            except ValidationError:
                raise HTTPException(422, "Invalid tool input or result") from None
            if resolution.member_id is not None:
                if session.member_id and session.member_id != resolution.member_id:
                    session.latest_correction = resolution.member_id
                session.member_id = resolution.member_id
            session.last_tool_call_id = body.tool_call_id
            session.last_tool_result = None
            log_event("member_id_resolved", status=resolution.status)
            return InvokeToolResponse(
                tool_name=resolver.name, tool_call_id=body.tool_call_id, result=resolution
            )
        result: ToolResult
        try:
            if body.tool_name == member_tool.name:
                member_result = member_tool.invoke(body.arguments)
                result = member_result
                if member_result.member is not None:
                    session.member_id = member_result.member.member_id
                    session.plan_id = member_result.member.plan_id
                    session.zip_code = member_result.member.zip_code
            elif body.tool_name == plan_context_tool.name:
                plan_result = plan_context_tool.invoke_with_member(
                    body.arguments, session.member_id
                )
                result = plan_result
                session.current_intent = "plan_explanation"
                session.citations = tuple(chunk.metadata for chunk in plan_result.chunks[:5])
            elif body.tool_name == provider_tool.name:
                provider_result = provider_tool.invoke_with_member(
                    body.arguments, session.member_id
                )
                result = provider_result
                session.current_intent = "provider_search"
                procedure = body.arguments.get("procedure")
                zip_code = body.arguments.get("zip_code")
                if isinstance(procedure, str):
                    session.procedure = procedure
                if isinstance(zip_code, str):
                    session.zip_code = zip_code
            elif body.tool_name == calculator_tool.name:
                calculation_result = calculator_tool.invoke_with_member(
                    body.arguments, session.member_id
                )
                result = calculation_result
                session.current_intent = "benefit_estimate"
                procedure = body.arguments.get("procedure")
                provider_id = body.arguments.get("provider_id")
                if isinstance(procedure, str):
                    if session.procedure and session.procedure != procedure:
                        session.latest_correction = procedure
                    session.procedure = procedure
                if isinstance(provider_id, str):
                    session.provider_id = provider_id
            else:
                raise HTTPException(400, "Unknown tool")
        except ValidationError:
            raise HTTPException(422, "Invalid tool input or result") from None
        log_event("tool_invoked", tool=body.tool_name)
        session.last_tool_call_id = body.tool_call_id
        session.last_tool_result = result
        log_event("tool_completed", tool=body.tool_name, status=result.status)
        return InvokeToolResponse(
            tool_name=body.tool_name,
            tool_call_id=body.tool_call_id,
            result=result,
        )

    return app
