from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from test_api import AUTH, TOKEN, start_session

from app.config import Settings
from app.conversation.sessions import SessionStore
from app.main import create_app
from app.retrieval.repository import PlanChunk, PlanScope


@pytest.fixture
def call() -> Iterator[tuple[TestClient, SessionStore, str]]:
    store = SessionStore()
    with TestClient(create_app(Settings(TOKEN), sessions=store)) as client:
        session_id = start_session(client)
        yield client, store, session_id


def invoke(
    client: TestClient, session_id: str, name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    response = client.post(
        f"/sessions/{session_id}/tools",
        headers=AUTH,
        json={"tool_name": name, "tool_call_id": "test-call", "arguments": arguments},
    )
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()["result"]
    return result


def lookup(client: TestClient, session_id: str) -> None:
    result = invoke(client, session_id, "resolve_member_id", {"spoken_id": "D E M O zero zero one"})
    assert result["member_id"] == "DEMO001"
    assert (
        invoke(client, session_id, "get_member", {"member_id": result["member_id"]})["member"][
            "annual_maximum_remaining"
        ]
        == 800
    )


def test_full_multi_tool_call_followups_citations_and_corrections(
    call: tuple[TestClient, SessionStore, str],
) -> None:
    client, store, sid = call
    lookup(client, sid)
    invoke(
        client,
        sid,
        "update_conversation_context",
        {
            "intent": "estimate",
            "procedure": "crown",
            "provider_id": "DEMO_P1",
            "zip_code": "27401",
            "constraints": ["prefer nearby"],
        },
    )
    plan = invoke(
        client, sid, "retrieve_plan_context", {"query": "what does my plan say about crowns?"}
    )
    assert plan["status"] == "verified"
    state = store.get(sid)
    assert state is not None and state.sources
    assert state.sources[0].page is None
    assert state.member_id == "DEMO001" and state.plan_id == "DEMO_DENTAL_PPO"
    providers = invoke(client, sid, "search_providers", {"radius_miles": 5, "procedure": "crown"})
    assert [p["provider_id"] for p in providers["providers"]] == ["DEMO_P1"]
    estimate = invoke(client, sid, "calculate_benefit", {"treatment_date": "2026-11-01"})
    assert estimate["plan_payment"] == "600.00"
    assert estimate["estimated_member_payment"] == "600.00"
    assert "Synthetic" in estimate["fee_source"]
    context = invoke(
        client,
        sid,
        "update_conversation_context",
        {"procedure": "root canal", "latest_correction": "root canal, not crown"},
    )
    assert "provider_id" not in context["context"]
    assert context["member_id"] == "DEMO001"
    incomplete = invoke(client, sid, "calculate_benefit", {"treatment_date": "2026-11-01"})
    assert "allowed_amount" in incomplete["missing_fields"]
    estimate2 = invoke(
        client, sid, "calculate_benefit", {"provider_id": "DEMO_P3", "treatment_date": "2026-11-01"}
    )
    assert estimate2["plan_payment"] == "800.00"
    assert estimate2["annual_maximum_remaining_afterward"] == "0.00"
    assert (
        invoke(client, sid, "get_member", {"member_id": "DEMO001"})["member"][
            "annual_maximum_remaining"
        ]
        == 800
    )
    assert client.delete(f"/sessions/{sid}", headers=AUTH).status_code == 204
    assert store.get(sid) is None


def test_no_member_does_not_select_demo_member_automatically(
    call: tuple[TestClient, SessionStore, str],
) -> None:
    client, _, sid = call
    for name, args in [
        ("retrieve_plan_context", {"query": "crowns"}),
        ("search_providers", {}),
        ("calculate_benefit", {}),
    ]:
        result = invoke(client, sid, name, args)
        assert result["missing_fields"] == ["member_id"]


def test_failed_id_does_not_become_valid_context_and_call_recovers(
    call: tuple[TestClient, SessionStore, str],
) -> None:
    client, store, sid = call
    lookup(client, sid)
    invoke(client, sid, "retrieve_plan_context", {"query": "crowns"})
    invoke(client, sid, "get_member", {"member_id": "UNKNOWN"})
    state = store.get(sid)
    assert state is not None and state.member_id is None and state.plan_id is None
    assert state.sources == []
    assert invoke(client, sid, "calculate_benefit", {})["missing_fields"] == ["member_id"]
    lookup(client, sid)
    assert invoke(client, sid, "retrieve_plan_context", {"query": "crowns"})["status"] == "verified"


def test_only_named_bounded_context_is_accepted_and_null_clears(
    call: tuple[TestClient, SessionStore, str],
) -> None:
    client, _, sid = call
    for args in [
        {"member_id": "DEMO001"},
        {"constraints": ["x"] * 6},
        {"intent": "x" * 201},
        {"transcript": "anything"},
    ]:
        response = client.post(
            f"/sessions/{sid}/tools",
            headers=AUTH,
            json={
                "tool_name": "update_conversation_context",
                "tool_call_id": "bad",
                "arguments": args,
            },
        )
        assert response.status_code == 422
    invoke(client, sid, "update_conversation_context", {"procedure": "crown"})
    result = invoke(client, sid, "update_conversation_context", {"procedure": None})
    assert result["context"] == {}


@pytest.mark.parametrize(
    "args,missing",
    [
        ({}, "procedure"),
        ({"procedure": "crown"}, "treatment_date"),
        (
            {
                "procedure": "crown",
                "provider_charge": "1400.00",
                "fee_source": "caller_quote",
                "treatment_date": "2026-11-01",
            },
            "allowed_amount",
        ),
        (
            {"procedure": "crown", "provider_id": "DEMO_P1", "treatment_date": "2026-02-30"},
            "treatment_date",
        ),
    ],
)
def test_specific_missing_inputs(
    call: tuple[TestClient, SessionStore, str], args: dict[str, Any], missing: str
) -> None:
    client, _, sid = call
    lookup(client, sid)
    result = invoke(client, sid, "calculate_benefit", args)
    assert result["status"] == "missing_information"
    assert missing in result["missing_fields"]


def test_caller_quote_provenance_and_conflicting_provider_facts(
    call: tuple[TestClient, SessionStore, str],
) -> None:
    client, _, sid = call
    lookup(client, sid)
    args = {
        "procedure": "crown",
        "treatment_date": "2026-11-01",
        "provider_charge": "1400.00",
        "allowed_amount": "1200.00",
        "network_status": "out_of_network",
        "fee_source": "caller_quote",
        "allowed_amount_source": "caller_reported_plan_allowed_amount",
        "network_source": "caller_reported",
    }
    result = invoke(client, sid, "calculate_benefit", args)
    assert result["plan_payment"] == "480.00"
    assert result["estimated_member_payment"] == "920.00"
    assert "unverified" in " ".join(result["warnings"])
    assert result["fee_source"] == "caller_quote"
    assert (
        invoke(client, sid, "calculate_benefit", {**args, "provider_id": "DEMO_P1"})["status"]
        == "unverified"
    )
    assert (
        invoke(
            client,
            sid,
            "calculate_benefit",
            {
                **args,
                "provider_id": "DEMO_P1",
                "network_status": "in_network",
                "allowed_amount": "1300",
            },
        )["status"]
        == "unverified"
    )


def test_rag_outage_is_sanitized_and_next_tool_works() -> None:
    class BrokenRetriever:
        def retrieve(self, query: str, scope: PlanScope) -> list[PlanChunk]:
            raise RuntimeError("sensitive upstream message")

    with TestClient(create_app(Settings(TOKEN), retriever=BrokenRetriever())) as client:
        sid = start_session(client)
        lookup(client, sid)
        result = invoke(client, sid, "retrieve_plan_context", {"query": "crowns"})
        assert result["status"] == "unavailable"
        assert "sensitive" not in str(result)
        assert invoke(client, sid, "get_member", {"member_id": "DEMO001"})["status"] == "success"


def test_session_busy_rejects_without_waiting(call: tuple[TestClient, SessionStore, str]) -> None:
    client, store, sid = call
    state = store.get(sid)
    assert state is not None
    with state.lock:
        response = client.post(
            f"/sessions/{sid}/tools",
            headers=AUTH,
            json={
                "tool_name": "get_member",
                "tool_call_id": "busy",
                "arguments": {"member_id": "DEMO001"},
            },
        )
        assert response.status_code == 409


def test_logs_do_not_include_member_data_token_or_query(
    call: tuple[TestClient, SessionStore, str], caplog: pytest.LogCaptureFixture
) -> None:
    client, _, sid = call
    lookup(client, sid)
    invoke(client, sid, "retrieve_plan_context", {"query": "unique private query"})
    assert all(
        value not in caplog.text
        for value in [TOKEN, "DEMO001", "unique private query", "Demo Member"]
    )


def test_procedure_alias_preserves_provider_but_correction_persists_before_estimate(
    call: tuple[TestClient, SessionStore, str],
) -> None:
    client, store, sid = call
    lookup(client, sid)
    invoke(
        client, sid, "update_conversation_context", {"procedure": "crown", "provider_id": "DEMO_P1"}
    )
    result = invoke(
        client,
        sid,
        "calculate_benefit",
        {"procedure": "dental crown", "treatment_date": "2026-11-01"},
    )
    assert result["status"] == "estimated"
    changed = invoke(client, sid, "calculate_benefit", {"procedure": "root canal"})
    assert changed["status"] == "missing_information"
    state = store.get(sid)
    assert state is not None
    assert state.context["procedure"] == "ROOT_CANAL"
    assert "provider_id" not in state.context
    assert state.sources == []
    followup = invoke(
        client, sid, "calculate_benefit", {"provider_id": "DEMO_P3", "treatment_date": "2026-11-01"}
    )
    assert followup["procedure"] == "ROOT_CANAL"


def test_provider_outage_and_unknown_rules_are_controlled() -> None:
    from app.benefits.models import PlanRules
    from app.benefits.repository import LocalPlanRulesRepository
    from app.providers.repository import Provider, ProviderSearch, SyntheticProviderRepository

    class BrokenProviders(SyntheticProviderRepository):
        def search(self, plan_id: str, query: ProviderSearch) -> list[Provider]:
            raise RuntimeError("private provider failure")

    class EmptyPlans(LocalPlanRulesRepository):
        def get_plan(
            self, plan_id: str, employer_id: str | None, plan_year: int | None
        ) -> PlanRules | None:
            return None

    with TestClient(create_app(Settings(TOKEN), providers=BrokenProviders())) as client:
        sid = start_session(client)
        lookup(client, sid)
        result = invoke(client, sid, "search_providers", {})
        assert result["status"] == "unavailable"
        assert "private" not in str(result)
        assert (
            invoke(client, sid, "retrieve_plan_context", {"query": "crowns"})["status"]
            == "verified"
        )
    with TestClient(create_app(Settings(TOKEN), plans=EmptyPlans())) as client:
        sid = start_session(client)
        lookup(client, sid)
        assert invoke(client, sid, "calculate_benefit", {})["status"] == "unverified"


def test_synthetic_mode_does_not_construct_aws_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.aws

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Local mode must not use AWS")

    monkeypatch.setattr(app.aws, "aws_client", forbidden)
    with TestClient(create_app(Settings(TOKEN))) as client:
        sid = start_session(client)
        lookup(client, sid)
        assert (
            invoke(client, sid, "retrieve_plan_context", {"query": "crowns"})["status"]
            == "verified"
        )
