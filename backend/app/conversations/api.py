"""Separate, demo-member-scoped app authentication. Never expose the internal token."""

import hmac
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import Field
from starlette.concurrency import run_in_threadpool

from app.config import Settings
from app.conversations import DEMO_MEMBER_ID, DISPLAY_NAME
from app.conversations.chat import ChatBusyError, TextChat
from app.conversations.inbox import Inbox
from app.conversations.models import ConversationDetail, ConversationList, MemberProfile
from app.models import StrictModel


class SendMessage(StrictModel):
    text: Annotated[str, Field(min_length=1, max_length=2000)]
    context_conversation_id: Annotated[str, Field(pattern=r"^[a-z0-9-]{1,64}$")] | None = None


def app_router(config: Settings, inbox: Inbox, chat: TextChat) -> APIRouter:
    async def authenticate(authorization: Annotated[str | None, Header()] = None) -> None:
        if not config.member_token:
            raise HTTPException(503, "App access is not configured")
        if not hmac.compare_digest(
            (authorization or "").encode(), f"Bearer {config.member_token}".encode()
        ):
            raise HTTPException(401, "Unauthorized")

    router = APIRouter(prefix="/api", dependencies=[Depends(authenticate)])

    @router.get("/conversations", response_model=ConversationList)
    def conversations(response: Response) -> ConversationList:
        response.headers["Cache-Control"] = "no-store"
        inbox.ensure_chat()
        return ConversationList(
            profile=MemberProfile(
                member_id=DEMO_MEMBER_ID,
                display_name=DISPLAY_NAME,
                call_number=config.demo_call_number,
            ),
            conversations=inbox.repository.list_conversations(DEMO_MEMBER_ID),
        )

    @router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
    def detail(conversation_id: str, response: Response) -> ConversationDetail:
        response.headers["Cache-Control"] = "no-store"
        value = inbox.repository.get_detail(DEMO_MEMBER_ID, conversation_id)
        if value is None:
            raise HTTPException(404, "Conversation not found")
        return value

    @router.post("/chat/messages", response_model=ConversationDetail)
    async def send(body: SendMessage, response: Response) -> ConversationDetail:
        if not body.text.strip():
            raise HTTPException(422, "A message is required")
        if (
            body.context_conversation_id
            and inbox.repository.get_conversation(DEMO_MEMBER_ID, body.context_conversation_id)
            is None
        ):
            raise HTTPException(404, "Conversation not found")
        response.headers["Cache-Control"] = "no-store"
        try:
            return await run_in_threadpool(
                chat.send, body.text.strip(), body.context_conversation_id
            )
        except ChatBusyError:
            raise HTTPException(409, "Abe is responding; please wait") from None

    return router
