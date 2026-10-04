from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.conversation.sessions import SessionStore
from app.main import create_app
from app.members.repository import Member

TOKEN = "unit-test-token-is-not-a-real-secret-1234"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
CALL_ID = "CA" + "a" * 32


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app(Settings(internal_token=TOKEN))) as test_client:
        yield test_client


def start_session(client: TestClient) -> str:
    response = client.post("/sessions", json={"call_id": CALL_ID}, headers=AUTH)
    assert response.status_code == 200
    result = response.json()
    assert isinstance(result["session_id"], str)
    return result["session_id"]


def test_health_and_private_endpoint_authentication(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}
    assert client.post("/sessions", json={"call_id": CALL_ID}).status_code == 401
    bad_auth = {"Authorization": "Bearer invalid"}
    assert client.post("/sessions", json={"call_id": CALL_ID}, headers=bad_auth).status_code == 401
    session_id = start_session(client)
    assert client.delete(f"/sessions/{session_id}").status_code == 401
    assert client.post(f"/sessions/{session_id}/tools", json={}).status_code == 401


def test_session_prompt_and_allowlisted_tool(client: TestClient) -> None:
    response = client.post("/sessions", json={"call_id": CALL_ID}, headers=AUTH).json()
    assert "AI benefits assistant" in response["system_prompt"]
    assert "no identity verification" in response["system_prompt"]
    assert "annual_maximum_remaining" in response["system_prompt"]
    assert [tool["name"] for tool in response["tools"]] == [
        "resolve_member_id",
        "get_member",
        "retrieve_plan_context",
        "search_providers",
        "calculate_benefit",
    ]


def test_end_to_end_get_member_and_cleanup(client: TestClient) -> None:
    session_id = start_session(client)
    body = {
        "tool_name": "get_member",
        "tool_call_id": "tool-1",
        "arguments": {"member_id": "DEMO001"},
    }
    response = client.post(f"/sessions/{session_id}/tools", headers=AUTH, json=body)
    assert response.status_code == 200
    assert response.json()["result"]["member"]["annual_maximum_remaining"] == 800
    assert response.json()["tool_call_id"] == "tool-1"
    assert "member_id" not in response.json()["result"]
    assert client.delete(f"/sessions/{session_id}", headers=AUTH).status_code == 204
    assert client.post(f"/sessions/{session_id}/tools", headers=AUTH, json=body).status_code == 404
    assert client.delete(f"/sessions/{session_id}", headers=AUTH).status_code == 204


def test_unknown_member_is_normal_tool_result(client: TestClient) -> None:
    session_id = start_session(client)
    response = client.post(
        f"/sessions/{session_id}/tools",
        headers=AUTH,
        json={
            "tool_name": "get_member",
            "tool_call_id": "tool-2",
            "arguments": {"member_id": "UNKNOWN"},
        },
    )
    assert response.status_code == 200
    assert response.json()["result"] == {"status": "not_found", "member_id": "UNKNOWN"}


def test_spoken_id_resolution_then_canonical_lookup(client: TestClient) -> None:
    session_id = start_session(client)
    endpoint = f"/sessions/{session_id}/tools"
    response = client.post(
        endpoint,
        headers=AUTH,
        json={
            "tool_name": "resolve_member_id",
            "tool_call_id": "resolve-1",
            "arguments": {"spoken_id": "demo double zero one"},
        },
    )
    assert response.status_code == 200
    assert response.json()["result"] == {"status": "resolved", "member_id": "DEMO001"}
    lookup = client.post(
        endpoint,
        headers=AUTH,
        json={
            "tool_name": "get_member",
            "tool_call_id": "lookup-1",
            "arguments": {"member_id": response.json()["result"]["member_id"]},
        },
    )
    assert lookup.json()["result"]["member"]["annual_maximum_remaining"] == 800
    repeat = client.post(
        endpoint,
        headers=AUTH,
        json={
            "tool_name": "resolve_member_id",
            "tool_call_id": "resolve-2",
            "arguments": {"spoken_id": "maybe demo zero zero one"},
        },
    )
    assert repeat.json()["result"] == {"status": "repeat"}


