from typing import Any

from app.retrieval.repository import (
    BedrockKnowledgeBaseRetriever,
    LocalPlanDocumentRetriever,
    RetrievalFilters,
)


def test_local_retrieval_preserves_sources_and_filters_wrong_plan() -> None:
    retriever = LocalPlanDocumentRetriever()
    result = retriever.retrieve(
        "What does the plan say about crowns?",
        RetrievalFilters(plan_id="DEMO_DENTAL_PPO", plan_year=2026, state="PA"),
    )
    assert result.status == "verified"
    assert result.chunks[0].metadata.source_document == "Synthetic Demo Dental Plan Summary"
    assert result.chunks[0].metadata.section == "Major services — crowns"
    assert result.chunks[0].metadata.page is None
    assert all(chunk.metadata.plan_id == "DEMO_DENTAL_PPO" for chunk in result.chunks)
    missing = retriever.retrieve("crowns", RetrievalFilters(plan_id="WRONG_PLAN", plan_year=2026))
    assert missing.status == "unverified" and missing.chunks == []


def test_bedrock_adapter_builds_metadata_filter_and_parses_mocked_sources() -> None:
    calls: list[dict[str, Any]] = []

    class Client:
        def retrieve(self, **kwargs: Any) -> dict[str, Any]:
            calls.append(kwargs)
            return {
                "retrievalResults": [
                    {
                        "content": {"text": "Supported crown language."},
                        "score": 0.9,
                        "location": {"s3Location": {"uri": "s3://demo/plan.pdf"}},
                        "metadata": {
                            "source_document": "Approved Demo Plan",
                            "source_id": "plan-1",
                            "section": "Crowns",
                            "page": 7,
                            "plan_id": "DEMO_DENTAL_PPO",
                            "employer": "SYNTHETIC_CODELINC",
                            "plan_year": 2026,
                            "state": "PA",
                            "doc_type": "summary_of_benefits",
                        },
                    }
                ]
            }

    retriever = BedrockKnowledgeBaseRetriever("kb-demo", region="us-east-1", client=Client())
    result = retriever.retrieve(
        "crowns",
        RetrievalFilters(plan_id="DEMO_DENTAL_PPO", state="PA"),
        limit=2,
    )
    assert result.status == "verified"
    assert result.chunks[0].metadata.page == 7
    configuration = calls[0]["retrievalConfiguration"]["vectorSearchConfiguration"]
    assert configuration["numberOfResults"] == 2
    assert {item["equals"]["key"] for item in configuration["filter"]["andAll"]} == {
        "plan_id",
        "state",
    }


def test_bedrock_empty_result_is_controlled_unverified_response() -> None:
    class EmptyClient:
        def retrieve(self, **kwargs: Any) -> dict[str, Any]:
            return {"retrievalResults": []}

    result = BedrockKnowledgeBaseRetriever(
        "kb-demo", region="us-east-1", client=EmptyClient()
    ).retrieve("waiting period", RetrievalFilters(plan_id="DEMO_DENTAL_PPO"))
    assert result.status == "unverified"
    assert result.chunks == []
    assert result.message is not None


def test_bedrock_drops_results_without_real_source_metadata() -> None:
    class MissingSourceClient:
        def retrieve(self, **kwargs: Any) -> dict[str, Any]:
            return {
                "retrievalResults": [
                    {
                        "content": {"text": "Text without a verifiable source."},
                        "metadata": {
                            "plan_id": "DEMO_DENTAL_PPO",
                            "employer": "SYNTHETIC_CODELINC",
                            "plan_year": 2026,
                            "state": "PA",
                            "doc_type": "summary_of_benefits",
                        },
                    }
                ]
            }

    result = BedrockKnowledgeBaseRetriever(
        "kb-demo", region="us-east-1", client=MissingSourceClient()
    ).retrieve("plan", RetrievalFilters(plan_id="DEMO_DENTAL_PPO"))
    assert result.status == "unverified"
    assert result.chunks == []
