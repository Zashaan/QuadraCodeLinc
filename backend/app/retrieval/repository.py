"""Retrieval only: scoped evidence, never generated answers or arithmetic rules."""

import json
import re
from math import isfinite
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

from pydantic import Field, TypeAdapter

from app.benefits.models import Source
from app.benefits.repository import DEMO_PATH
from app.models import StrictModel


class PlanScope(StrictModel):
    plan_id: str
    employer: str | None = None
    plan_year: int | None = None
    state: str | None = None
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
        self,
        knowledge_base_id: str,
        region: str = "us-east-1",
        *,
        client: Any = None,
        manifest_s3_uri: str | None = None,
        s3_client: Any = None,
    ) -> None:
        if not knowledge_base_id:
            raise ValueError("BEDROCK_KNOWLEDGE_BASE_ID is required")
        if client is None:
            from app.aws import aws_client

            client = aws_client("bedrock-agent-runtime", region)
        self._client = client
        self._id = knowledge_base_id
        self._manifest_uri = manifest_s3_uri
        self._s3 = s3_client
        self._region = region
        self._manifest: dict[str, dict[str, Any]] | None = None

    def _load_manifest(self) -> dict[str, dict[str, Any]]:
        if self._manifest is not None:
            return self._manifest
        location = urlparse(self._manifest_uri or "")
        if location.scheme != "s3" or not location.netloc or not location.path:
            raise ValueError("Invalid RAG document manifest S3 URI")
        if self._s3 is None:
            from app.aws import aws_client

            self._s3 = aws_client("s3", self._region)
        body = self._s3.get_object(Bucket=location.netloc, Key=location.path.lstrip("/"))["Body"]
        try:
            raw = body.read(256 * 1024 + 1)
        finally:
            body.close()
        if len(raw) > 256 * 1024:
            raise ValueError("RAG manifest too large")
        entries = json.loads(raw)
        if not isinstance(entries, list) or not 1 <= len(entries) <= 100:
            raise ValueError("Invalid RAG document manifest")
        result = {}
        prefix = location.path.lstrip("/").rsplit("/metadata/", 1)[0]
        for entry in entries:
            filename = entry["file"]
            if not isinstance(filename, str) or "/" in filename or ".." in filename:
                raise ValueError("Invalid manifest filename")
            uri = f"s3://{location.netloc}/{prefix}/source/{filename}"
            if uri in result:
                raise ValueError("Duplicate RAG source")
            if not isinstance(entry["title"], str) or not isinstance(entry["document_id"], str):
                raise ValueError("Invalid manifest provenance")
            result[uri] = entry
        self._manifest = result
        return result

    def _retrieve_manifest(self, query: str, scope: PlanScope) -> list[PlanChunk]:
        manifest = self._load_manifest()
        titles = [e["title"] for e in manifest.values() if e.get("plan_id") == scope.plan_id]
        response = self._client.retrieve(
            knowledgeBaseId=self._id,
            retrievalQuery={"text": f"{scope.plan_id} {'; '.join(titles)[:300]}: {query}"},
            retrievalConfiguration={"vectorSearchConfiguration": {"numberOfResults": 8}},
        )
        if response.get("guardrailAction") == "INTERVENED":
            return []
        other_ids = {
            e["plan_id"] for e in manifest.values() if e.get("plan_id") not in (None, scope.plan_id)
        }
        ranked = []
        for result in response.get("retrievalResults", [])[:8]:
            uri = result.get("location", {}).get("s3Location", {}).get("uri")
            entry = manifest.get(uri)
            text = result.get("content", {}).get("text")
            if not entry or not isinstance(text, str) or not text or len(text) > 6000:
                continue
            if entry.get("plan_id") not in (None, scope.plan_id):
                continue
            if scope.doc_type and entry.get("document_type") != scope.doc_type:
                continue
            # A global document can contain multiple plans. Reject mixed-plan excerpts.
            if any(
                re.search(r"(?<![A-Z0-9])" + re.escape(pid) + r"(?![A-Z0-9])", text, re.I)
                for pid in other_ids
            ):
                continue
            other_names = {
                e["title"].split(" Summary of Benefits", 1)[0]
                for e in manifest.values()
                if e.get("plan_id") in other_ids
            }
            if any(name.casefold() in text.casefold() for name in other_names):
                continue
            # Metadata, when present, must agree with the trusted source mapping.
            indicated = result.get("metadata", {}).get("plan_id")
            if indicated is not None and indicated not in (scope.plan_id, "GLOBAL", "global"):
                continue
            source = Source(
                source_id=entry["document_id"],
                document=entry["title"],
                plan_id=entry.get("plan_id") or "GLOBAL",
                doc_type=entry["document_type"],
                synthetic=True,
                uri=uri,
            )
            ranked.append((entry.get("plan_id") is None, PlanChunk(text=text, source=source)))
        ranked.sort(key=lambda pair: pair[0])
        return [chunk for _, chunk in ranked[:4]]

    def retrieve(self, query: str, scope: PlanScope) -> list[PlanChunk]:
        if self._manifest_uri:
            return self._retrieve_manifest(query, scope)
        filters = [
            {"equals": {"key": k, "value": v}}
            for k, v in scope.model_dump(exclude_none=True).items()
        ]
        response = self._client.retrieve(
            knowledgeBaseId=self._id,
            retrievalQuery={"text": query},
            retrievalConfiguration={
                "vectorSearchConfiguration": {
                    "numberOfResults": 4,
                    "filter": {"andAll": filters} if len(filters) > 1 else filters[0],
                }
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
