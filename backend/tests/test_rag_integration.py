import copy
import io
import json
from typing import Any

import boto3  # type: ignore[import-untyped]
import pytest
from botocore.exceptions import ClientError  # type: ignore[import-untyped]
from botocore.stub import Stubber  # type: ignore[import-untyped]
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.members.repository import DynamoDBMemberRepository
from app.retrieval.repository import (
    BedrockKnowledgeBaseRetriever,
    PlanScope,
    canonical_s3_uri,
)

BUCKET = "abe-rag-517355425116-us-east-1"
KB = "3IVSARFSID"
MANIFEST_URI = f"s3://{BUCKET}/rag/metadata/documents.json"
QUESTION = "What does my plan say about major dental work?"
MANIFEST = [
    {
        "file": "benefits_plus.pdf",
        "document_id": "PLUS",
        "title": "HarborCare Plus Summary of Benefits",
        "plan_id": "HC-PLUS",
        "document_type": "summary_of_benefits",
    },
    {
        "file": "benefits_basic.pdf",
        "document_id": "BASIC",
        "title": "HarborCare Essential Summary of Benefits",
        "plan_id": "HC-BASIC",
        "document_type": "summary_of_benefits",
    },
    {
        "file": "benefits_premier.pdf",
        "document_id": "PREMIER",
        "title": "HarborCare Premier Summary of Benefits",
        "plan_id": "HC-PREMIER",
        "document_type": "summary_of_benefits",
    },
    {
        "file": "network_guide.pdf",
        "document_id": "NETWORK",
        "title": "Network Guide",
        "plan_id": None,
        "document_type": "guide",
    },
]


def result(filename: str, text: str) -> dict[str, Any]:
    # Actual managed Retrieve shape: HTTPS location, S3 documentId, no plan sidecars.
    return {
        "documentId": f"s3://{BUCKET}/rag/source/{filename}",
        "location": {
            "type": "S3",
            "s3Location": {"uri": f"https://{BUCKET}.s3.amazonaws.com/rag/source/{filename}"},
        },
        "metadata": {"_document_title": filename, "_excerpt_page_number": 1.0},
        "content": {"text": text},
    }


RESPONSE = {
    "retrievalResults": [
        result("benefits_basic.pdf", "HC-BASIC major services rules."),
        result(
            "benefits_plus.pdf",
            "HC-PLUS: major services coverage is 50%; six month waiting period.",
        ),
        result(
            "network_guide.pdf", "Out-of-network providers may charge above the allowed amount."
        ),
        result("benefits_premier.pdf", "HC-PREMIER major services rules."),
    ]
}


class S3:
    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        assert kwargs == {"Bucket": BUCKET, "Key": "rag/metadata/documents.json"}
        return {"Body": io.BytesIO(json.dumps(MANIFEST).encode())}


