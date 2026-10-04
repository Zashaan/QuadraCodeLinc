from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from test_api import AUTH, TOKEN, start_session
from test_benefit_api import invoke, lookup

from app.config import Settings
from app.conversations import CHAT_CONVERSATION_ID, DEMO_MEMBER_ID
from app.conversations.chat import BedrockTextModel
from app.conversations.repository import SyntheticConversationRepository, new_message
from app.conversations.sqlite_repository import SQLiteConversationRepository
from app.main import create_app
from app.tools.base import ToolDefinition

APP_TOKEN = "app-test-key-separate-from-internal-123456"
APP_AUTH = {"Authorization": f"Bearer {APP_TOKEN}"}
SETTINGS = Settings(TOKEN, member_token=APP_TOKEN)


def reply(text: str) -> dict[str, Any]:
    return {
        "output": {"message": {"role": "assistant", "content": [{"text": text}]}},
        "stopReason": "end_turn",
    }


class ScriptedModel:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = iter(responses)
        self.requests: list[list[dict[str, Any]]] = []

    def converse(
        self, messages: list[dict[str, Any]], system: str, tools: list[ToolDefinition]
    ) -> dict[str, Any]:
        self.requests.append(list(messages))
        return next(self.responses)


def tool_reply(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "output": {
            "message": {
                "role": "assistant",
                "content": [{"toolUse": {"toolUseId": "call-1", "name": name, "input": arguments}}],
            }
        },
        "stopReason": "tool_use",
    }


def test_separate_app_auth_and_member_scope() -> None:
    repo = SyntheticConversationRepository()
    seed = repo.list_conversations(DEMO_MEMBER_ID)[0]
    repo.put_conversation(
        seed.model_copy(update={"conversation_id": "private", "member_id": "OTHER"})
    )
    with TestClient(create_app(SETTINGS, conversations=repo)) as client:
        assert client.get("/api/conversations").status_code == 401
        assert client.get("/api/conversations", headers=AUTH).status_code == 401
        response = client.get("/api/conversations", headers=APP_AUTH)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert all(c["member_id"] == DEMO_MEMBER_ID for c in response.json()["conversations"])
        assert client.get("/api/conversations/private", headers=APP_AUTH).status_code == 404
        assert client.post("/sessions", headers=APP_AUTH, json={}).status_code == 401
    with TestClient(create_app(Settings(TOKEN))) as client:
        assert client.get("/api/conversations", headers=APP_AUTH).status_code == 503


def test_transcript_lifecycle_idempotence_and_verified_recap(tmp_path: Path) -> None:
    path = tmp_path / "conversations.db"
    repo = SQLiteConversationRepository(path)
    with TestClient(create_app(SETTINGS, conversations=repo)) as client:
        sid = start_session(client)
        lookup(client, sid)
        invoke(
            client,
            sid,
            "update_conversation_context",
            {"procedure": "crown", "provider_id": "DEMO_P1"},
        )
        estimate = invoke(client, sid, "calculate_benefit", {"treatment_date": "2026-11-01"})
        batch = {
            "events": [{"event_id": "utterance-1", "role": "user", "text": "What about my crown?"}]
        }
        endpoint = f"/sessions/{sid}/transcripts"
        assert client.post(endpoint, headers=APP_AUTH, json=batch).status_code == 401
        for _ in range(2):
            assert client.post(endpoint, headers=AUTH, json=batch).status_code == 204
        assert client.delete(f"/sessions/{sid}", headers=AUTH).status_code == 204
        response = client.get(f"/api/conversations/{sid}", headers=APP_AUTH).json()
        assert len(response["messages"]) == 1
        recap = response["conversation"]["recap"]
        assert recap["origin"] == "tool_result"
        assert recap["estimated_member_payment"] == estimate["estimated_member_payment"]
        assert recap["annual_maximum_remaining"] == "800.00"
        assert recap["provider_name"] and "Synthetic" in recap["provider_name"]
        assert response["conversation"]["status"] == "completed"
        assert client.post(endpoint, headers=AUTH, json=batch).status_code == 404
    reopened = SQLiteConversationRepository(path).get_detail(DEMO_MEMBER_ID, sid)
    assert reopened and len(reopened.messages) == 1
    assert reopened.conversation.recap and reopened.conversation.recap.fsa_balance == "350.00"
    assert path.stat().st_mode & 0o777 == 0o600


def test_partial_and_abandoned_calls_are_labeled(tmp_path: Path) -> None:
    path = tmp_path / "conversations.db"
    with TestClient(
        create_app(SETTINGS, conversations=SQLiteConversationRepository(path))
    ) as client:
        sid = start_session(client)
        endpoint = f"/sessions/{sid}/transcripts"
        assert (
            client.post(endpoint, headers=AUTH, json={"events": [], "incomplete": True}).status_code
            == 204
        )
        client.delete(f"/sessions/{sid}", headers=AUTH)
        assert (
            client.get(f"/api/conversations/{sid}", headers=APP_AUTH).json()["conversation"][
                "status"
            ]
            == "partial"
        )
        unfinished = start_session(client)
    reopened = SQLiteConversationRepository(path).get_conversation(DEMO_MEMBER_ID, unfinished)
    assert reopened and reopened.status == "partial"


