"""Retrieval only: scoped evidence, never generated answers or arithmetic rules."""

import re
from math import isfinite
from pathlib import Path
from typing import Any, Protocol

from pydantic import Field, TypeAdapter

from app.benefits.models import Source
from app.benefits.repository import DEMO_PATH
from app.models import StrictModel


class PlanScope(StrictModel):
    plan_id: str
    employer: str
    plan_year: int
    state: str
    doc_type: str | None = None


class PlanChunk(StrictModel):
    text: str = Field(min_length=1, max_length=6000)
    source: Source


class PlanDocumentRetriever(Protocol):
    def retrieve(self, query: str, scope: PlanScope) -> list[PlanChunk]: ...


def matches(source: Source, scope: PlanScope) -> bool:
    return all(
        getattr(source, key) == value for key, value in scope.model_dump(exclude_none=True).items()
    )


class LocalPlanDocumentRetriever:
    def __init__(self, path: Path | None = None) -> None:
        self._chunks = TypeAdapter(list[PlanChunk]).validate_json(
            (path or DEMO_PATH / "plan-content.json").read_text()
        )

    def retrieve(self, query: str, scope: PlanScope) -> list[PlanChunk]:
        # Small deterministic lexical retrieval; no sentence/intent scripts.
        stop = {
            "the",
            "a",
            "an",
            "is",
            "are",
            "my",
            "does",
            "do",
            "what",
            "how",
            "of",
            "in",
            "for",
            "about",
            "plan",
            "say",
            "to",
            "there",
        }
        terms = {w.rstrip("s") for w in re.findall(r"[a-z0-9]+", query.lower()) if w not in stop}
        ranked = []
        for index, chunk in enumerate(self._chunks):
            if not matches(chunk.source, scope):
                continue
            words = {w.rstrip("s") for w in re.findall(r"[a-z0-9]+", chunk.text.lower())}
            score = len(terms & words)
            if score:
                ranked.append((-score, index, chunk))
        return [chunk for _, _, chunk in sorted(ranked)[:4]]


class BedrockKnowledgeBaseRetriever:
    def __init__(
        self, knowledge_base_id: str, region: str = "us-east-1", *, client: Any = None
    ) -> None:
        if not knowledge_base_id:
            raise ValueError("BEDROCK_KNOWLEDGE_BASE_ID is required")
        if client is None:
            from app.aws import aws_client

            client = aws_client("bedrock-agent-runtime", region)
        self._client = client
        self._id = knowledge_base_id

    def retrieve(self, query: str, scope: PlanScope) -> list[PlanChunk]:
        filters = [
            {"equals": {"key": k, "value": v}}
            for k, v in scope.model_dump(exclude_none=True).items()
        ]
        response = self._client.retrieve(
            knowledgeBaseId=self._id,
            retrievalQuery={"text": query},
            retrievalConfiguration={
                "vectorSearchConfiguration": {"numberOfResults": 4, "filter": {"andAll": filters}}
            },
        )
        if response.get("guardrailAction") == "INTERVENED":
            return []
        chunks = []
        for result in response.get("retrievalResults", [])[:4]:
            metadata = result.get("metadata", {})
            # Require genuine configured provenance; never infer names/pages from a query.
            fields = {k: v for k, v in metadata.items() if k in Source.model_fields}
            # Bedrock's metadata number type is a double, even for integral years/pages.
            for key in ("plan_year", "page"):
                value = fields.get(key)
                if isinstance(value, float) and isfinite(value) and value.is_integer():
                    fields[key] = int(value)
            source = Source.model_validate(fields)
            if not matches(source, scope):
                continue
            text = result.get("content", {}).get("text")
            if text:
                chunks.append(PlanChunk(text=text, source=source))
        return chunks