class Bedrock:
    def __init__(self, response: dict[str, Any] | None = None, error: str | None = None) -> None:
        self.response = response if response is not None else copy.deepcopy(RESPONSE)
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def retrieve(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.error:
            raise ClientError(
                {"Error": {"Code": self.error, "Message": "sensitive error text"}}, "Retrieve"
            )
        assert kwargs["knowledgeBaseId"] == KB
        assert "HC-PLUS HarborCare Plus Summary of Benefits:" in kwargs["retrievalQuery"]["text"]
        configuration = next(iter(kwargs["retrievalConfiguration"].values()))
        assert "filter" not in configuration
        return self.response


def repository(client: Any) -> BedrockKnowledgeBaseRetriever:
    return BedrockKnowledgeBaseRetriever(
        KB, client=client, manifest_s3_uri=MANIFEST_URI, s3_client=S3()
    )


def test_managed_sdk_contract_retries_only_known_incompatibility_and_caches_mode() -> None:
    client = boto3.client(
        "bedrock-agent-runtime",
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    query = "HC-PLUS HarborCare Plus Summary of Benefits: " + QUESTION
    base = {"knowledgeBaseId": KB, "retrievalQuery": {"text": query}}
    vector = {
        **base,
        "retrievalConfiguration": {"vectorSearchConfiguration": {"numberOfResults": 8}},
    }
    managed = {
        **base,
        "retrievalConfiguration": {"managedSearchConfiguration": {"numberOfResults": 8}},
    }
    with Stubber(client) as stub:
        stub.add_client_error(
            "retrieve",
            service_error_code="ValidationException",
            service_message=(
                "Incompatible configuration: vectorSearchConfiguration is not supported "
                "for managed knowledge bases. Use managedSearchConfiguration instead."
            ),
            expected_params=vector,
        )
        stub.add_response("retrieve", RESPONSE, managed)
        stub.add_response("retrieve", RESPONSE, managed)
        repo = repository(client)
        for _ in range(2):
            chunks = repo.retrieve(QUESTION, PlanScope(plan_id="HC-PLUS"))
            assert [c.source.plan_id for c in chunks] == ["HC-PLUS", "GLOBAL"]
            assert chunks[0].source.page == 1
            assert chunks[0].source.uri == f"s3://{BUCKET}/rag/source/benefits_plus.pdf"
        stub.assert_no_pending_responses()


def test_missing_metadata_sidecars_and_document_id_only_still_return_evidence() -> None:
    response = copy.deepcopy(RESPONSE)
    for item in response["retrievalResults"]:
        item.pop("location")
        item["metadata"] = {}
    chunks = repository(Bedrock(response)).retrieve(QUESTION, PlanScope(plan_id="HC-PLUS"))
    assert [c.source.source_id for c in chunks] == ["PLUS", "NETWORK"]
    assert all(c.text and c.source.page is None for c in chunks)


@pytest.mark.parametrize(
    "uri",
    [
        f"s3://{BUCKET}/rag/source/benefits_plus.pdf",
        f"https://{BUCKET}.s3.amazonaws.com/rag/source/benefits_plus.pdf",
        f"https://{BUCKET}.s3.us-east-1.amazonaws.com/rag/source/benefits_plus.pdf",
        f"https://s3.us-east-1.amazonaws.com/{BUCKET}/rag/source/benefits_plus.pdf",
    ],
)
def test_source_normalization_preserves_bucket_and_key(uri: str) -> None:
    assert canonical_s3_uri(uri) == f"s3://{BUCKET}/rag/source/benefits_plus.pdf"


def test_spoofed_or_conflicting_sources_and_mixed_plan_content_are_rejected() -> None:
    conflict = result("benefits_plus.pdf", "HC-PLUS coverage")
    conflict["documentId"] = "s3://wrong-bucket/rag/source/benefits_plus.pdf"
    unknown = result("unlisted.pdf", "HC-PLUS coverage")
    global_mixed = result("network_guide.pdf", "HC-BASIC has different percentages.")
    https_spoof = result("benefits_plus.pdf", "HC-PLUS coverage")
    https_spoof.pop("documentId")
    https_spoof["location"]["s3Location"]["uri"] = "https://evil.example/benefits_plus.pdf"
    chunks = repository(
        Bedrock({"retrievalResults": [conflict, unknown, global_mixed, https_spoof]})
    ).retrieve(QUESTION, PlanScope(plan_id="HC-PLUS"))
    assert chunks == []


class MemberTable:
    def get_item(self, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["Key"] == {"member_id": "DEMO001"}
        return {
            "Item": {
                "member_id": "DEMO001",
                "first_name": "Jordan",
                "last_name": "Lee",
                "plan_id": "HC-PLUS",
                "is_synthetic": True,
                "member_status": "active",
                "annual_maximum": 1500,
                "annual_maximum_used": 700,
                "annual_maximum_remaining": 800,
                "deductible": 50,
                "deductible_met": 50,
                "deductible_remaining": 0,
            }
        }


@pytest.mark.parametrize("error", [None, "AccessDeniedException", "ValidationException"])
def test_full_member_tool_path_retains_plan_and_fails_safely(error: str | None) -> None:
    bedrock = Bedrock(error=error)
    token = "rag-test-token-" * 4
    app = create_app(
        Settings(token),
        repository=DynamoDBMemberRepository("abe-members", table=MemberTable()),
        retriever=repository(bedrock),
    )
    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(app) as client:
        session = client.post(
            "/sessions", headers=headers, json={"call_id": "CA" + "a" * 32}
        ).json()
        assert "If no evidence is found or retrieval" in session["system_prompt"]
        assert "Do not fill gaps" in session["system_prompt"]
        sid = session["session_id"]

        def invoke(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            response = client.post(
                f"/sessions/{sid}/tools",
                headers=headers,
                json={"tool_name": name, "tool_call_id": name, "arguments": arguments},
            )
            assert response.status_code == 200
            return dict(response.json()["result"])

        member = invoke("get_member", {"member_id": "DEMO001"})
        assert member["member"]["plan_id"] == "HC-PLUS"
        for question in (QUESTION, "What is the waiting period?"):
            evidence = invoke("retrieve_plan_context", {"query": question})
            if error:
                assert evidence["status"] == "unavailable"
                assert "chunks" not in evidence and "sensitive" not in json.dumps(evidence)
            else:
                assert evidence["status"] == "verified" and len(evidence["chunks"]) == 2
                assert evidence["chunks"][0]["source"]["uri"].endswith("benefits_plus.pdf")
                assert evidence["chunks"][0]["source"]["plan_id"] == "HC-PLUS"
        # Unrelated AWS failures must not trigger the managed-config retry.
        assert len(bedrock.calls) == 2
        assert client.delete(f"/sessions/{sid}", headers=headers).status_code == 204
