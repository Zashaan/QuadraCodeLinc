"""Build at most 40 scenarios, reject hard violations, then rank using exact arithmetic."""

from datetime import date, timedelta
from decimal import Decimal
from typing import TypedDict

from app.benefits.calculator import add_months, calculate_benefit
from app.benefits.models import BenefitEstimate, BenefitInput, PlanRules, UnavailableResult
from app.history.repository import MemberHistory
from app.members.repository import Member
from app.optimization.models import (
    ExcludedScenario,
    OptimizationRequest,
    OptimizationResult,
    Scenario,
)
from app.providers.repository import Provider


class _ResultContext(TypedDict):
    scenarios_evaluated: int
    feasible_scenarios: int
    excluded: list[ExcludedScenario]
    missing_fields: list[str]
    assumptions: list[str]
    follow_up: str | None


def _constraint_failure(
    member: Member,
    plan: PlanRules,
    provider: Provider,
    request: OptimizationRequest,
    day: date,
) -> tuple[str, list[str]] | None:
    c = request.constraints
    if member.member_status != "active":
        return "Active member eligibility is required.", ["member_status"]
    if provider.plan_id != plan.plan_id or provider.network_scope != "plan":
        return "Provider participation in this member's plan is unverified.", [
            "plan_network_status"
        ]
    if day < request.requested_date:
        return "The date precedes the requested treatment window.", []
    if day > request.requested_date and (
        c.urgent or not c.dentist_confirmed_multiple_dates or c.dentist_safe_until is None
    ):
        return "Delayed care requires dentist-approved timing and no reported urgency.", [
            "dentist_safe_timing"
        ]
    if c.dentist_safe_until is not None and day > c.dentist_safe_until:
        return "The date exceeds the dentist's safe timing window.", []
    if c.deadline is not None and day > c.deadline:
        return "The date exceeds the caller's deadline.", []
    if c.required_network is not None and provider.network_status != c.required_network:
        return "Provider does not meet the required network constraint.", []
    code = plan.procedure_id(request.procedure)
    specialties = [c.required_specialty, plan.procedures[code].required_specialty if code else None]
    if any(
        specialty is not None and provider.specialty.casefold() != specialty.casefold()
        for specialty in specialties
    ):
        return "Provider does not meet the required specialty.", []
    if code is not None:
        network = plan.networks.get(provider.network_status)
        category = plan.procedures[code].category
        if network is None or network.categories[category].plan_share == 0:
            return "The procedure is not covered under this network rule.", []
    if c.new_patient and provider.accepting_new_patients is not True:
        return "Provider acceptance of new patients is not confirmed.", ["accepting_new_patients"]
    distance = provider.distances_miles.get(request.origin_zip or member.zip_code or "")
    if c.max_distance_miles is not None and (distance is None or distance > c.max_distance_miles):
        return "Provider distance is unknown or exceeds the hard travel limit.", [
            "distance_miles"
        ] if distance is None else []
    if request.preferences.priority == "closest" and distance is None:
        return "Distance is required to identify the closest provider.", ["distance_miles"]
    if provider.available_dates is not None and day not in provider.available_dates:
        return "Provider is unavailable on this date.", []
    if request.preferences.priority == "fastest" and provider.available_dates is None:
        return "Appointment availability is required to identify the fastest option.", [
            "provider_availability"
        ]
    return None


def _period(
    member: Member,
    plan: PlanRules,
    request: OptimizationRequest,
    day: date,
) -> tuple[Member, PlanRules, list[str]] | UnavailableResult:
    if plan.starts_on is None or plan.ends_on is None or plan.plan_year is None:
        return UnavailableResult(
            status="missing_information",
            reason="Verified benefit-year dates are required.",
            missing_fields=["benefit_year"],
        )
    if plan.starts_on <= day <= plan.ends_on:
        return member, plan, []
    c = request.constraints
    if c.urgent or not c.dentist_confirmed_multiple_dates or c.dentist_safe_until is None:
        return UnavailableResult(
            status="missing_information",
            reason="A future-year timing projection requires dentist-approved timing.",
            missing_fields=["dentist_safe_timing"],
        )
    reset = plan.ends_on + timedelta(days=1)
    next_end = add_months(reset, 12) - timedelta(days=1)
    if not reset <= day <= next_end or not request.constraints.assume_continued_plan_terms:
        return UnavailableResult(
            status="missing_information",
            reason="Next-year projection requires an explicit continued-terms assumption.",
            missing_fields=["continued_plan_terms"],
        )
    # This is a labeled simulation; original records and original source metadata stay intact.
    projected_plan = plan.model_copy(
        update={"plan_year": reset.year, "starts_on": reset, "ends_on": next_end}
    )
    projected_member = member.model_copy(
        update={
            "plan_year": reset.year,
            "balances_as_of": reset,
            "annual_maximum": plan.annual_maximum,
            "annual_maximum_used": Decimal(0),
            "annual_maximum_remaining": plan.annual_maximum,
            "deductible_total": plan.deductible,
            "deductible_used": Decimal(0),
            "deductible_remaining": plan.deductible,
        }
    )
    return (
        projected_member,
        projected_plan,
        [
            "Hypothetical next year: assumes continued enrollment and unchanged terms.",
            "Annual/deductible limits reset; no other claims. Lifetime usage does not reset.",
            "Timing comparison requires your dentist to approve either timing.",
        ],
    )


