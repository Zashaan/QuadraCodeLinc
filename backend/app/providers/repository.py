from pathlib import Path
from typing import Annotated, Literal, Protocol

from pydantic import Field, TypeAdapter

from app.benefits.models import Money, Network, ShortText
from app.benefits.repository import DEMO_PATH
from app.models import StrictModel


class ProcedureFee(StrictModel):
    provider_charge: Money | None = None
    allowed_amount: Money | None = None
    source: ShortText


class Provider(StrictModel):
    provider_id: ShortText
    name: ShortText
    specialty: ShortText
    plan_id: ShortText
    network_status: Network
    zip_code: Annotated[str, Field(pattern=r"^[0-9]{5}$")]
    # Explicit synthetic distances from named origins; no invented geocoding.
    distances_miles: dict[str, Annotated[int, Field(ge=0)]]
    fees: dict[str, ProcedureFee]
    source: ShortText
    verification_status: Literal["synthetic"]
    verified_on: ShortText
    synthetic: Literal[True]


class ProviderSearch(StrictModel):
    zip_code: Annotated[str, Field(pattern=r"^[0-9]{5}$")] | None = None
    radius_miles: Annotated[int, Field(ge=0, le=100)] | None = None
    network_status: Network | None = None
    specialty: ShortText | None = None
    procedure: ShortText | None = None


class ProviderRepository(Protocol):
    def get_provider(self, provider_id: str, plan_id: str) -> Provider | None: ...
    def search(self, plan_id: str, query: ProviderSearch) -> list[Provider]: ...


class SyntheticProviderRepository:
    def __init__(self, path: Path | None = None) -> None:
        providers = TypeAdapter(list[Provider]).validate_json(
            (path or DEMO_PATH / "providers.json").read_text()
        )
        self._providers = {(p.provider_id, p.plan_id): p for p in providers}
        if len(self._providers) != len(providers):
            raise ValueError("Duplicate providers")

    def get_provider(self, provider_id: str, plan_id: str) -> Provider | None:
        return self._providers.get((provider_id, plan_id))

    def search(self, plan_id: str, query: ProviderSearch) -> list[Provider]:
        if query.radius_miles is not None and query.zip_code is None:
            raise ValueError("ZIP is required for radius")
        results = []
        for provider in self._providers.values():
            if provider.plan_id != plan_id:
                continue
            if query.network_status and provider.network_status != query.network_status:
                continue
            if query.specialty and provider.specialty.casefold() != query.specialty.casefold():
                continue
            if query.procedure and query.procedure not in provider.fees:
                continue
            if query.zip_code:
                if query.radius_miles is None and provider.zip_code != query.zip_code:
                    continue
                if query.radius_miles is not None:
                    distance = provider.distances_miles.get(query.zip_code)
                    if distance is None or distance > query.radius_miles:
                        continue
            results.append(provider)
        return sorted(results, key=lambda p: p.provider_id)[:10]
