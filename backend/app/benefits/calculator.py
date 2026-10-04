"""Pure one-scenario estimate. Does not retrieve data, rank choices, or call an LLM."""

from calendar import monthrange
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from app.benefits.models import BenefitEstimate, BenefitInput, PlanRules, UnavailableResult
from app.history.repository import MemberHistory
from app.members.repository import Member


def add_months(day: date, months: int) -> date:
    """Calendar months, including month-end enrollment dates (never approximate as days)."""
    year, month = divmod(day.year * 12 + day.month - 1 + months, 12)
    return date(year, month + 1, min(day.day, monthrange(year, month + 1)[1]))


def _eligibility(
    member: Member,
    plan: PlanRules,
    scenario: BenefitInput,
    procedure: str,
    history: MemberHistory | None,
) -> UnavailableResult | None:
    rule = plan.procedures[procedure]
    if member.member_status != "active":
        return UnavailableResult(
            status="unverified", reason="Active member eligibility is required."
        )
    if rule.waiting_period_days is None and rule.waiting_period_months is None:
        return UnavailableResult(status="unverified", reason="Waiting period is unknown.")
    if rule.waiting_period_months or rule.waiting_period_days:
        if member.coverage_start_date is None:
            return UnavailableResult(
                status="missing_information",
                reason="Enrollment date is required to verify the waiting period.",
                missing_fields=["coverage_start_date"],
            )
        end = (
            add_months(member.coverage_start_date, rule.waiting_period_months)
            if rule.waiting_period_months is not None
            else member.coverage_start_date + timedelta(days=rule.waiting_period_days or 0)
        )
        if scenario.treatment_date < end:
            return UnavailableResult(
                status="unverified", reason="The waiting period is not satisfied for that date."
            )
    if (
        member.coverage_start_date is not None
        and scenario.treatment_date < member.coverage_start_date
    ):
        return UnavailableResult(status="unverified", reason="Treatment precedes member coverage.")
    if rule.preauthorization_required is None:
        return UnavailableResult(
            status="unverified", reason="Preauthorization requirements are unknown."
        )
    authorization_required = rule.preauthorization_required or (
        rule.preauthorization_threshold is not None
        and scenario.provider_charge >= rule.preauthorization_threshold
    )
    if authorization_required:
        if history is None or not history.authorizations_complete:
            return UnavailableResult(
                status="missing_information",
                reason="Complete authorization records are required.",
                missing_fields=["authorizations"],
            )
        approved = any(
            auth.member_id == member.member_id
            and auth.procedure_code == procedure
            and auth.status == "approved"
            and auth.provider_id is not None
            and auth.provider_id == scenario.provider_id
            and auth.valid_from is not None
            and auth.valid_until is not None
            and auth.valid_from <= scenario.treatment_date <= auth.valid_until
            for auth in history.authorizations
        )
        if not approved:
            return UnavailableResult(
                status="unverified",
                reason="Approval for this provider and treatment date is not verified.",
            )
    if rule.frequency_count is not None:
        if history is None or not history.claims_complete:
            return UnavailableResult(
                status="missing_information",
                reason="Complete claim history is required for the frequency limit.",
                missing_fields=["claims"],
            )
        if rule.frequency_per_tooth and scenario.tooth_id is None:
            return UnavailableResult(
                status="missing_information",
                reason="The tooth number is required for this frequency limit.",
                missing_fields=["tooth_id"],
            )
        start = (
            add_months(scenario.treatment_date, -rule.frequency_months)
            if rule.frequency_months is not None
            else plan.starts_on
        )
        if start is None:
            return UnavailableResult(
                status="missing_information",
                reason="The benefit-year start is required for this frequency limit.",
                missing_fields=["benefit_year"],
            )
        count = 0
        for claim in history.claims:
            if (
                claim.member_id != member.member_id
                or claim.procedure_code != procedure
                or claim.claim_status == "denied"
            ):
                continue
            if not start <= claim.service_date <= scenario.treatment_date:
                continue
            if rule.frequency_per_tooth:
                if claim.tooth_id is None:
                    return UnavailableResult(
                        status="unverified",
                        reason="Relevant claim history lacks tooth information.",
                    )
                if claim.tooth_id != scenario.tooth_id:
                    continue
            if claim.claim_status != "paid":
                return UnavailableResult(
                    status="unverified", reason="A relevant claim has not been adjudicated."
                )
            count += 1
        if count >= rule.frequency_count:
            return UnavailableResult(
                status="unverified", reason="The procedure frequency limit has been reached."
            )
    elif not rule.frequency_verified_unrestricted or rule.frequency_limit is not None:
        return UnavailableResult(
            status="unverified", reason="Frequency eligibility requires verification."
        )
    return None


