"""Procedure catalog metadata, never substituted for provider quotes."""

from pathlib import Path
from typing import Any, Protocol

from pydantic import TypeAdapter

from app.aws_data import dynamodb_table, normalize_numbers, read_all
from app.benefits.models import Money, ShortText
from app.models import StrictModel


class Procedure(StrictModel):
    procedure_code: ShortText
    procedure_name: ShortText
    category: ShortText
    specialty: ShortText
    typical_price: Money | None = None


class ProcedureRepository(Protocol):
    def get_procedure(self, procedure_code: str) -> Procedure | None: ...
    def list_procedures(self) -> list[Procedure]: ...


class LocalProcedureRepository:
    def __init__(self, path: Path) -> None:
        values = TypeAdapter(list[Procedure]).validate_json(path.read_text())
        self._items = {item.procedure_code: item for item in values}
        if len(self._items) != len(values):
            raise ValueError("Duplicate procedure IDs")

    def get_procedure(self, procedure_code: str) -> Procedure | None:
        return self._items.get(procedure_code)

    def list_procedures(self) -> list[Procedure]:
        return sorted(self._items.values(), key=lambda item: item.procedure_code)


class DynamoDBProcedureRepository:
    def __init__(self, table_name: str, region: str = "us-east-1", *, table: Any = None) -> None:
        self._table = dynamodb_table(table_name, region, table)

    def get_procedure(self, procedure_code: str) -> Procedure | None:
        item = self._table.get_item(
            Key={"procedure_code": procedure_code}, ConsistentRead=True
        ).get("Item")
        if item is None:
            return None
        procedure = Procedure.model_validate(normalize_numbers(item))
        if procedure.procedure_code != procedure_code:
            raise ValueError("Mismatched procedure")
        return procedure

    def list_procedures(self) -> list[Procedure]:
        values = [
            Procedure.model_validate(item) for item in read_all(self._table, ConsistentRead=True)
        ]
        if len({p.procedure_code for p in values}) != len(values):
            raise ValueError("Duplicate procedure IDs")
        return sorted(values, key=lambda item: item.procedure_code)
