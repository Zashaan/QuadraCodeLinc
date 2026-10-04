"""Backend-only synthetic member → plan → provider → Decimal calculator smoke test."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.calculator.benefits import BenefitCalculator  # noqa: E402
from app.members.repository import SyntheticMemberRepository  # noqa: E402
from app.plans.repository import LocalPlanRulesRepository  # noqa: E402
from app.providers.repository import SyntheticProviderRepository  # noqa: E402
from app.tools.calculate_benefit import CalculateBenefitTool  # noqa: E402

members = SyntheticMemberRepository()
plans = LocalPlanRulesRepository()
providers = SyntheticProviderRepository()
member = members.get_member("DEMO001")
assert member is not None and member.annual_maximum_remaining == 800

result = CalculateBenefitTool(BenefitCalculator(), members, plans, providers).invoke(
    {
        "member_id": "DEMO001",
        "procedure": "crown",
        "treatment_date": "2026-06-01",
        "provider_id": "SYNTH001",
    }
)
assert result.status == "estimated"
assert str(result.plan_payment) == "600.00"  # type: ignore[union-attr]
assert str(result.annual_maximum_remaining_after) == "200.00"  # type: ignore[union-attr]
print("PASS: DEMO001 synthetic crown estimate used stored $800 remaining maximum.")
