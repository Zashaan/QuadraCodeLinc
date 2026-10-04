"""Small fail-closed configuration boundary; never include secrets in repr or logs."""

import os
import re
from dataclasses import dataclass, field


def _require_token(value: str, name: str) -> None:
    if len(value) < 32 or not value.isascii() or any(character.isspace() for character in value):
        raise ValueError(f"{name} requires at least 32 non-whitespace ASCII characters")


@dataclass(frozen=True)
class Settings:
    internal_token: str = field(repr=False)
    member_token: str = field(repr=False, default="")
    member_repository: str = "synthetic"
    dynamodb_member_table: str = ""
    conversation_repository: str = "synthetic"
    dynamodb_conversation_table: str = ""
    conversation_db_path: str = ".local/abe-conversations.sqlite3"
    plan_rules_source: str = "local"
    provider_repository: str = "synthetic"
    history_repository: str = "synthetic"
    dynamodb_plan_table: str = ""
    dynamodb_coverage_rule_table: str = ""
    dynamodb_procedure_table: str = ""
    dynamodb_provider_table: str = ""
    dynamodb_claim_table: str = ""
    dynamodb_authorization_table: str = ""
    rag_document_manifest_s3_uri: str = ""
    rag_provider: str = "local"
    bedrock_knowledge_base_id: str = ""
    aws_region: str = "us-east-1"
    nova_text_model_id: str = "us.amazon.nova-2-lite-v1:0"
    demo_call_number: str = ""

    def __post_init__(self) -> None:
        _require_token(self.internal_token, "ABE_INTERNAL_TOKEN")
        if self.member_token:
            _require_token(self.member_token, "ABE_MEMBER_TOKEN")
            if self.member_token == self.internal_token:
                raise ValueError("ABE_MEMBER_TOKEN must differ from ABE_INTERNAL_TOKEN")
        if self.demo_call_number and not re.fullmatch(r"\+[1-9][0-9]{7,14}", self.demo_call_number):
            raise ValueError("ABE_DEMO_CALL_NUMBER must use E.164 format")
        if self.member_repository not in {"synthetic", "dynamodb"}:
            raise ValueError("Invalid MEMBER_REPOSITORY")
        if self.conversation_repository not in {"synthetic", "sqlite", "dynamodb"}:
            raise ValueError("Invalid CONVERSATION_REPOSITORY")
        if self.rag_provider not in {"local", "bedrock", "disabled"}:
            raise ValueError("Invalid RAG_PROVIDER")
        for mode, allowed, label in (
            (self.plan_rules_source, {"local", "dynamodb"}, "PLAN_RULES_SOURCE"),
            (self.provider_repository, {"synthetic", "dynamodb"}, "PROVIDER_REPOSITORY"),
            (self.history_repository, {"synthetic", "dynamodb"}, "HISTORY_REPOSITORY"),
        ):
            if mode not in allowed:
                raise ValueError(f"Invalid {label}")
        for enabled, fields in (
            (
                self.plan_rules_source == "dynamodb",
                ("dynamodb_plan_table", "dynamodb_coverage_rule_table", "dynamodb_procedure_table"),
            ),
            (self.provider_repository == "dynamodb", ("dynamodb_provider_table",)),
            (
                self.history_repository == "dynamodb",
                ("dynamodb_claim_table", "dynamodb_authorization_table"),
            ),
        ):
            for field_name in fields:
                if enabled and not getattr(self, field_name):
                    raise ValueError(f"{field_name.upper()} is required")
        if self.member_repository == "dynamodb" and not self.dynamodb_member_table:
            raise ValueError("DYNAMODB_MEMBER_TABLE is required")
        if self.conversation_repository == "dynamodb" and not self.dynamodb_conversation_table:
            raise ValueError("DYNAMODB_CONVERSATION_TABLE is required")
        if self.rag_provider == "bedrock" and not self.bedrock_knowledge_base_id:
            raise ValueError("BEDROCK_KNOWLEDGE_BASE_ID is required")

    @classmethod
    def from_environment(cls) -> "Settings":
        return cls(
            internal_token=os.environ.get("ABE_INTERNAL_TOKEN", ""),
            member_token=os.environ.get("ABE_MEMBER_TOKEN", ""),
            member_repository=os.environ.get("MEMBER_REPOSITORY", "synthetic"),
            dynamodb_member_table=os.environ.get("DYNAMODB_MEMBER_TABLE", ""),
            conversation_repository=os.environ.get("CONVERSATION_REPOSITORY", "sqlite"),
            dynamodb_conversation_table=os.environ.get("DYNAMODB_CONVERSATION_TABLE", ""),
            conversation_db_path=os.environ.get(
                "ABE_CONVERSATION_DB", ".local/abe-conversations.sqlite3"
            ),
            plan_rules_source=os.environ.get("PLAN_RULES_SOURCE", "local"),
            provider_repository=os.environ.get("PROVIDER_REPOSITORY", "synthetic"),
            history_repository=os.environ.get("HISTORY_REPOSITORY", "synthetic"),
            dynamodb_plan_table=os.environ.get("DYNAMODB_PLAN_TABLE", ""),
            dynamodb_coverage_rule_table=os.environ.get("DYNAMODB_COVERAGE_RULE_TABLE", ""),
            dynamodb_procedure_table=os.environ.get("DYNAMODB_PROCEDURE_TABLE", ""),
            dynamodb_provider_table=os.environ.get("DYNAMODB_PROVIDER_TABLE", ""),
            dynamodb_claim_table=os.environ.get("DYNAMODB_CLAIM_TABLE", ""),
            dynamodb_authorization_table=os.environ.get("DYNAMODB_AUTHORIZATION_TABLE", ""),
            rag_document_manifest_s3_uri=os.environ.get("RAG_DOCUMENT_MANIFEST_S3_URI", ""),
            rag_provider=os.environ.get("RAG_PROVIDER", "local"),
            bedrock_knowledge_base_id=os.environ.get("BEDROCK_KNOWLEDGE_BASE_ID", ""),
            aws_region=os.environ.get("AWS_REGION", "us-east-1"),
            nova_text_model_id=os.environ.get("NOVA_TEXT_MODEL_ID", "us.amazon.nova-2-lite-v1:0"),
            demo_call_number=os.environ.get("ABE_DEMO_CALL_NUMBER", ""),
        )
