import json
from decimal import Decimal
from typing import Any

import boto3  # type: ignore[import-untyped]
import pytest
from botocore.exceptions import ClientError  # type: ignore[import-untyped]
from botocore.stub import Stubber  # type: ignore[import-untyped]
from pydantic import ValidationError

from app.benefits.repository import DEMO_PATH
from app.config import Settings
from app.members.repository import DemoMemberRepository, DynamoDBMemberRepository
from app.providers.repository import ProviderSearch, SyntheticProviderRepository
from app.retrieval.repository import (
    BedrockKnowledgeBaseRetriever,
    LocalPlanDocumentRetriever,
    PlanScope,
)

SCOPE = PlanScope(plan_id="DEMO_DENTAL_PPO", employer="DEMO_EMPLOYER", plan_year=2026, state="NC")


def test_dynamodb_sdk_contract_without_network_or_real_credentials() -> None:
    resource = boto3.resource(
        "dynamodb", region_name="us-east-1", aws_access_key_id="test", aws_secret_access_key="test"
    )
    table = resource.Table("demo-members")
    member = DemoMemberRepository().get_member("DEMO001")
    assert member is not None
    from boto3.dynamodb.types import TypeSerializer  # type: ignore[import-untyped]

    serializer = TypeSerializer()
    item = {k: serializer.serialize(v) for k, v in member.model_dump(mode="json").items()}
    params = {"TableName": "demo-members", "Key": {"member_id": "DEMO001"}, "ConsistentRead": True}
    with Stubber(resource.meta.client) as stub:
        stub.add_response("get_item", {"Item": item}, params)
        repository = DynamoDBMemberRepository("demo-members", table=table)
        assert repository.get_member("DEMO001") == member
        stub.add_response("get_item", {}, {**params, "Key": {"member_id": "UNKNOWN"}})
        assert repository.get_member("UNKNOWN") is None
        stub.add_client_error(
            "get_item", service_error_code="ResourceNotFoundException", expected_params=params
        )
        with pytest.raises(ClientError):
            repository.get_member("DEMO001")
        stub.assert_no_pending_responses()


def test_dynamodb_rejects_fractional_integer_facts_and_real_records() -> None:
    item = json.loads((DEMO_PATH / "members.json").read_text())[0]

    class Table:
        def get_item(self, **kwargs: Any) -> dict[str, Any]:
            return {"Item": item}

    repository = DynamoDBMemberRepository("demo", table=Table())
    item["annual_maximum_remaining"] = Decimal("800.25")
    with pytest.raises(ValidationError):
        repository.get_member("DEMO001")
    item["annual_maximum_remaining"] = Decimal(800)
    item["synthetic"] = False
    with pytest.raises(ValidationError):
        repository.get_member("DEMO001")


def test_local_rag_scoping_relevance_and_real_metadata() -> None:
    retriever = LocalPlanDocumentRetriever()
    result = retriever.retrieve("waiting period", SCOPE)
    assert result and "no waiting period" in result[0].text
    assert result[0].source.section == "Waiting and frequency"
    assert result[0].source.page is None
    for field, value in [
        ("plan_id", "OTHER"),
        ("employer", "OTHER"),
        ("plan_year", 2027),
        ("state", "CA"),
        ("doc_type", "OTHER"),
    ]:
        assert retriever.retrieve("crowns", SCOPE.model_copy(update={field: value})) == []
    assert retriever.retrieve("orthodontic braces", SCOPE) == []


def test_bedrock_sdk_filter_contract_and_wrong_plan_defense() -> None:
    client = boto3.client(
        "bedrock-agent-runtime",
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    chunk = LocalPlanDocumentRetriever().retrieve("crowns", SCOPE)[0]
    metadata = chunk.source.model_dump(exclude_none=True)
    metadata["plan_year"] = 2026.0  # AWS metadata numbers are doubles.
    metadata["page"] = 3.0  # Test fixture explicitly supplies a genuine page.
    expected = {
        "knowledgeBaseId": "DEMO123456",
        "retrievalQuery": {"text": "crowns"},
        "retrievalConfiguration": {
            "vectorSearchConfiguration": {
                "numberOfResults": 4,
                "filter": {
                    "andAll": [
                        {"equals": {"key": k, "value": v}}
                        for k, v in SCOPE.model_dump(exclude_none=True).items()
                    ]
                },
            }
        },
    }
    response = {
        "retrievalResults": [
            {"content": {"text": chunk.text}, "metadata": metadata},
            {"content": {"text": "Wrong plan"}, "metadata": {**metadata, "plan_id": "OTHER"}},
        ]
    }
    with Stubber(client) as stub:
        stub.add_response("retrieve", response, expected)
        retriever = BedrockKnowledgeBaseRetriever("DEMO123456", client=client)
        result = retriever.retrieve("crowns", SCOPE)
        assert len(result) == 1
        assert result[0].source.page == 3
        assert result[0].source.document == chunk.source.document
        stub.add_response("retrieve", {"retrievalResults": []}, expected)
        assert retriever.retrieve("crowns", SCOPE) == []
        stub.assert_no_pending_responses()


def test_provider_filters_and_unknown_distances() -> None:
    repo = SyntheticProviderRepository()
    plan = "DEMO_DENTAL_PPO"
    assert [
        p.provider_id for p in repo.search(plan, ProviderSearch(network_status="out_of_network"))
    ] == ["DEMO_P2"]
    assert [p.provider_id for p in repo.search(plan, ProviderSearch(zip_code="27401"))] == [
        "DEMO_P1"
    ]
    assert [
        p.provider_id for p in repo.search(plan, ProviderSearch(zip_code="27401", radius_miles=8))
    ] == ["DEMO_P1", "DEMO_P2"]
    assert repo.search(plan, ProviderSearch(zip_code="99999", radius_miles=100)) == []
    assert [
        p.provider_id
        for p in repo.search(plan, ProviderSearch(specialty="endodontics", procedure="ROOT_CANAL"))
    ] == ["DEMO_P3"]
    assert repo.search(plan, ProviderSearch(specialty="endodontics", procedure="CROWN")) == []
    assert repo.search("OTHER", ProviderSearch()) == []
    assert repo.get_provider("DEMO_P1", "OTHER") is None
    with pytest.raises(ValueError):
        repo.search(plan, ProviderSearch(radius_miles=5))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"member_repository": "bad"},
        {"member_repository": "dynamodb"},
        {"rag_provider": "bad"},
        {"rag_provider": "bedrock"},
    ],
)
def test_explicit_configuration_fails_without_required_settings(kwargs: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        Settings(internal_token="test-" * 10, **kwargs)
