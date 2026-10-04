from datetime import date
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol

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
    verified_on: ShortText | None = None
    accepting_new_patients: bool | None = None
    available_dates: list[date] | None = None
    network_scope: Literal["plan", "dataset_global"] = "plan"
    city: ShortText | None = None
    state: Annotated[str, Field(pattern=r"^[A-Z]{2}$")] | None = None
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


class DynamoDBProviderRepository:
    """Read-only existing directory; absent fees/geocoding/availability stay absent."""

    def __init__(self, table_name: str, region: str = "us-east-1", *, table: Any = None) -> None:
        from app.aws_data import dynamodb_table

        self._table = dynamodb_table(table_name, region, table)
        self._name = table_name

    def _parse(self, item: dict[str, Any], plan_id: str) -> Provider | None:
        from app.aws_data import normalize_numbers

        item = normalize_numbers(item)
        assigned_plan = item.get("plan_id")
        plan_ids = item.get("plan_ids")
        if assigned_plan is not None and assigned_plan != plan_id:
            return None
        if plan_ids is not None:
            if not isinstance(plan_ids, list) or any(not isinstance(p, str) for p in plan_ids):
                raise ValueError("Malformed provider plan IDs")
            if plan_id not in plan_ids:
                return None
        fields = {key: value for key, value in item.items() if key in Provider.model_fields}
        fields.update(
            plan_id=plan_id,
            zip_code=item.get("zip_code", item.get("zip")),
            distances_miles=item.get("distances_miles", {}),
            fees=item.get("fees", {}),
            source=f"DynamoDB {self._name}/{item['provider_id']}",
            verification_status="synthetic",
            synthetic=item.get("is_synthetic", item.get("synthetic")),
            network_scope="plan"
            if assigned_plan is not None or plan_ids is not None
            else "dataset_global",
        )
        if isinstance(fields.get("available_dates"), list):
            fields["available_dates"] = [
                date.fromisoformat(v) if isinstance(v, str) else v
                for v in fields["available_dates"]
            ]
        return Provider.model_validate(fields)

    def get_provider(self, provider_id: str, plan_id: str) -> Provider | None:
        item = self._table.get_item(Key={"provider_id": provider_id}, ConsistentRead=True).get(
            "Item"
        )
        if item is None:
            return None
        if item.get("provider_id") != provider_id:
            raise ValueError("Mismatched provider ID")
        return self._parse(item, plan_id)

    def search(self, plan_id: str, query: ProviderSearch) -> list[Provider]:
        from app.aws_data import read_all

        if query.radius_miles is not None and query.zip_code is None:
            raise ValueError("ZIP is required for radius")
        results = []
        for item in read_all(self._table, ConsistentRead=True):
            provider = self._parse(item, plan_id)
            if provider is None:
                continue
            if query.network_status and provider.network_status != query.network_status:
                continue
            if query.specialty and provider.specialty.casefold() != query.specialty.casefold():
                continue
            # No fee does not imply a provider cannot perform a procedure. With an explicit
            # fee catalog, procedure filtering can use it; otherwise specialty is available.
            if query.procedure and provider.fees and query.procedure not in provider.fees:
                continue
            if query.zip_code:
                if query.radius_miles is None and provider.zip_code != query.zip_code:
                    continue
                if query.radius_miles is not None:
                    distance = provider.distances_miles.get(query.zip_code)
                    if distance is None or distance > query.radius_miles:
                        continue
            results.append(provider)
        if len({p.provider_id for p in results}) != len(results):
            raise ValueError("Duplicate provider IDs")
        return sorted(results, key=lambda p: p.provider_id)[:10]
