"""Small fail-closed configuration boundary; never include secrets in repr or logs."""

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    internal_token: str = field(repr=False)

    def __post_init__(self) -> None:
        if (
            len(self.internal_token) < 32
            or not self.internal_token.isascii()
            or any(character.isspace() for character in self.internal_token)
        ):
            raise ValueError(
                "ABE_INTERNAL_TOKEN requires at least 32 non-whitespace ASCII characters"
            )

    @classmethod
    def from_environment(cls) -> "Settings":
        return cls(internal_token=os.environ.get("ABE_INTERNAL_TOKEN", ""))