def calculate_benefit(
    member: Member,
    plan: PlanRules,
    scenario: BenefitInput,
    *,
    history: MemberHistory | None = None,
) -> BenefitEstimate | UnavailableResult:
    if member.plan_id != plan.plan_id or any(
        left is not None and right is not None and left != right
        for left, right in [
            (member.employer_id, plan.employer_id),
            (member.plan_year, plan.plan_year),
        ]
    ):
        return UnavailableResult(status="unverified", reason="Member and plan do not match.")
    if plan.starts_on is None or plan.ends_on is None or member.balances_as_of is None:
        return UnavailableResult(
            status="missing_information",
            reason="Verified benefit-year dates and balance date are required for an estimate.",
            missing_fields=["benefit_year", "balances_as_of"],
        )
    if not plan.starts_on <= scenario.treatment_date <= plan.ends_on:
        return UnavailableResult(
            status="unverified", reason="No rules and balances for that treatment date."
        )
    if not plan.starts_on <= member.balances_as_of <= plan.ends_on:
        return UnavailableResult(
            status="unverified", reason="Stored balances are from a different benefit period."
        )
    if scenario.treatment_date < member.balances_as_of:
        return UnavailableResult(status="unverified", reason="Historical balances are unavailable.")
    if member.annual_maximum != plan.annual_maximum or member.deductible_total != plan.deductible:
        return UnavailableResult(
            status="unverified", reason="Member limits disagree with structured rules."
        )
    if (
        member.state is not None
        and plan.source.state is not None
        and member.state != plan.source.state
    ):
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
    blocked = _eligibility(member, plan, scenario, procedure, history)
    if blocked is not None:
        return blocked
    rule = plan.procedures[procedure]
    coverage = network.categories[rule.category]
    eligible = min(scenario.provider_charge, scenario.allowed_amount)
    deductible = (
        min(Decimal(member.deductible_remaining), eligible)
        if coverage.deductible_applies
        else Decimal(0)
    )
    after = eligible - deductible
    uncapped = (after * coverage.plan_share).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    payment = (
        min(uncapped, Decimal(member.annual_maximum_remaining))
        if coverage.annual_maximum_applies
        else uncapped
    )
    ortho_remaining = None
    if coverage.orthodontic_lifetime_maximum_applies:
        if member.orthodontic_lifetime_maximum is None or member.orthodontic_lifetime_used is None:
            return UnavailableResult(
                status="missing_information",
                reason="Orthodontic lifetime limits and usage are required.",
                missing_fields=["orthodontic_lifetime_maximum", "orthodontic_lifetime_used"],
            )
        if member.orthodontic_lifetime_used > member.orthodontic_lifetime_maximum:
            return UnavailableResult(
                status="unverified", reason="Orthodontic usage exceeds its stored limit."
            )
        ortho_remaining = Decimal(
            member.orthodontic_lifetime_maximum - member.orthodontic_lifetime_used
        )
        payment = min(payment, ortho_remaining)
        ortho_remaining -= payment
    annual_consumed = payment if coverage.annual_maximum_applies else Decimal(0)
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
        annual_maximum_consumed=annual_consumed,
        annual_maximum_remaining_afterward=Decimal(member.annual_maximum_remaining)
        - annual_consumed,
        deductible_remaining_afterward=Decimal(member.deductible_remaining) - deductible,
        orthodontic_lifetime_remaining_afterward=ortho_remaining,
        steps=[
            f"Eligible charge: {eligible:.2f}",
            f"Deductible applied: {deductible:.2f}",
            f"Configured plan share: {coverage.plan_share}; uncapped payment: {uncapped:.2f}",
            f"Payment after applicable annual/lifetime caps: {payment:.2f}",
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
