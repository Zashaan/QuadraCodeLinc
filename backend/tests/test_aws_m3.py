import copy
import io
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.aws_data import read_all
from app.benefits.repository import DynamoDBPlanRulesRepository
from app.config import Settings
from app.history.repository import DynamoDBHistoryRepository
from app.members.repository import DynamoDBMemberRepository
from app.procedures.repository import DynamoDBProcedureRepository
from app.providers.repository import DynamoDBProviderRepository, ProviderSearch
from app.retrieval.repository import BedrockKnowledgeBaseRetriever, PlanScope

DATA = json.loads((Path(__file__).parent / "fixtures/aws_demo.json").read_text())


def numbers(value: Any, field: str = "") -> Any:
    if isinstance(value, dict):
        return {k: numbers(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [numbers(v) for v in value]
    if isinstance(value, str) and field not in {"zip", "zip_code"}:
        try:
            return Decimal(value)
        except Exception:
            return value
    return value


class Table:
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = numbers(copy.deepcopy(records))
        self.requests: list[dict[str, Any]] = []

    def get_item(self, **kwargs: Any) -> dict[str, Any]:
        self.requests.append(kwargs)
        item = next(
            (r for r in self.records if all(r.get(k) == v for k, v in kwargs["Key"].items())), None
        )
        return {"Item": item} if item is not None else {}

    def scan(self, **kwargs: Any) -> dict[str, Any]:
        self.requests.append(kwargs)
        return {"Items": self.records}

    def query(self, **kwargs: Any) -> dict[str, Any]:
        self.requests.append(kwargs)
        return {"Items": self.records}


def test_live_shaped_member_has_dynamic_plan_and_unknown_optional_facts() -> None:
    table = Table(DATA["abe-members"])
    member = DynamoDBMemberRepository("abe-members", table=table).get_member("DEMO001")
    assert member and member.plan_id == "HC-PLUS" and member.name == "Jordan Lee"
    assert (member.annual_maximum, member.annual_maximum_used, member.annual_maximum_remaining) == (
        1500,
        700,
        800,
    )
    assert member.fsa_balance is None and member.zip_code is None and member.plan_year is None
    assert table.requests[0]["Key"] == {"member_id": "DEMO001"}
    table.records[0]["member_status"] = "invented"
    with pytest.raises(ValidationError):
        DynamoDBMemberRepository("abe-members", table=table).get_member("DEMO001")


def test_joined_plan_rules_and_procedure_catalog_use_requested_plan() -> None:
    plans, rules, procedures = (
        Table(DATA["abe-plans"]),
        Table(DATA["abe-coverage-rules"]),
        Table(DATA["abe-procedures"]),
    )
    catalog = DynamoDBProcedureRepository("abe-procedures", table=procedures)
    assert catalog.get_procedure("D2740") is not None
    repository = DynamoDBPlanRulesRepository(
        "abe-plans",
        "abe-coverage-rules",
        "abe-procedures",
        table=plans,
        coverage_table=rules,
        procedure_repository=catalog,
    )
    plan = repository.get_plan("HC-PLUS")
    assert plan and plan.procedure_id("crown") == "D2740"
    assert plan.networks["in_network"].categories["major"].plan_share == Decimal("0.5")
    assert plan.procedures["D2740"].waiting_period_months == 6
    assert plan.starts_on is None and plan.plan_year is None
    assert plans.requests[0]["Key"] == {"plan_id": "HC-PLUS"}
    assert "KeyConditionExpression" in rules.requests[0]
    assert repository.get_plan("MISSING") is None
    rules.records[0]["plan_id"] = "WRONG"
    with pytest.raises(ValueError, match="Mismatched"):
        repository.get_plan("HC-PLUS")


def test_provider_directory_does_not_invent_prices_distance_or_plan_network() -> None:
    repo = DynamoDBProviderRepository("abe-providers", table=Table(DATA["abe-providers"]))
    provider = repo.get_provider("DEN1001", "HC-PLUS")
    assert provider and provider.fees == {} and provider.distances_miles == {}
    assert provider.network_scope == "dataset_global" and provider.available_dates is None
    assert repo.search("HC-PLUS", ProviderSearch(radius_miles=20, zip_code="08901")) == []
    assert len(repo.search("HC-PLUS", ProviderSearch(procedure="D2740"))) == 1


def test_claims_and_authorizations_require_complete_member_scoped_history() -> None:
    claims = Table(DATA["abe-claims"])
    auths = Table([])
    repo = DynamoDBHistoryRepository(
        "abe-claims", "abe-authorizations", claim_table=claims, authorization_table=auths
    )
    history = repo.get_history("DEMO001")
    assert len(history.claims) == 3 and history.claims_complete and history.authorizations_complete
    assert "FilterExpression" in claims.requests[0]
    auths.records = numbers(DATA["abe-authorizations"])
    with pytest.raises(ValueError, match="Mismatched member"):
        repo.get_authorizations("DEMO001")
    member_id = str(auths.records[0]["member_id"])
    assert repo.get_authorizations(member_id)[0].status == "denied"


def test_paginated_reads_never_accept_truncated_or_looping_results() -> None:
    class Paged:
        def __init__(self) -> None:
            self.calls = 0

        def scan(self, **kwargs: Any) -> dict[str, Any]:
            self.calls += 1
            if self.calls == 1:
                return {"Items": [], "ScannedCount": 1, "LastEvaluatedKey": {"key": "a"}}
            assert kwargs["ExclusiveStartKey"] == {"key": "a"}
            return {"Items": [{"id": "b"}]}

    assert read_all(Paged()) == [{"id": "b"}]

    class Loop:
        def scan(self, **kwargs: Any) -> dict[str, Any]:
            return {"Items": [], "LastEvaluatedKey": {"key": "a"}}

    with pytest.raises(ValueError, match="complete"):
        read_all(Loop())


def test_manifest_rag_rejects_cross_plan_sources_and_mixed_global_excerpts() -> None:
    manifest = [
        {
            "file": "plus.pdf",
            "document_id": "plus",
            "title": "Plus Benefits",
            "plan_id": "HC-PLUS",
            "document_type": "summary",
        },
        {
            "file": "basic.pdf",
            "document_id": "basic",
            "title": "Basic Benefits",
            "plan_id": "HC-BASIC",
            "document_type": "summary",
        },
        {
            "file": "global.pdf",
            "document_id": "global",
            "title": "Network Guide",
            "plan_id": None,
            "document_type": "guide",
        },
    ]

    class S3:
        def get_object(self, **kwargs: Any) -> dict[str, Any]:
            return {"Body": io.BytesIO(json.dumps(manifest).encode())}

    class Bedrock:
        def retrieve(self, **kwargs: Any) -> dict[str, Any]:
            assert "HC-PLUS Plus Benefits" in kwargs["retrievalQuery"]["text"]
            assert "filter" not in kwargs["retrievalConfiguration"]["vectorSearchConfiguration"]
            return {
                "retrievalResults": [
                    {
                        "content": {"text": text},
                        "location": {"s3Location": {"uri": f"s3://demo/rag/source/{file}"}},
                    }
                    for file, text in [
                        ("basic.pdf", "Wrong plan"),
                        ("global.pdf", "HC-BASIC has other rules"),
                        ("plus.pdf", "HC-PLUS annual maximum"),
                        ("global.pdf", "Allowed amounts differ from charges"),
                        ("unknown.pdf", "Untrusted source"),
                    ]
                ]
            }

    repo = BedrockKnowledgeBaseRetriever(
        "KB123",
        client=Bedrock(),
        manifest_s3_uri="s3://demo/rag/metadata/documents.json",
        s3_client=S3(),
    )
    chunks = repo.retrieve("annual maximum", PlanScope(plan_id="HC-PLUS"))
    assert [c.source.source_id for c in chunks] == ["plus", "global"]
    assert all(c.source.uri and c.source.page is None for c in chunks)


@pytest.mark.parametrize(
    "config",
    [
        {"plan_rules_source": "dynamodb"},
        {"provider_repository": "dynamodb"},
        {"history_repository": "dynamodb"},
        {"plan_rules_source": "guess"},
    ],
)
def test_new_aws_modes_fail_closed_without_resource_identifiers(config: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        Settings(internal_token="test-" * 10, **config)