def test_member_correction_clears_estimates() -> None:
    with TestClient(create_app(SETTINGS)) as client:
        sid = start_session(client)
        lookup(client, sid)
        invoke(
            client,
            sid,
            "calculate_benefit",
            {"procedure": "crown", "provider_id": "DEMO_P1", "treatment_date": "2026-11-01"},
        )
        invoke(client, sid, "get_member", {"member_id": "UNKNOWN"})
        client.delete(f"/sessions/{sid}", headers=AUTH)
        assert (
            client.get(f"/api/conversations/{sid}", headers=APP_AUTH).json()["conversation"][
                "recap"
            ]
            is None
        )


def test_chat_uses_tools_and_persists_both_sides() -> None:
    model = ScriptedModel(
        [
            tool_reply("get_member", {"member_id": DEMO_MEMBER_ID}),
            reply("Your demo annual maximum remaining is $800."),
        ]
    )
    with TestClient(create_app(SETTINGS, text_model=model)) as client:
        result = client.post("/api/chat/messages", headers=APP_AUTH, json={"text": "What is left?"})
        assert result.status_code == 200
        messages = result.json()["messages"]
        assert messages[-2]["text"] == "What is left?"
        assert messages[-1]["text"] == "Your demo annual maximum remaining is $800."
        tool = model.requests[1][-1]["content"][0]["toolResult"]
        assert tool["status"] == "success"
        assert tool["content"][0]["json"]["member"]["annual_maximum_remaining"] == 800
        assert (
            client.get(f"/api/conversations/{CHAT_CONVERSATION_ID}", headers=APP_AUTH).json()[
                "messages"
            ]
            == messages
        )


@pytest.mark.parametrize(
    "name,arguments", [("run_shell", {}), ("get_member", {"member_id": "OTHER"})]
)
def test_chat_refuses_unallowed_tools(name: str, arguments: dict[str, Any]) -> None:
    model = ScriptedModel([tool_reply(name, arguments), reply("I can only use this demo member.")])
    with TestClient(create_app(SETTINGS, text_model=model)) as client:
        assert (
            client.post(
                "/api/chat/messages", headers=APP_AUTH, json={"text": "Try another member"}
            ).status_code
            == 200
        )
        assert model.requests[1][-1]["content"][0]["toolResult"]["status"] == "error"


@pytest.mark.parametrize("responses", [[], [reply("")], [reply("x" * 4001)]])
def test_chat_failure_keeps_message_without_fabricating_answer(
    responses: list[dict[str, Any]],
) -> None:
    with TestClient(create_app(SETTINGS, text_model=ScriptedModel(responses))) as client:
        result = client.post("/api/chat/messages", headers=APP_AUTH, json={"text": "Help"})
        assert result.status_code == 200
        assert result.json()["messages"][-2]["text"] == "Help"
        assert "couldn't reach" in result.json()["messages"][-1]["text"]
        assert (
            client.post("/api/chat/messages", headers=APP_AUTH, json={"text": "   "}).status_code
            == 422
        )
        assert (
            client.post(
                "/api/chat/messages",
                headers=APP_AUTH,
                json={"text": "Hi", "context_conversation_id": "missing"},
            ).status_code
            == 404
        )


def test_chat_tool_loop_is_bounded() -> None:
    model = ScriptedModel([tool_reply("get_member", {"member_id": DEMO_MEMBER_ID})] * 8)
    with TestClient(create_app(SETTINGS, text_model=model)) as client:
        result = client.post("/api/chat/messages", headers=APP_AUTH, json={"text": "Keep going"})
        assert result.status_code == 200 and len(model.requests) == 4
        assert "one procedure" in result.json()["messages"][-1]["text"]


def test_sqlite_keeps_latest_500_messages_in_order(tmp_path: Path) -> None:
    repo = SQLiteConversationRepository(tmp_path / "test.db")
    for i in range(505):
        repo.append_message(new_message(CHAT_CONVERSATION_ID, "user", str(i), "text"))
    detail = repo.get_detail(DEMO_MEMBER_ID, CHAT_CONVERSATION_ID)
    assert detail and len(detail.messages) == 500
    assert detail.messages[0].text == "5" and detail.messages[-1].text == "504"
    assert repo.get_detail("OTHER", CHAT_CONVERSATION_ID) is None


def test_bedrock_converse_sdk_contract() -> None:
    import boto3  # type: ignore[import-untyped]
    from botocore.stub import Stubber  # type: ignore[import-untyped]

    client = boto3.client(
        "bedrock-runtime",
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    response = reply("Hello") | {
        "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
        "metrics": {"latencyMs": 1},
    }
    messages = [{"role": "user", "content": [{"text": "Hi"}]}]
    definition = ToolDefinition(
        name="get_member", description="Read member", input_schema={"type": "object"}
    )
    expected: dict[str, Any] = {
        "modelId": "us.amazon.nova-2-lite-v1:0",
        "messages": messages,
        "system": [{"text": "AI"}],
        "inferenceConfig": {"maxTokens": 700, "temperature": 0.3},
        "toolConfig": {
            "tools": [
                {
                    "toolSpec": {
                        "name": "get_member",
                        "description": "Read member",
                        "inputSchema": {"json": {"type": "object"}},
                    }
                }
            ]
        },
    }
    with Stubber(client) as stub:
        stub.add_response("converse", response, expected)
        assert (
            BedrockTextModel(expected["modelId"], "us-east-1", client).converse(
                messages, "AI", [definition]
            )
            == response
        )
