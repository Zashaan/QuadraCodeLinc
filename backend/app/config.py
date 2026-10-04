"""Small fail-closed configuration boundary; never include secrets in repr or logs."""

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    internal_token: str = field(repr=False)

    member_repository: str = "synthetic"
    dynamodb_member_table: str = ""
    rag_provider: str = "local"
    bedrock_knowledge_base_id: str = ""
    aws_region: str = "us-east-1"

    def __post_init__(self) -> None:
        if self.member_repository not in {"synthetic", "dynamodb"}:
            raise ValueError("Invalid MEMBER_REPOSITORY")
        if self.rag_provider not in {"local", "bedrock"}:
            raise ValueError("Invalid RAG_PROVIDER")
        if self.member_repository == "dynamodb" and not self.dynamodb_member_table:
            raise ValueError("DYNAMODB_MEMBER_TABLE is required")
        if self.rag_provider == "bedrock" and not self.bedrock_knowledge_base_id:
            raise ValueError("BEDROCK_KNOWLEDGE_BASE_ID is required")
        if (
            len(self.internal_token) < 32
            or not self.internal_token.isascii()
            or any(character.isspace() for character in self.internal_token)
        ):
            raise ValueError(
                "ABE_INTERNAL_TOKEN requires at least 32 non-whitespace ASCII characters"
            )

    @classmethod
    def from_environment(cls) -> "Settings":
        return cls(
            internal_token=os.environ.get("ABE_INTERNAL_TOKEN", ""),
            member_repository=os.environ.get("MEMBER_REPOSITORY", "synthetic"),
            dynamodb_member_table=os.environ.get("DYNAMODB_MEMBER_TABLE", ""),
            rag_provider=os.environ.get("RAG_PROVIDER", "local"),
            bedrock_knowledge_base_id=os.environ.get("BEDROCK_KNOWLEDGE_BASE_ID", ""),
            aws_region=os.environ.get("AWS_REGION", "us-east-1"),
        )
