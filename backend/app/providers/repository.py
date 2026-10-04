from decimal import Decimal
from math import asin, cos, radians, sin, sqrt
from pathlib import Path
from typing import Annotated, Literal, Protocol

from pydantic import Field, TypeAdapter

from app.models import StrictModel
from app.plans.repository import NetworkStatus


class ProcedureFee(StrictModel):
    provider_charge: Annotated[Decimal, Field(ge=0)]
    allowed_amount: Annotated[Decimal, Field(ge=0)] | None = None


class Provider(StrictModel):
    provider_id: Annotated[str, Field(min_length=1, max_length=64)]
    name: Annotated[str, Field(min_length=1, max_length=200)]
    specialty: Annotated[str, Field(min_length=1, max_length=100)]
    network_status: NetworkStatus
    zip_code: Annotated[str, Field(pattern=r"^\d{5}$")]
    latitude: Annotated[Decimal, Field(ge=-90, le=90)]
    longitude: Annotated[Decimal, Field(ge=-180, le=180)]
    fees: dict[str, ProcedureFee]
    source: Annotated[str, Field(min_length=1, max_length=200)]
    verification_status: Literal["synthetic", "verified", "unverified"]
    verified_at: Annotated[str, Field(min_length=1, max_length=64)] | None = None


class ProviderMatch(StrictModel):
    provider: Provider
    distance_miles: Annotated[Decimal, Field(ge=0)] | None = None


class ProviderSearch(StrictModel):
    zip_code: Annotated[str, Field(pattern=r"^\d{5}$")] | None = None
    radius_miles: Annotated[Decimal, Field(gt=0, le=100)] | None = None
    network_status: NetworkStatus | None = None
    specialty: str | None = None
    procedure_id: str | None = None


class ProviderRepository(Protocol):
    def get_provider(self, provider_id: str) -> Provider | None: ...

    def search(self, query: ProviderSearch) -> list[ProviderMatch]: ...


class _ProviderFixture(StrictModel):
    notice: Literal["SYNTHETIC DEMO DATA — NOT REAL DENTISTS"]
    providers: list[Provider]


class SyntheticProviderRepository:
    def __init__(self, data_path: Path | None = None) -> None:
        source = data_path or Path(__file__).resolve().parents[3] / "data/demo/providers.json"
        fixture = TypeAdapter(_ProviderFixture).validate_json(source.read_text(encoding="utf-8"))
        self._providers = {provider.provider_id: provider for provider in fixture.providers}
        if len(self._providers) != len(fixture.providers):
            raise ValueError("Duplicate provider IDs")

    def get_provider(self, provider_id: str) -> Provider | None:
        return self._providers.get(provider_id.upper())

    @staticmethod
    def _distance(first: Provider, second: Provider) -> Decimal:
        lat1, lon1, lat2, lon2 = map(
            radians,
            map(float, (first.latitude, first.longitude, second.latitude, second.longitude)),
        )
        delta_lat = lat2 - lat1
        delta_lon = lon2 - lon1
        value = sin(delta_lat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(delta_lon / 2) ** 2
        miles = 2 * 3958.7613 * asin(sqrt(value))
        return Decimal(str(miles)).quantize(Decimal("0.1"))

    def search(self, query: ProviderSearch) -> list[ProviderMatch]:
        origin = next(
            (
                provider
                for provider in self._providers.values()
                if provider.zip_code == query.zip_code
            ),
            None,
        )
        matches: list[ProviderMatch] = []
        for provider in self._providers.values():
            if query.network_status and provider.network_status != query.network_status:
                continue
            if query.specialty and provider.specialty != query.specialty.lower():
                continue
            if query.procedure_id and query.procedure_id.upper() not in provider.fees:
                continue
            distance = self._distance(origin, provider) if origin else None
            if query.zip_code and origin is None and provider.zip_code != query.zip_code:
                continue
            if query.radius_miles is not None and (
                distance is None or distance > query.radius_miles
            ):
                continue
            matches.append(ProviderMatch(provider=provider, distance_miles=distance))
        # Fixture order is stable; this is filtering, not "best provider" optimization.
        return matches
