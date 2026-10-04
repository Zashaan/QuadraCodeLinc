from typing import Any

import pytest
from pydantic import ValidationError

from app.members.repository import DemoMemberRepository, Member
from app.tools.get_member import GetMemberArguments, GetMemberResult, GetMemberTool


def test_demo_member_has_exact_stored_balances() -> None:
    tool = GetMemberTool(DemoMemberRepository())
    result = tool.invoke({"member_id": "DEMO001"})
    assert result.status == "success"
    assert result.member is not None
    assert result.member.model_dump() == {
        "member_id": "DEMO001",
        "name": "Demo Member",
        "plan_id": "DEMO_DENTAL_PPO",
        "currency": "USD",
        "annual_maximum": 2000,
        "annual_maximum_used": 1200,
        "annual_maximum_remaining": 800,
        "deductible_total": 50,
        "deductible_used": 50,
        "deductible_remaining": 0,
        "fsa_balance": 350,
    }


def test_remaining_value_is_passed_from_repository_not_recomputed() -> None:
    member = DemoMemberRepository().get_member("DEMO001")
    assert member is not None
    stored = member.model_copy(update={"annual_maximum_remaining": 731})

    class StoredBalanceRepository:
        def get_member(self, member_id: str) -> Member | None:
            return stored

    result = GetMemberTool(StoredBalanceRepository()).invoke({"member_id": "DEMO001"})
    assert result.member is not None
    assert result.member.annual_maximum_remaining == 731


def test_unknown_id_returns_not_found() -> None:
    result = GetMemberTool(DemoMemberRepository()).invoke({"member_id": "missing"})
    assert result.model_dump(exclude_none=True) == {"status": "not_found", "member_id": "MISSING"}


def test_member_id_is_case_insensitive() -> None:
    result = GetMemberTool(DemoMemberRepository()).invoke({"member_id": "demo001"})
    assert result.member is not None
    assert result.member.member_id == "DEMO001"


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"member_id": ""},
        {"member_id": 1},
        {"member_id": "x" * 33},
        {"member_id": "../DEMO001"},
        {"member_id": "DEMO001", "shell": "bad"},
        {"member_id": "DEMO 001"},
        {"member_id": ["DEMO001"]},
    ],
)
def test_invalid_tool_input(arguments: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        GetMemberArguments.model_validate(arguments)


def test_tool_schema_forbids_extra_fields_and_requires_id() -> None:
    schema = GetMemberTool(DemoMemberRepository()).definition.input_schema
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["member_id"]
    properties = schema["properties"]
    assert isinstance(properties, dict)
    assert list(properties) == ["member_id"]


@pytest.mark.parametrize(
    "value",
    [
        {"status": "success"},
        {"status": "not_found"},
        {"status": "not_found", "member_id": "X", "unexpected": "secret"},
    ],
)
def test_invalid_result_is_rejected(value: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        GetMemberResult.model_validate(value)
