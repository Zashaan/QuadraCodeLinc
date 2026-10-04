from pathlib import Path
from typing import Any, Protocol

from pydantic import TypeAdapter

from app.benefits.models import PlanRules

DEMO_PATH = Path(__file__).resolve().parents[3] / "data/demo"


class PlanRulesRepository(Protocol):
    def get_plan(
        self, plan_id: str, employer_id: str | None, plan_year: int | None
    ) -> PlanRules | None: ...


class LocalPlanRulesRepository:
    def __init__(self, path: Path | None = None) -> None:
        plans = TypeAdapter(list[PlanRules]).validate_json(
            (path or DEMO_PATH / "plans.json").read_text()
        )
        self._plans = {(p.plan_id, p.employer_id, p.plan_year): p for p in plans}
        if len(self._plans) != len(plans):
            raise ValueError("Duplicate plan rules")

    def get_plan(
        self, plan_id: str, employer_id: str | None, plan_year: int | None
    ) -> PlanRules | None:
        return self._plans.get((plan_id, employer_id, plan_year))


class DynamoDBPlanRulesRepository:
    """Join existing structured plan, category rules, and procedure catalog by IDs."""

    def __init__(
        self,
        plan_table_name: str,
        coverage_rule_table_name: str,
        procedure_table_name: str,
        region: str = "us-east-1",
        *,
        table: Any = None,
        coverage_table: Any = None,
        procedure_repository: Any = None,
    ) -> None:
        from app.aws_data import dynamodb_table
        from app.procedures.repository import DynamoDBProcedureRepository

        self._table = dynamodb_table(plan_table_name, region, table)
        self._coverage = dynamodb_table(coverage_rule_table_name, region, coverage_table)
        self._procedures = procedure_repository or DynamoDBProcedureRepository(
            procedure_table_name, region
        )
        self._name = plan_table_name

    def get_plan(
        self, plan_id: str, employer_id: str | None = None, plan_year: int | None = None
    ) -> PlanRules | None:
        from datetime import date
        from decimal import Decimal

        from boto3.dynamodb.conditions import Key  # type: ignore[import-untyped]

        from app.aws_data import normalize_numbers, read_all
        from app.benefits.models import CoverageRule, NetworkRule, ProcedureRule, Source

        raw = self._table.get_item(Key={"plan_id": plan_id}, ConsistentRead=True).get("Item")
        if raw is None:
            return None
        raw = normalize_numbers(raw)
        if raw.get("plan_id") != plan_id:
            raise ValueError("Mismatched plan ID")
        if employer_id is not None and raw.get("employer_id") != employer_id:
            return None
        if plan_year is not None and raw.get("plan_year") != plan_year:
            return None
        records = read_all(
            self._coverage,
            operation="query",
            KeyConditionExpression=Key("plan_id").eq(plan_id),
            ConsistentRead=True,
        )
        if not records:
            raise ValueError("Plan has no structured coverage rules")
        categories = {}
        for record in records:
            if record.get("plan_id") != plan_id or record.get("is_synthetic") is not True:
                raise ValueError("Mismatched or nonsynthetic coverage rule")
            category = record["category"]
            if category in categories:
                raise ValueError("Duplicate coverage category")
            categories[category] = CoverageRule(
                plan_share=Decimal(record["coverage_percent"]) / 100,
                deductible_applies=record["deductible_applies"],
                annual_maximum_applies=record["annual_maximum_applies"],
                orthodontic_lifetime_maximum_applies=record["orthodontic_lifetime_maximum_applies"],
            )
        catalog = self._procedures.list_procedures()
        aliases: dict[str, list[str]] = {}
        counts: dict[str, int] = {}
        for procedure in catalog:
            name = procedure.procedure_name.lower()
            candidate = name.split(" - ", 1)[0].removesuffix(" therapy")
            values = list(dict.fromkeys([name, candidate]))
            aliases[procedure.procedure_code] = values
            for alias in values:
                counts[alias] = counts.get(alias, 0) + 1
        procedures = {}
        preauth = raw.get("preauthorization")
        for procedure in catalog:
            if procedure.category not in categories:
                # An absent coverage category does not become invented zero/positive coverage.
                continue
            record = next(r for r in records if r["category"] == procedure.category)
            name = procedure.procedure_name.lower()
            frequency = _frequency_rule(name, raw.get("frequency_limits", {}))
            required = None
            threshold = None
            if isinstance(preauth, dict):
                tags = {procedure.category}
                if "implant" in name:
                    tags.add("implant")
                if "extraction" in name and procedure.specialty.casefold() == "oral surgeon":
                    tags.add("oral_surgery")
                required = bool(tags.intersection(preauth["required_categories"]))
                if procedure.category == "orthodontic":
                    required = required or preauth["orthodontics_required"]
                if procedure.category == "major":
                    threshold = preauth.get("major_service_threshold")
            procedures[procedure.procedure_code] = ProcedureRule(
                aliases=[a for a in aliases[procedure.procedure_code] if counts[a] == 1],
                category=procedure.category,
                waiting_period_months=record.get("waiting_period_months"),
                preauthorization_required=required,
                preauthorization_threshold=threshold,
                required_specialty=procedure.specialty,
                **frequency,
            )
        narratives = raw["network_rules"]
        in_text = narratives["in_network"].lower()
        out_text = narratives["out_of_network"].lower()
        if "no balance billing" not in in_text or not any(
            text in out_text
            for text in ("balance billing may apply", "provider may bill", "member may owe")
        ):
            raise ValueError("Unsupported network billing rule wording")
        return PlanRules(
            plan_id=plan_id,
            employer_id=raw.get("employer_id"),
            plan_year=raw.get("plan_year"),
            starts_on=date.fromisoformat(raw["starts_on"]) if raw.get("starts_on") else None,
            ends_on=date.fromisoformat(raw["ends_on"]) if raw.get("ends_on") else None,
            annual_maximum=raw["annual_maximum"],
            deductible=raw["annual_deductible_individual"],
            procedures=procedures,
            networks={
                "in_network": NetworkRule(balance_billing=False, categories=categories),
                "out_of_network": NetworkRule(balance_billing=True, categories=categories),
            },
            source=Source(
                source_id=f"dynamodb:{self._name}:{plan_id}",
                document=raw["plan_name"],
                plan_id=plan_id,
                employer=raw.get("employer_id"),
                plan_year=raw.get("plan_year"),
                state=raw.get("state"),
                doc_type="structured_plan_rules",
                synthetic=True,
            ),
        )


def _frequency_rule(name: str, limits: dict[str, str]) -> dict[str, Any]:
    """Map catalog terminology to structured policy keys, parse only documented grammar."""
    import re

    key = next((key for key in limits if key.replace("_", " ") in name), None)
    if "oral evaluation" in name:
        key = "exam"
    elif "bitewing" in name:
        key = "bitewing_xray"
    elif "complete series of intraoral" in name:
        key = "full_mouth_xray"
    value = limits.get(key) if key is not None else None
    if value is None:
        # Missing restriction is unknown, not an assertion of unlimited coverage.
        return {"frequency_verified_unrestricted": False}
    result: dict[str, Any] = {"frequency_limit": value}
    annual = re.fullmatch(r"(\d+) per benefit year", value)
    months = re.fullmatch(r"(\d+)( per tooth)? every (\d+) months", value)
    if annual:
        result["frequency_count"] = int(annual[1])
        # None months means benefit-year scope, evaluated against explicit plan dates.
    elif months:
        result.update(
            frequency_count=int(months[1]),
            frequency_months=int(months[3]),
            frequency_per_tooth=bool(months[2]),
        )
    return result
