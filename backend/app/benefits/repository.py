from pathlib import Path
from typing import Protocol

from pydantic import TypeAdapter

from app.benefits.models import PlanRules

DEMO_PATH = Path(__file__).resolve().parents[3] / "data/demo"


class PlanRulesRepository(Protocol):
    def get_plan(self, plan_id: str, employer_id: str, plan_year: int) -> PlanRules | None: ...


class LocalPlanRulesRepository:
    def __init__(self, path: Path | None = None) -> None:
        plans = TypeAdapter(list[PlanRules]).validate_json(
            (path or DEMO_PATH / "plans.json").read_text()
        )
        self._plans = {(p.plan_id, p.employer_id, p.plan_year): p for p in plans}
        if len(self._plans) != len(plans):
            raise ValueError("Duplicate plan rules")

    def get_plan(self, plan_id: str, employer_id: str, plan_year: int) -> PlanRules | None:
        return self._plans.get((plan_id, employer_id, plan_year))
