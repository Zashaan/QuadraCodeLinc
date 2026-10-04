from typing import Any

import pytest

from app.members.repository import DynamoDBMemberRepository, SyntheticMemberRepository
from app.plans.repository import LocalPlanRulesRepository


def test_synthetic_member_repository_needs_no_aws_and_unknown_is_clean() -> None:
    repository = SyntheticMemberRepository()
    member = repository.get_member("demo001")
    assert member is not None
    assert member.annual_maximum_remaining == 800
    assert repository.get_member("NOTREAL") is None


def test_dynamodb_adapter_uses_explicit_table_and_can_be_mocked() -> None:
    calls: list[dict[str, Any]] = []

    class Client:
        def get_item(self, **kwargs: Any) -> dict[str, Any]:
            calls.append(kwargs)
            return {
                "Item": {
                    "member_id": {"S": "DEMO001"},
                    "name": {"S": "Synthetic Demo Member"},
                    "employer_id": {"S": "SYNTHETIC_CODELINC"},
                    "plan_id": {"S": "DEMO_DENTAL_PPO"},
                    "plan_year": {"N": "2026"},
                    "state": {"S": "PA"},
                    "zip_code": {"S": "19103"},
                    "currency": {"S": "USD"},
                    "annual_maximum": {"N": "2000"},
                    "annual_maximum_used": {"N": "1200"},
                    "annual_maximum_remaining": {"N": "800"},
                    "deductible_total": {"N": "50"},
                    "deductible_used": {"N": "50"},
                    "deductible_remaining": {"N": "0"},
                    "fsa_balance": {"N": "350"},
                }
            }

    repository = DynamoDBMemberRepository("demo-members", Client())
    member = repository.get_member("demo001")
    assert member is not None and member.annual_maximum_remaining == 800
    assert calls == [
        {
            "TableName": "demo-members",
            "Key": {"member_id": {"S": "DEMO001"}},
            "ConsistentRead": True,
        }
    ]
    with pytest.raises(ValueError, match="DYNAMODB_MEMBER_TABLE"):
        DynamoDBMemberRepository("", Client())


def test_structured_plan_loads_and_procedure_mapping_comes_from_data() -> None:
    plans = LocalPlanRulesRepository()
    plan = plans.get_plan("DEMO_DENTAL_PPO", 2026)
    assert plan is not None and plan.plan_name == "SYNTHETIC DEMO PLAN"
    assert plan.procedure("crown") is not None
    assert plan.procedure("crown").procedure_id == "D2740"  # type: ignore[union-attr]
    assert plan.procedure("root canal").category == "major"  # type: ignore[union-attr]
    assert plans.get_plan("UNKNOWN", 2026) is None