def _scenario(
    member: Member,
    plan: PlanRules,
    provider: Provider,
    request: OptimizationRequest,
    day: date,
    history: MemberHistory | None,
) -> Scenario | UnavailableResult:
    period = _period(member, plan, request, day)
    if isinstance(period, UnavailableResult):
        return period
    projected_member, projected_plan, assumptions = period
    code = plan.procedure_id(request.procedure)
    quote = next((q for q in request.fee_quotes if q.provider_id == provider.provider_id), None)
    fee = provider.fees.get(code or "")
    if quote is None and (fee is None or fee.provider_charge is None or fee.allowed_amount is None):
        return UnavailableResult(
            status="missing_information",
            reason="Provider quote and plan allowance required; typical prices are insufficient.",
            missing_fields=["provider_charge", "allowed_amount"],
        )
    if (
        quote is not None
        and fee is not None
        and fee.allowed_amount is not None
        and quote.allowed_amount != fee.allowed_amount
    ):
        return UnavailableResult(
            status="unverified",
            reason="Reported allowed amount conflicts with the structured provider record.",
        )
    if quote is not None:
        charge, allowed, source = quote.provider_charge, quote.allowed_amount, "caller_reported"
        assumptions.append(
            "Provider charge and plan allowance were reported by the caller and are unverified."
        )
    else:
        assert (
            fee is not None and fee.provider_charge is not None and fee.allowed_amount is not None
        )
        charge, allowed, source = fee.provider_charge, fee.allowed_amount, fee.source
    calculated = calculate_benefit(
        projected_member,
        projected_plan,
        BenefitInput(
            procedure=request.procedure,
            treatment_date=day,
            network_status=provider.network_status,
            provider_charge=charge,
            allowed_amount=allowed,
            fee_source=source,
            allowed_amount_source=source,
            network_source=provider.source,
            provider_id=provider.provider_id,
            tooth_id=request.tooth_id,
        ),
        history=history,
    )
    if not isinstance(calculated, BenefitEstimate):
        return calculated
    if provider.available_dates is None:
        assumptions.append(
            "Appointment availability is unknown; the scenario does not book an appointment."
        )
    projected = projected_member is not member
    fsa_applied = Decimal(0)
    wants_fsa = request.use_fsa or request.preferences.priority == "cash"
    if wants_fsa:
        if member.fsa_balance is None:
            assumptions.append("FSA balance is unknown; no FSA funds were assumed.")
        elif member.fsa_expires_on is None and projected:
            assumptions.append("FSA expiry/carryover is unknown; next-year FSA use is not assumed.")
        elif member.fsa_expires_on is None or day <= member.fsa_expires_on:
            fsa_applied = min(Decimal(member.fsa_balance), calculated.estimated_member_payment)
            assumptions.append("FSA covers member responsibility; it is not an insurance discount.")
            if member.fsa_expires_on is None:
                assumptions.append(
                    "FSA expiry is unknown; verify the account's reimbursement deadline."
                )
        else:
            assumptions.append(
                "The recorded FSA use-by date precedes treatment; no FSA funds were applied."
            )
    distance = provider.distances_miles.get(request.origin_zip or member.zip_code or "")
    if distance is None:
        assumptions.append("Travel distance is unknown; verify it before selecting a provider.")
    return Scenario(
        scenario_id=f"{provider.provider_id}:{day.isoformat()}",
        provider_id=provider.provider_id,
        provider_name=provider.name,
        treatment_date=day,
        benefit_year=projected_plan.plan_year or day.year,
        timing="after_benefit_reset" if projected else "current_benefit_year",
        network_status=provider.network_status,
        distance_miles=distance,
        availability_confirmed=provider.available_dates is not None,
        estimate=calculated,
        fsa_applied=fsa_applied,
        estimated_cash_payment=calculated.estimated_member_payment - fsa_applied,
        assumptions=assumptions,
        reasons=["Passed the supplied hard constraints and structured benefit eligibility checks."],
        tradeoffs=[],
    )


def _score(scenario: Scenario, request: OptimizationRequest) -> tuple[Decimal, ...]:
    p = request.preferences
    cost = scenario.estimate.estimated_member_payment
    # Unknown distance is penalized, never silently interpreted as zero miles.
    distance = Decimal(scenario.distance_miles if scenario.distance_miles is not None else 100)
    days = Decimal((scenario.treatment_date - request.requested_date).days)
    network = Decimal(0 if scenario.network_status == "in_network" else 1)
    score = (
        cost * p.cost_priority
        + days * p.time_priority * 10
        + distance * p.distance_priority * 5
        + network * p.network_priority * 50
    )
    primary = {
        "balanced": score,
        "cheapest": cost,
        "fastest": days,
        "closest": distance,
        "cash": scenario.estimated_cash_payment,
    }[p.priority]
    return primary, score, cost, days, distance