def test_member_context_survives_followups_invalid_ids_and_corrections() -> None:
    store = SessionStore()
    with TestClient(create_app(Settings(TOKEN), sessions=store)) as client:
        session_id = start_session(client)
        endpoint = f"/sessions/{session_id}/tools"
        lookup = client.post(
            endpoint,
            headers=AUTH,
            json={
                "tool_name": "get_member",
                "tool_call_id": "lookup",
                "arguments": {"member_id": "DEMO001"},
            },
        )
        assert lookup.status_code == 200
        invalid = client.post(
            endpoint,
            headers=AUTH,
            json={
                "tool_name": "resolve_member_id",
                "tool_call_id": "bad-correction",
                "arguments": {"spoken_id": "demo zero zero two"},
            },
        )
        assert invalid.json()["result"] == {"status": "repeat"}
        context = client.post(
            endpoint,
            headers=AUTH,
            json={
                "tool_name": "retrieve_plan_context",
                "tool_call_id": "followup",
                "arguments": {"query": "What does that annual maximum mean?"},
            },
        )
        assert context.status_code == 200
        assert context.json()["result"]["status"] == "verified"
        session = store.get(session_id)
        assert session is not None
        assert session.member_id == "DEMO001"
        assert session.plan_id == "DEMO_DENTAL_PPO"
        assert session.citations[0].source_id == "synthetic-demo-plan-2026"


def test_valid_member_correction_replaces_prior_context() -> None:
    from app.members.repository import SyntheticMemberRepository

    demo = SyntheticMemberRepository().get_member("DEMO001")
    assert demo is not None
    known_demo: Member = demo

    class TwoMemberRepository:
        def get_member(self, member_id: str) -> Member | None:
            if member_id == "DEMO001":
                return known_demo
            if member_id == "DEMO002":
                return known_demo.model_copy(update={"member_id": "DEMO002"})
            return None

    store = SessionStore()
    app = create_app(Settings(TOKEN), repository=TwoMemberRepository(), sessions=store)
    with TestClient(app) as client:
        session_id = start_session(client)
        endpoint = f"/sessions/{session_id}/tools"
        for spoken in ["demo zero zero two", "demo zero zero one"]:
            response = client.post(
                endpoint,
                headers=AUTH,
                json={
                    "tool_name": "resolve_member_id",
                    "tool_call_id": spoken.replace(" ", "-"),
                    "arguments": {"spoken_id": spoken},
                },
            )
            assert response.json()["result"]["status"] == "resolved"
        session = store.get(session_id)
        assert session is not None
        assert session.member_id == "DEMO001"
        assert session.latest_correction == "DEMO001"


def test_provider_search_and_calculator_use_known_session_member(client: TestClient) -> None:
    session_id = start_session(client)
    endpoint = f"/sessions/{session_id}/tools"
    client.post(
        endpoint,
        headers=AUTH,
        json={
            "tool_name": "get_member",
            "tool_call_id": "member",
            "arguments": {"member_id": "DEMO001"},
        },
    )
    providers = client.post(
        endpoint,
        headers=AUTH,
        json={
            "tool_name": "search_providers",
            "tool_call_id": "providers",
            "arguments": {
                "procedure": "crown",
                "network_status": "in_network",
                "radius_miles": 5,
            },
        },
    )
    assert providers.status_code == 200
    assert providers.json()["result"]["providers"][0]["provider"]["provider_id"] == "SYNTH001"
    estimate = client.post(
        endpoint,
        headers=AUTH,
        json={
            "tool_name": "calculate_benefit",
            "tool_call_id": "estimate",
            "arguments": {
                "procedure": "crown",
                "treatment_date": "2026-06-01",
                "provider_id": "SYNTH001",
            },
        },
    )
    assert estimate.status_code == 200
    result = estimate.json()["result"]
    assert result["status"] == "estimated"
    assert result["plan_payment"] == "600.00"
    assert result["estimated_member_payment"] == "800.00"
    assert result["annual_maximum_remaining_after"] == "200.00"


def test_calculator_requests_exact_missing_values_without_inventing_fees(
    client: TestClient,
) -> None:
    session_id = start_session(client)
    response = client.post(
        f"/sessions/{session_id}/tools",
        headers=AUTH,
        json={
            "tool_name": "calculate_benefit",
            "tool_call_id": "missing",
            "arguments": {"member_id": "DEMO001", "procedure": "filling"},
        },
    )
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["status"] == "missing_input"
    assert result["missing"] == [
        "treatment_date",
        "network_status",
        "provider_charge",
        "allowed_amount",
        "fee_source",
    ]
    unknown_provider = client.post(
        f"/sessions/{session_id}/tools",
        headers=AUTH,
        json={
            "tool_name": "calculate_benefit",
            "tool_call_id": "unknown-provider",
            "arguments": {
                "member_id": "DEMO001",
                "procedure": "filling",
                "treatment_date": "2026-06-01",
                "provider_id": "NOTREAL",
            },
        },
    )
    assert unknown_provider.json()["result"] == {
        "status": "unavailable",
        "missing": [],
        "message": "The requested provider record is unavailable.",
    }


