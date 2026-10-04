from app.conversation.sessions import Session
from app.conversations import CHAT_CONVERSATION_ID, DEMO_MEMBER_ID
from app.conversations.models import Conversation, Recap, SuggestedAction
from app.conversations.recap import conversation_preview, conversation_tag, conversation_title
from app.conversations.repository import ConversationRepository, new_message, now_iso


class Inbox:
    def __init__(self, repository: ConversationRepository) -> None:
        self.repository = repository

    def ensure_chat(self) -> Conversation:
        existing = self.repository.get_conversation(DEMO_MEMBER_ID, CHAT_CONVERSATION_ID)
        if existing is not None:
            return existing
        conversation = Conversation(
            conversation_id=CHAT_CONVERSATION_ID,
            member_id=DEMO_MEMBER_ID,
            channel="text",
            pinned=True,
            title="Chat with Abe",
            tag="Plan AI",
            icon="abe",
            preview="Ask Abe about remaining benefits, estimates, and timing.",
            updated_at=now_iso(),
        )
        self.repository.put_conversation(conversation)
        self.repository.append_message(
            new_message(
                CHAT_CONVERSATION_ID,
                "assistant",
                "Hi Sarah. I'm Abe, your AI dental benefits guide. How can I help?",
                "text",
            )
        )
        found = self.repository.get_conversation(DEMO_MEMBER_ID, CHAT_CONVERSATION_ID)
        assert found is not None
        return found

    def start_voice(self, session: Session) -> None:
        self.ensure_chat()
        if session.conversation_id:
            return
        conversation_id = session.session_id
        session.conversation_id = conversation_id
        self.repository.put_conversation(
            Conversation(
                conversation_id=conversation_id,
                member_id=DEMO_MEMBER_ID,
                channel="voice",
                status="in_progress",
                title="Abe call",
                tag="Call",
                icon="hospital",
                preview="Voice conversation in progress.",
                updated_at=now_iso(),
            )
        )

    def append_transcript(self, session: Session, role: str, text: str, event_id: str) -> None:
        if session.conversation_id is None:
            self.start_voice(session)
        assert session.conversation_id is not None
        self.repository.append_message(
            new_message(session.conversation_id, role, text, "voice").model_copy(
                update={"message_id": event_id}
            )
        )

    def complete_voice(
        self, session: Session, recap: Recap | None, actions: list[SuggestedAction]
    ) -> None:
        if session.conversation_id is None:
            self.start_voice(session)
        assert session.conversation_id is not None
        current = self.repository.get_conversation(DEMO_MEMBER_ID, session.conversation_id)
        if current is None:
            return
        fallback = current.preview
        self.repository.put_conversation(
            current.model_copy(
                update={
                    "status": "partial" if current.status == "partial" else "completed",
                    "title": conversation_title(recap)[:80],
                    "tag": conversation_tag(recap)[:40],
                    "recap": recap,
                    "actions": actions,
                    "preview": conversation_preview(recap, fallback),
                    "updated_at": now_iso(),
                }
            )
        )
