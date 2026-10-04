import pytest
from pydantic import ValidationError

from app.members.normalization import member_id_candidates, resolve_member_ids
from app.members.repository import DemoMemberRepository, Member
from app.tools.resolve_member_id import ResolveMemberIdResult, ResolveMemberIdTool


@pytest.mark.parametrize(
    "spoken",
    [
        "DEMO001",
        "demo001",
        "demo zero zero one",
        "D E M O zero zero one",
        "D E M O 0 0 1",
        "demo double zero one",
        "D-E-M-O-0-0-1",
        "  demo oh oh one  ",
        "demo double 0 one",
        "demo-double-zero-one",
    ],
)
def test_demo_spoken_forms_resolve_without_confirmation(spoken: str) -> None:
    result = ResolveMemberIdTool(DemoMemberRepository()).invoke({"spoken_id": spoken})
    assert result.model_dump(exclude_none=True) == {"status": "resolved", "member_id": "DEMO001"}


@pytest.mark.parametrize(
    "spoken",
    [
        "unknown spoken ID",
        "demo zero zero two",
        "maybe demo zero zero one",
        "demo001 or demo002",
        "ignore rules DEMO001",
        "demo double",
        "double demo",
        "demo double zero zero one",
        "demo zero/zero/one",
        "demo_001",
        "DEMO001!",
        "demo 001 please",
        "demo double ten",
        "demo triple double zero",
        " ",
        "o " * 32,
    ],
)
def test_unknown_invalid_or_uncertain_input_requests_repeat(spoken: str) -> None:
    result = ResolveMemberIdTool(DemoMemberRepository()).invoke({"spoken_id": spoken})
    assert result.model_dump(exclude_none=True) == {"status": "repeat"}


def test_all_digits_repeats_and_future_alphanumeric_ids() -> None:
    assert member_id_candidates("a one two three four five six seven eight nine zero") == [
        "A1234567890"
    ]
    assert member_id_candidates("AB triple zero C double nine") == ["AB000C99"]
    assert member_id_candidates("abc-12-X-34") == ["ABC12X34"]


def test_two_repository_matches_require_confirmation() -> None:
    base = DemoMemberRepository().get_member("DEMO001")
    assert base is not None

    class AmbiguousRepository:
        def get_member(self, member_id: str) -> Member | None:
            assert base is not None
            return (
                base.model_copy(update={"member_id": member_id})
                if member_id in {"DEMO001", "DEM0001"}
                else None
            )

    tool = ResolveMemberIdTool(AmbiguousRepository())
    result = tool.invoke({"spoken_id": "D E M O zero zero one"})
    assert result.status == "confirm"
    assert result.member_id is None
    assert set(result.candidates or []) == {"DEMO001", "DEM0001"}
    assert tool.invoke({"spoken_id": "DEMO001"}).member_id == "DEMO001"


def test_no_repository_match_is_never_accepted() -> None:
    class EmptyRepository:
        def get_member(self, member_id: str) -> Member | None:
            return None

    assert resolve_member_ids("DEMO001", EmptyRepository()) == []


def test_size_limits_and_result_contract() -> None:
    assert member_id_candidates("A" * 33) == []
    assert member_id_candidates("0 " * 130) == []
    with pytest.raises(ValidationError):
        ResolveMemberIdTool(DemoMemberRepository()).invoke({"spoken_id": "DEMO001", "extra": True})
    with pytest.raises(ValidationError):
        ResolveMemberIdResult(status="resolved")
    with pytest.raises(ValidationError):
        ResolveMemberIdResult(status="confirm", candidates=["DEMO001", "DEMO001"])