def optimize_benefits(
    member: Member,
    plan: PlanRules,
    providers: list[Provider],
    request: OptimizationRequest,
    *,
    history: MemberHistory | None = None,
) -> OptimizationResult:
    """No I/O, LLM, mutation or hidden clock; caller supplies repository data and intent."""
    dates = {request.requested_date, *request.candidate_dates}
    if request.compare_benefit_year_reset and plan.ends_on is not None:
        dates.add(plan.ends_on + timedelta(days=1))
    # Requested date plus at most three other meaningful dates, including the reset if asked.
    if len(dates) > 4:
        dates = (
            {
                request.requested_date,
                *sorted(dates - {request.requested_date})[:2],
                plan.ends_on + timedelta(days=1),
            }
            if plan.ends_on
            else set(sorted(dates)[:4])
        )
    excluded: list[ExcludedScenario] = []
    feasible: list[Scenario] = []
    selected_providers = sorted(providers, key=lambda p: p.provider_id)[:10]
    for provider in selected_providers:
        provider_dates = set(dates)
        if provider.available_dates:
            usable = [d for d in provider.available_dates if d >= request.requested_date]
            if usable and len(provider_dates) < 4:
                provider_dates.add(min(usable))
        for day in sorted(provider_dates):
            failure = _constraint_failure(member, plan, provider, request, day)
            if failure:
                excluded.append(
                    ExcludedScenario(
                        provider_id=provider.provider_id,
                        treatment_date=day,
                        reason=failure[0],
                        missing_fields=failure[1],
                    )
                )
                continue
            scenario = _scenario(member, plan, provider, request, day, history)
            if isinstance(scenario, UnavailableResult):
                excluded.append(
                    ExcludedScenario(
                        provider_id=provider.provider_id,
                        treatment_date=day,
                        reason=scenario.reason,
                        missing_fields=scenario.missing_fields,
                    )
                )
            else:
                feasible.append(scenario)
    missing = sorted({field for e in excluded for field in e.missing_fields})
    if not selected_providers:
        missing.append("candidate_providers")
    if request.compare_benefit_year_reset and plan.ends_on is None:
        missing.append("benefit_year")
    follow_up = None
    if "dentist_safe_timing" in missing:
        follow_up = "Did your dentist say how long this can safely wait?"
    elif missing:
        follow_up = {
            "plan_network_status": "Has the provider confirmed participation in your plan?",
            "benefit_year": "Can you confirm your benefit-year dates?",
            "provider_charge": "Do you have the provider's quote and your plan's allowed amount?",
        }.get(missing[0], "Can you confirm the missing information before I compare these options?")
    common: _ResultContext = {
        "scenarios_evaluated": len(excluded) + len(feasible),
        "feasible_scenarios": len(feasible),
        "excluded": excluded,
        "missing_fields": sorted(set(missing)),
        "assumptions": [
            "Financial estimates only; treatment timing is determined by your dentist."
        ],
        "follow_up": follow_up,
    }
    if not feasible:
        return OptimizationResult(
            status="missing_information" if missing else "no_feasible_scenarios", **common
        )
    ranked = sorted(feasible, key=lambda s: (*_score(s, request), s.scenario_id))
    best = ranked[0].model_copy(
        update={
            "reasons": [
                *ranked[0].reasons,
                f"Best match for the caller's {request.preferences.priority} preferences.",
            ]
        }
    )
    others = [s for s in feasible if s.scenario_id != best.scenario_id]
    cheapest = min(
        others, key=lambda s: (s.estimate.estimated_member_payment, s.scenario_id), default=None
    )
    fastest = min(
        (s for s in others if s.availability_confirmed),
        key=lambda s: (s.treatment_date, s.scenario_id),
        default=None,
    )
    closest = min(
        (s for s in others if s.distance_miles is not None),
        key=lambda s: (s.distance_miles or 0, s.scenario_id),
        default=None,
    )
    if (
        cheapest is not None
        and best.estimate.estimated_member_payment - cheapest.estimate.estimated_member_payment < 25
    ):
        cheapest = None
    if fastest is not None and (
        not best.availability_confirmed or (best.treatment_date - fastest.treatment_date).days < 2
    ):
        fastest = None
    if closest is not None and (
        best.distance_miles is None or best.distance_miles - (closest.distance_miles or 0) < 5
    ):
        closest = None
    seen = {best.scenario_id}
    alternatives: list[Scenario | None] = []
    for alternative, reason in [
        (
            cheapest,
            "Lower estimated insurance/member cost; compare the travel and appointment tradeoffs.",
        ),
        (fastest, "Earlier known availability; compare its estimated cost and travel."),
        (closest, "Shorter known travel distance; compare its estimated cost and timing."),
    ]:
        if alternative is not None and alternative.scenario_id not in seen:
            seen.add(alternative.scenario_id)
            alternatives.append(alternative.model_copy(update={"tradeoffs": [reason]}))
        else:
            alternatives.append(None)
    return OptimizationResult(
        status="optimized",
        best_overall=best,
        cheapest_alternative=alternatives[0],
        fastest_alternative=alternatives[1],
        closest_alternative=alternatives[2],
        **common,
    )