@pytest.mark.parametrize("arguments", [{}, {"member_id": 123}, {"member_id": "X", "extra": 1}])
def test_invalid_tool_arguments_are_safe_errors(
    client: TestClient, arguments: dict[str, Any]
) -> None:
    session_id = start_session(client)
    response = client.post(
        f"/sessions/{session_id}/tools",
        headers=AUTH,
        json={
            "tool_name": "get_member",
            "tool_call_id": "tool-3",
            "arguments": arguments,
        },
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid tool input or result"}


def test_unknown_tool_and_missing_session(client: TestClient) -> None:
    session_id = start_session(client)
    body = {"tool_name": "run_shell", "tool_call_id": "tool-4", "arguments": {}}
    assert client.post(f"/sessions/{session_id}/tools", headers=AUTH, json=body).status_code == 400
    assert client.post(f"/sessions/{uuid4()}/tools", headers=AUTH, json=body).status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"call_id": "not-a-call"},
        {"call_id": CALL_ID, "secret": "never-echo-this"},
        {"call_id": 12},
        {},
    ],
)
def test_invalid_requests_do_not_echo_inputs(client: TestClient, body: dict[str, Any]) -> None:
    response = client.post("/sessions", headers=AUTH, json=body)
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid request"}


def test_session_capacity_and_expiry() -> None:
    now = [0.0]
    store = SessionStore(capacity=1, idle_ttl_seconds=1, clock=lambda: now[0])
    with TestClient(create_app(Settings(TOKEN), sessions=store)) as client:
        session_id = start_session(client)
        response = client.post("/sessions", headers=AUTH, json={"call_id": "CA" + "b" * 32})
        assert response.status_code == 503
        now[0] = 1
        response = client.post(
            f"/sessions/{session_id}/tools",
            headers=AUTH,
            json={
                "tool_name": "get_member",
                "tool_call_id": "tool-5",
                "arguments": {"member_id": "DEMO001"},
            },
        )
        assert response.status_code == 404
        assert start_session(client) != session_id


def test_unexpected_failure_is_sanitized() -> None:
    class BrokenRepository:
        def get_member(self, member_id: str) -> Member | None:
            raise RuntimeError("sensitive internal failure")

    app = create_app(Settings(TOKEN), repository=BrokenRepository())
    with TestClient(app, raise_server_exceptions=False) as client:
        session_id = start_session(client)
        response = client.post(
            f"/sessions/{session_id}/tools",
            headers=AUTH,
            json={
                "tool_name": "get_member",
                "tool_call_id": "tool-6",
                "arguments": {"member_id": "DEMO001"},
            },
        )
        assert response.status_code == 500
        assert response.json() == {"detail": "Service unavailable"}


def test_session_stores_only_last_result() -> None:
    store = SessionStore()
    with TestClient(create_app(Settings(TOKEN), sessions=store)) as client:
        session_id = start_session(client)
        for member_id in ["DEMO001", "UNKNOWN"]:
            client.post(
                f"/sessions/{session_id}/tools",
                headers=AUTH,
                json={
                    "tool_name": "get_member",
                    "tool_call_id": member_id,
                    "arguments": {"member_id": member_id},
                },
            )
        session = store.get(session_id)
        assert session is not None
        assert session.member_id == "DEMO001"
        assert session.last_tool_result is not None
        assert session.last_tool_result.status == "not_found"
    assert store.get(session_id) is None


@pytest.mark.parametrize("token", ["", "short", "has space" * 8, "é" * 40])
def test_configuration_rejects_unsafe_tokens(token: str) -> None:
    with pytest.raises(ValueError, match="ABE_INTERNAL_TOKEN"):
        Settings(token)


def test_aws_repository_modes_require_explicit_resource_ids() -> None:
    with pytest.raises(ValueError, match="DYNAMODB_MEMBER_TABLE"):
        Settings(TOKEN, member_repository="dynamodb")
    with pytest.raises(ValueError, match="BEDROCK_KNOWLEDGE_BASE_ID"):
        Settings(TOKEN, rag_provider="bedrock")
