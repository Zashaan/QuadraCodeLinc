"""Pure one-scenario estimate. Does not retrieve data, rank choices, or call an LLM."""

from decimal import ROUND_HALF_UP, Decimal

from app.benefits.models import BenefitEstimate, BenefitInput, PlanRules, UnavailableResult
from app.members.repository import Member


def calculate_benefit(
    member: Member, plan: PlanRules, scenario: BenefitInput
) -> BenefitEstimate | UnavailableResult:
    if (member.plan_id, member.employer_id, member.plan_year) != (
        plan.plan_id,
        plan.employer_id,
        plan.plan_year,
    ):
        return UnavailableResult(status="unverified", reason="Member and plan do not match.")
    if not plan.starts_on <= scenario.treatment_date <= plan.ends_on:
        return UnavailableResult(
            status="unverified", reason="No rules and balances for that treatment date."
        )
    if scenario.treatment_date < member.balances_as_of:
        return UnavailableResult(status="unverified", reason="Historical balances are unavailable.")
    if member.annual_maximum != plan.annual_maximum or member.deductible_total != plan.deductible:
        return UnavailableResult(
            status="unverified", reason="Member limits disagree with structured rules."
        )
    if member.state != plan.source.state:
        return UnavailableResult(
            status="unverified", reason="Plan state does not match the member."
        )
    if (
        member.annual_maximum_remaining > member.annual_maximum
        or member.deductible_remaining > member.deductible_total
    ):
        return UnavailableResult(status="unverified", reason="Stored balances exceed plan limits.")
    procedure = plan.procedure_id(scenario.procedure)
    network = plan.networks.get(scenario.network_status)
    if procedure is None or network is None:
        return UnavailableResult(
            status="unverified", reason="Procedure or network rule is unavailable."
        )
    rule = plan.procedures[procedure]
    if (
        rule.waiting_period_days != 0
        or not rule.frequency_verified_unrestricted
        or rule.frequency_limit is not None
    ):
        return UnavailableResult(
            status="unverified",
            reason="Waiting period or frequency eligibility requires verification.",
        )
    coverage = network.categories[rule.category]
    eligible = min(scenario.provider_charge, scenario.allowed_amount)
    deductible = (
        min(Decimal(member.deductible_remaining), eligible)
        if coverage.deductible_applies
        else Decimal(0)
    )
    after = eligible - deductible
    uncapped = (after * coverage.plan_share).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    payment = min(uncapped, Decimal(member.annual_maximum_remaining))
    excess = scenario.provider_charge - eligible
    balance_bill = excess if network.balance_billing else Decimal(0)
    write_off = excess - balance_bill
    return BenefitEstimate(
        member_id=member.member_id,
        plan_id=member.plan_id,
        treatment_date=scenario.treatment_date,
        balances_as_of=member.balances_as_of,
        procedure=procedure,
        network_status=scenario.network_status,
        provider_charge=scenario.provider_charge,
        allowed_amount=scenario.allowed_amount,
        deductible_applied=deductible,
        amount_after_deductible=after,
        plan_payment=payment,
        estimated_member_payment=eligible - payment + balance_bill,
        amount_not_covered=balance_bill + uncapped - payment,
        contractual_write_off=write_off,
        annual_maximum_consumed=payment,
        annual_maximum_remaining_afterward=Decimal(member.annual_maximum_remaining) - payment,
        steps=[
            f"Eligible charge: {eligible:.2f}",
            f"Deductible applied: {deductible:.2f}",
            f"Configured plan share: {coverage.plan_share}; uncapped payment: {uncapped:.2f}",
            f"Payment capped at stored remaining maximum: {payment:.2f}",
        ],
        assumptions=[
            "One procedure; no intervening claims or other insurance.",
            f"Balances as of {member.balances_as_of.isoformat()}; "
            "estimate does not reserve benefits.",
        ],
        warnings=["Estimate only, not a guarantee of coverage or payment."]
        + (
            ["Caller-reported prices, allowed amount or network are unverified assumptions."]
            if any(
                "caller" in s
                for s in [
                    scenario.fee_source,
                    scenario.allowed_amount_source,
                    scenario.network_source,
                ]
            )
            else []
        )
        + (["SYNTHETIC DEMO DATA — NOT REAL LINCOLN DATA"] if plan.source.synthetic else []),
        sources=[plan.source],
        fee_source=scenario.fee_source,
        allowed_amount_source=scenario.allowed_amount_source,
        network_source=scenario.network_source,
    )
