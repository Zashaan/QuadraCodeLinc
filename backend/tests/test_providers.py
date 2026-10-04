from decimal import Decimal

from app.providers.repository import ProviderSearch, SyntheticProviderRepository


def test_provider_filters_network_location_specialty_and_procedure_without_ranking() -> None:
    repository = SyntheticProviderRepository()
    in_network = repository.search(ProviderSearch(network_status="in_network"))
    assert [match.provider.provider_id for match in in_network] == ["SYNTH001", "SYNTH003"]
    nearby = repository.search(
        ProviderSearch(zip_code="19103", radius_miles=Decimal("2"), procedure_id="D2740")
    )
    assert [match.provider.provider_id for match in nearby] == ["SYNTH001", "SYNTH002"]
    specialist = repository.search(ProviderSearch(specialty="endodontics", procedure_id="D3310"))
    assert [match.provider.provider_id for match in specialist] == ["SYNTH003"]
    assert all("Synthetic" in match.provider.name for match in specialist)
