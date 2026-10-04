import re
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol, cast

from pydantic import Field, TypeAdapter

from app.models import StrictModel


class SourceMetadata(StrictModel):
    source_document: Annotated[str, Field(min_length=1, max_length=300)]
    source_id: Annotated[str, Field(min_length=1, max_length=500)]
    section: Annotated[str, Field(min_length=1, max_length=300)] | None = None
    page: Annotated[int, Field(ge=1)] | None = None
    plan_id: Annotated[str, Field(min_length=1, max_length=100)]
    employer: Annotated[str, Field(min_length=1, max_length=100)]
    plan_year: Annotated[int, Field(ge=2000, le=2200)]
    state: Annotated[str, Field(pattern=r"^[A-Z]{2}$")]
    doc_type: Annotated[str, Field(min_length=1, max_length=100)]


class PlanDocumentChunk(StrictModel):
    text: Annotated[str, Field(min_length=1, max_length=5000)]
    metadata: SourceMetadata
    score: Annotated[float, Field(ge=0)] | None = None


class RetrievalFilters(StrictModel):
    plan_id: str | None = None
    employer: str | None = None
    plan_year: int | None = None
    state: str | None = None
    doc_type: str | None = None


class RetrievalResult(StrictModel):
    status: Literal["verified", "unverified"]
    chunks: list[PlanDocumentChunk]
    message: str | None = None


class PlanDocumentRetriever(Protocol):
    def retrieve(
        self, query: str, filters: RetrievalFilters, limit: int = 3
    ) -> RetrievalResult: ...


class _ChunkFixture(StrictModel):
    notice: Literal["SYNTHETIC DEMO PLAN CONTENT — NOT AN ACTUAL LINCOLN PLAN"]
    chunks: list[PlanDocumentChunk]


class LocalPlanDocumentRetriever:
    """Small deterministic term-overlap retriever for approved local demo content."""

    def __init__(self, data_path: Path | None = None) -> None:
        source = data_path or Path(__file__).resolve().parents[3] / "data/demo/rag/plan_chunks.json"
        fixture = TypeAdapter(_ChunkFixture).validate_json(source.read_text(encoding="utf-8"))
        self._chunks = fixture.chunks

    @staticmethod
    def _terms(value: str) -> set[str]:
        return {term for term in re.findall(r"[a-z0-9]+", value.lower()) if len(term) > 2}

    @staticmethod
    def _matches(metadata: SourceMetadata, filters: RetrievalFilters) -> bool:
        return all(
            requested is None or getattr(metadata, field) == requested
            for field, requested in filters.model_dump().items()
        )

    def retrieve(self, query: str, filters: RetrievalFilters, limit: int = 3) -> RetrievalResult:
        if not query.strip() or not 1 <= limit <= 5:
            return RetrievalResult(
                status="unverified", chunks=[], message="No supported plan context was found."
            )
        terms = self._terms(query)
        ranked: list[tuple[int, int, PlanDocumentChunk]] = []
        for index, chunk in enumerate(self._chunks):
            if not self._matches(chunk.metadata, filters):
                continue
            searchable = self._terms(
                f"{chunk.text} {chunk.metadata.section or ''} {chunk.metadata.doc_type}"
            )
            score = len(terms & searchable)
            if score:
                ranked.append((score, -index, chunk.model_copy(update={"score": float(score)})))
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        chunks = [item[2] for item in ranked[:limit]]
        if not chunks:
            return RetrievalResult(
                status="unverified",
                chunks=[],
                message="The requested plan detail could not be verified from available sources.",
            )
        return RetrievalResult(status="verified", chunks=chunks)


class BedrockKnowledgeBaseClient(Protocol):
    def retrieve(self, **kwargs: Any) -> Mapping[str, Any]: ...


class BedrockKnowledgeBaseRetriever:
    def __init__(
        self,
        knowledge_base_id: str,
        *,
        region: str,
        client: BedrockKnowledgeBaseClient | None = None,
    ) -> None:
        if not knowledge_base_id.strip():
            raise ValueError("BEDROCK_KNOWLEDGE_BASE_ID is required for bedrock retrieval")
        self._knowledge_base_id = knowledge_base_id
        self._client = client or self._default_client(region)

    @staticmethod
    def _default_client(region: str) -> BedrockKnowledgeBaseClient:
        try:
            import boto3  # type: ignore[import-untyped]
        except ImportError as error:
            raise RuntimeError("Install boto3 to use RAG_PROVIDER=bedrock") from error
        return cast(
            BedrockKnowledgeBaseClient,
            boto3.client("bedrock-agent-runtime", region_name=region),
        )

    @staticmethod
    def _filter(filters: RetrievalFilters) -> Mapping[str, Any] | None:
        clauses = [
            {"equals": {"key": key, "value": value}}
            for key, value in filters.model_dump().items()
            if value is not None
        ]
        if not clauses:
            return None
        return clauses[0] if len(clauses) == 1 else {"andAll": clauses}

    def retrieve(self, query: str, filters: RetrievalFilters, limit: int = 3) -> RetrievalResult:
        vector: dict[str, Any] = {"numberOfResults": max(1, min(limit, 5))}
        metadata_filter = self._filter(filters)
        if metadata_filter:
            vector["filter"] = metadata_filter
        response = self._client.retrieve(
            knowledgeBaseId=self._knowledge_base_id,
            retrievalQuery={"text": query},
            retrievalConfiguration={"vectorSearchConfiguration": vector},
        )
        chunks: list[PlanDocumentChunk] = []
        results = response.get("retrievalResults", [])
        if not isinstance(results, list):
            raise ValueError("Invalid Bedrock retrieval response")
        for result in results[:limit]:
            if not isinstance(result, Mapping):
                continue
            content = result.get("content")
            metadata = result.get("metadata")
            location = result.get("location")
            if not isinstance(content, Mapping) or not isinstance(metadata, Mapping):
                continue
            text = content.get("text")
            if not isinstance(text, str):
                continue
            location_source_id = self._source_id(location)
            metadata_source_id = metadata.get("source_id")
            source_id = (
                metadata_source_id
                if isinstance(metadata_source_id, str) and metadata_source_id
                else location_source_id
            )
            raw = {
                "source_document": metadata.get("source_document") or source_id,
                "source_id": source_id,
                "section": metadata.get("section"),
                "page": metadata.get("page"),
                "plan_id": metadata.get("plan_id"),
                "employer": metadata.get("employer"),
                "plan_year": metadata.get("plan_year"),
                "state": metadata.get("state"),
                "doc_type": metadata.get("doc_type"),
            }
            try:
                source = SourceMetadata.model_validate(raw)
                score = result.get("score")
                chunks.append(
                    PlanDocumentChunk(
                        text=text,
                        metadata=source,
                        score=float(score) if isinstance(score, int | float) else None,
                    )
                )
            except (TypeError, ValueError):
                continue
        if not chunks:
            return RetrievalResult(
                status="unverified",
                chunks=[],
                message="The requested plan detail could not be verified from the knowledge base.",
            )
        return RetrievalResult(status="verified", chunks=chunks)

    @staticmethod
    def _source_id(location: object) -> str | None:
        if isinstance(location, Mapping):
            for value in location.values():
                if isinstance(value, Mapping):
                    for key in ("uri", "url", "webLocation"):
                        candidate = value.get(key)
                        if isinstance(candidate, str) and candidate:
                            return candidate
        return None
