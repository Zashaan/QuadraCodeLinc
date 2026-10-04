"""Small fail-closed configuration boundary; never include secrets in repr or logs."""

import os
from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class Settings:
    internal_token: str = field(repr=False)
    member_repository: Literal["synthetic", "dynamodb"] = "synthetic"
    dynamodb_member_table: str = ""
    plan_rules_source: Literal["local"] = "local"
    rag_provider: Literal["local", "bedrock"] = "local"
    bedrock_knowledge_base_id: str = ""
    provider_repository: Literal["synthetic"] = "synthetic"
    aws_region: str = "us-east-1"

    def __post_init__(self) -> None:
        if (
            len(self.internal_token) < 32
            or not self.internal_token.isascii()
            or any(character.isspace() for character in self.internal_token)
        ):
            raise ValueError(
                "ABE_INTERNAL_TOKEN requires at least 32 non-whitespace ASCII characters"
            )
        if self.member_repository == "dynamodb" and not self.dynamodb_member_table:
            raise ValueError("DYNAMODB_MEMBER_TABLE is required for MEMBER_REPOSITORY=dynamodb")
        if self.rag_provider == "bedrock" and not self.bedrock_knowledge_base_id:
            raise ValueError("BEDROCK_KNOWLEDGE_BASE_ID is required for RAG_PROVIDER=bedrock")

    @classmethod
    def from_environment(cls) -> "Settings":
        member_repository = os.environ.get("MEMBER_REPOSITORY", "synthetic")
        rag_provider = os.environ.get("RAG_PROVIDER", "local")
        plan_source = os.environ.get("PLAN_RULES_SOURCE", "local")
        provider_repository = os.environ.get("PROVIDER_REPOSITORY", "synthetic")
        if member_repository not in {"synthetic", "dynamodb"}:
            raise ValueError("MEMBER_REPOSITORY must be synthetic or dynamodb")
        if rag_provider not in {"local", "bedrock"}:
            raise ValueError("RAG_PROVIDER must be local or bedrock")
        if plan_source != "local":
            raise ValueError("PLAN_RULES_SOURCE must be local")
        if provider_repository != "synthetic":
            raise ValueError("PROVIDER_REPOSITORY must be synthetic")
        return cls(
            internal_token=os.environ.get("ABE_INTERNAL_TOKEN", ""),
            member_repository=member_repository,  # type: ignore[arg-type]
            dynamodb_member_table=os.environ.get("DYNAMODB_MEMBER_TABLE", ""),
            plan_rules_source=plan_source,  # type: ignore[arg-type]
            rag_provider=rag_provider,  # type: ignore[arg-type]
            bedrock_knowledge_base_id=os.environ.get("BEDROCK_KNOWLEDGE_BASE_ID", ""),
            provider_repository=provider_repository,  # type: ignore[arg-type]
            aws_region=os.environ.get("AWS_REGION", "us-east-1"),
        )
