from pydantic import JsonValue

from app.models import StrictModel


class ToolDefinition(StrictModel):
    name: str
    description: str
    input_schema: dict[str, JsonValue]


class Tool[Arguments: StrictModel, Result: StrictModel]:
    """Validates inputs and outputs at the tool boundary."""

    name: str
    description: str
    arguments_type: type[Arguments]
    result_type: type[Result]

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            input_schema=self.arguments_type.model_json_schema(),
        )

    def invoke(self, arguments: dict[str, JsonValue]) -> Result:
        validated = self.arguments_type.model_validate(arguments)
        result = self.execute(validated)
        return self.result_type.model_validate(result.model_dump())

    def execute(self, arguments: Arguments) -> Result:
        raise NotImplementedError
