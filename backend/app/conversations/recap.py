from decimal import Decimal

from app.benefits.models import BenefitEstimate
from app.conversation.sessions import Session
from app.conversations.models import Recap, SuggestedAction
from app.providers.repository import Provider
from app.tools.get_member import GetMemberResult


def usd(value: Decimal | int | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, int):
        return f"{value:.2f}"
    return format(value, ".2f")


def build_recap(
    session: Session,
    providers: list[Provider] | None = None,
) -> Recap | None:
    provider_id = session.context.get("provider_id")
    procedure = session.context.get("procedure")
    raw_constraints = session.context.get("constraints")
    constraints = (
        [str(item) for item in raw_constraints] if isinstance(raw_constraints, list) else []
    )
    provider_name = None
    network_status = None
    if isinstance(provider_id, str):
        known = providers or session.last_providers
        match = next((row for row in known if row.provider_id == provider_id), None)
        if match is not None:
            provider_name = match.name
            network_status = match.network_status

    member = None
    member_result = session.last_tool_result
    if isinstance(member_result, GetMemberResult) and member_result.member:
        member = member_result.member
    estimate = session.last_estimate
    if (
        procedure is None
        and provider_id is None
        and member is None
        and not isinstance(estimate, BenefitEstimate)
    ):
        return None
    return Recap(
        sources=session.sources[:4],
        provider_id=str(provider_id) if isinstance(provider_id, str) else None,
        provider_name=provider_name,
        procedure=str(procedure) if isinstance(procedure, str) else None,
        constraints=constraints[:5],
        plan_payment=usd(estimate.plan_payment) if isinstance(estimate, BenefitEstimate) else None,
        estimated_member_payment=(
            usd(estimate.estimated_member_payment)
            if isinstance(estimate, BenefitEstimate)
            else None
        ),
        deductible_applied=(
            usd(estimate.deductible_applied) if isinstance(estimate, BenefitEstimate) else None
        ),
        annual_maximum_remaining=usd(member.annual_maximum_remaining) if member else None,
        annual_maximum_remaining_afterward=(
            usd(estimate.annual_maximum_remaining_afterward)
            if isinstance(estimate, BenefitEstimate)
            else None
        ),
        fsa_balance=usd(member.fsa_balance) if member and member.fsa_balance is not None else None,
        network_status=network_status or (estimate.network_status if estimate else None),
    )


def build_actions(recap: Recap | None) -> list[SuggestedAction]:
    if recap is None or recap.estimated_member_payment is None:
        return []
    return [
        SuggestedAction(
            id="A",
            label="Explain this estimate",
            prompt="Explain the saved estimate and its assumptions in simple terms.",
        ),
        SuggestedAction(
            id="B",
            label="Find in-network options",
            prompt="Show synthetic in-network dentists nearby for this procedure.",
        ),
        SuggestedAction(
            id="C",
            label="Check my plan details",
            prompt="What does my plan document say about this procedure?",
        ),
    ]


def conversation_title(recap: Recap | None) -> str:
    if recap is None:
        return "Abe call"
    if recap.provider_name and recap.procedure:
        return f"{recap.provider_name} · {recap.procedure}"
    if recap.provider_name:
        return recap.provider_name
    if recap.procedure:
        return recap.procedure
    return "Abe call"


def conversation_tag(recap: Recap | None) -> str:
    if recap is None:
        return "Call"
    if recap.procedure:
        return recap.procedure
    return "Call"


def conversation_preview(recap: Recap | None, fallback: str) -> str:
    if recap is None:
        return fallback
    parts: list[str] = []
    if recap.procedure:
        parts.append(recap.procedure)
    if recap.estimated_member_payment:
        parts.append(f"member estimate ${recap.estimated_member_payment}")
    if recap.annual_maximum_remaining:
        parts.append(f"max remaining ${recap.annual_maximum_remaining}")
    return "; ".join(parts)[:240] if parts else fallback
