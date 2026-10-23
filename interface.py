"""Validated public command contracts, independent of executable implementation."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

type Text = Annotated[
    str, StringConstraints(min_length=1, max_length=600, pattern=r"^[^\x00-\x1f\x7f-\x9f]+$")
]
type Name = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{0,47}$")]
type OptionName = Annotated[str, StringConstraints(pattern=r"^(?:--[a-z][a-z0-9-]*|-[a-zA-Z0-9])$")]


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, defer_build=True)


class NoCompletion(Closed):
    kind: Literal["none"]


class Choices(Closed):
    kind: Literal["choices"]
    values: Annotated[list[Text], Field(min_length=1, max_length=1000)]


class Filesystem(Closed):
    kind: Literal["file", "directory"]


type Completion = Annotated[NoCompletion | Choices | Filesystem, Field(discriminator="kind")]


class Value(Closed):
    name: Text
    completion: Completion


class Positional(Value):
    required: bool = False
    repeatable: bool = False


class Option(Closed):
    names: Annotated[list[OptionName], Field(min_length=1)]
    description: Text
    value: Value | None = None
    repeatable: bool = False


class Interface(Closed):
    schema_version: Literal[1] = Field(alias="schemaVersion")
    name: Name
    summary: Text
    positionals: list[Positional] = Field(default_factory=list[Positional])
    options: list[Option] = Field(default_factory=list[Option])
    examples: list[Text] = Field(default_factory=list[Text])
    output: list[Literal["text", "json", "jsonl", "native"]] = Field(
        default_factory=lambda: ["text"]
    )

    @model_validator(mode="after")
    def unambiguous(self) -> Interface:
        names = [name for option in self.options for name in option.names]
        if self.name in ("ayi", "ayi-tool") or len(names) != len(set(names)):
            raise ValueError("reserved command name or duplicate option spelling")
        optional = False
        for index, positional in enumerate(self.positionals):
            if positional.repeatable and index != len(self.positionals) - 1:
                raise ValueError("only the final positional may repeat")
            if optional and positional.required:
                raise ValueError("required positional cannot follow an optional positional")
            optional = not positional.required
        if len({value.name for value in self.positionals}) != len(self.positionals):
            raise ValueError("duplicate positional name")
        return self
