"""Retrieval only: scoped evidence, never generated answers or arithmetic rules."""

import json
import logging
import re
from math import isfinite
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import unquote, urlparse

from botocore.exceptions import ClientError  # type: ignore[import-untyped]
from pydantic import Field, TypeAdapter

from app.benefits.models import Source
from app.benefits.repository import DEMO_PATH
from app.models import StrictModel


def rag_log(event: str, **fields: str | int) -> None:
    # Explicit diagnostics only: never query text, member details or exception messages.
    logging.getLogger("abe").info(json.dumps({"event": event, **fields}))


def canonical_s3_uri(value: object) -> str | None:
    """Normalize AWS S3 location variants, retaining the bucket/key trust boundary."""
    if not isinstance(value, str):
        return None
    parsed = urlparse(value)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return None
    if parsed.scheme == "s3" and parsed.netloc and parsed.path:
        return f"s3://{parsed.netloc}/{unquote(parsed.path.lstrip('/'))}"
    if parsed.scheme != "https":
        return None
    host = parsed.hostname or ""
    virtual = re.fullmatch(r"(.+)\.s3(?:[.-][a-z0-9-]+)?\.amazonaws\.com", host)
    if virtual and parsed.path:
        return f"s3://{virtual[1]}/{unquote(parsed.path.lstrip('/'))}"
    if re.fullmatch(r"s3(?:[.-][a-z0-9-]+)?\.amazonaws\.com", host):
        bucket, _, key = parsed.path.lstrip("/").partition("/")
        if bucket and key:
            return f"s3://{bucket}/{unquote(key)}"
    return None


def result_source_uri(result: dict[str, Any]) -> str | None:
    candidates = {
        uri
        for value in (
            result.get("documentId"),
            result.get("location", {}).get("s3Location", {}).get("uri"),
            result.get("metadata", {}).get("_source_uri"),
        )
        if (uri := canonical_s3_uri(value)) is not None
    }
    # Conflicting provenance must not be rescued by a matching basename.
    return next(iter(candidates)) if len(candidates) == 1 else None


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
        self._search_type = "vectorSearchConfiguration"

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
        titles = [
            e["title"]
            for e in manifest.values()
            if e.get("plan_id") == scope.plan_id and e.get("document_type") == "summary_of_benefits"
        ]
        if not titles:
            titles = [e["title"] for e in manifest.values() if e.get("plan_id") == scope.plan_id]
        response = self._search(
            f"{scope.plan_id} {'; '.join(titles)[:300]}: {query}", {"numberOfResults": 8}
        )
        if response.get("guardrailAction") == "INTERVENED":
            return []
        other_ids = {
            e["plan_id"] for e in manifest.values() if e.get("plan_id") not in (None, scope.plan_id)
        }
        ranked = []
        for result in response.get("retrievalResults", [])[:8]:
            uri = result_source_uri(result)
            entry = manifest.get(uri) if uri else None
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
            raw_page = result.get("metadata", {}).get("_excerpt_page_number")
            page = (
                int(raw_page)
                if isinstance(raw_page, (int, float))
                and not isinstance(raw_page, bool)
                and isfinite(raw_page)
                and raw_page >= 1
                and float(raw_page).is_integer()
                else None
            )
            source = Source(
                page=page,
                source_id=entry["document_id"],
                document=entry["title"],
                plan_id=entry.get("plan_id") or "GLOBAL",
                doc_type=entry["document_type"],
                synthetic=True,
                uri=uri,
            )
            priority = (
                0
                if entry.get("plan_id") == scope.plan_id
                and entry.get("document_type") == "summary_of_benefits"
                else 1
                if entry.get("plan_id") == scope.plan_id
                else 2
            )
            ranked.append((priority, PlanChunk(text=text, source=source)))
        ranked.sort(key=lambda pair: pair[0])
        return [chunk for _, chunk in ranked[:4]]

    def _search(self, query: str, configuration: dict[str, Any]) -> Any:
        try:
            response = self._client.retrieve(
                knowledgeBaseId=self._id,
                retrievalQuery={"text": query},
                retrievalConfiguration={self._search_type: configuration},
            )
        except ClientError as error:
            detail = error.response.get("Error", {})
            if (
                self._search_type != "vectorSearchConfiguration"
                or detail.get("Code") != "ValidationException"
                or "vectorSearchConfiguration is not supported for managed knowledge bases"
                not in detail.get("Message", "")
            ):
                raise
            # The managed API explicitly identifies its supported configuration.
            # Cache this per adapter; do not retry auth, timeout, or other failures.
            self._search_type = "managedSearchConfiguration"
            response = self._client.retrieve(
                knowledgeBaseId=self._id,
                retrievalQuery={"text": query},
                retrievalConfiguration={self._search_type: configuration},
            )
        rag_log(
            "rag_results_count",
            count=len(response.get("retrievalResults", [])),
            search_type=self._search_type,
        )
        return response

    def retrieve(self, query: str, scope: PlanScope) -> list[PlanChunk]:
        rag_log("rag_query_started", provider="bedrock")
        try:
            chunks = self._retrieve(query, scope)
        except Exception as error:
            rag_log("rag_query_failed", error_type=type(error).__name__)
            raise
        rag_log("rag_evidence_count", count=len(chunks))
        for chunk in chunks:
            rag_log(
                "rag_result_source",
                document=(chunk.source.uri or chunk.source.document).rsplit("/", 1)[-1],
            )
        return chunks

    def _retrieve(self, query: str, scope: PlanScope) -> list[PlanChunk]:
        if self._manifest_uri:
            return self._retrieve_manifest(query, scope)
        filters = [
            {"equals": {"key": k, "value": v}}
            for k, v in scope.model_dump(exclude_none=True).items()
        ]
        response = self._search(
            f"{scope.plan_id}: {query}",
            {
                "numberOfResults": 4,
                "filter": {"andAll": filters} if len(filters) > 1 else filters[0],
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
