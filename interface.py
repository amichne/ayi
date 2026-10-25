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
    type: Literal["text", "integer"] = "text"


class Positional(Value):
    required: bool = False
    repeatable: bool = False


class Option(Closed):
    names: Annotated[list[OptionName], Field(min_length=1)]
    description: Text
    value: Value | None = None
    repeatable: bool = False
    required: bool = False
    default: Text | bool | None = None
    env: Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]*$")] | None = None
    prompt: Text | None = None

    @property
    def key(self) -> str:
        return next((name[2:] for name in self.names if name.startswith("--")), self.names[0][1:])

    @property
    def negative_names(self) -> list[str]:
        return (
            ["--no-" + name[2:] for name in self.names if name.startswith("--")]
            if self.value is None and not self.repeatable
            else []
        )

    @model_validator(mode="after")
    def input_contract(self) -> Option:
        if self.repeatable and (
            self.default is not None or self.env or self.prompt or self.required
        ):
            raise ValueError(
                "repeatable options cannot declare defaults, env, prompts, or required"
            )

        if self.value is None:
            if self.required or self.prompt or isinstance(self.default, str):
                raise ValueError("flags accept only Boolean defaults and no prompt or required")
        elif isinstance(self.default, bool):
            raise ValueError("value options need text defaults")
        elif self.default is not None:
            if self.value.type == "integer":
                _ = int(self.default)
            if (
                isinstance(self.value.completion, Choices)
                and self.default not in self.value.completion.values
            ):
                raise ValueError("default must match a declared choice")
        return self


class Interface(Closed):
    schema_version: Literal[1] = Field(alias="schemaVersion")
    name: Name
    summary: Text
    runtime: Literal["python"] | None = None
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
        if self.runtime == "python":
            names += [name for option in self.options for name in option.negative_names]
            if len(names) != len(set(names)):
                raise ValueError("duplicate generated negative option spelling")
            if set(names) & {"--help", "-h", "--json", "--no-input"}:
                raise ValueError("reserved shared option spelling")
            keys = [option.key for option in self.options] + [p.name for p in self.positionals]
            if len(keys) != len(set(keys)) or set(keys) & {"json", "no_input", "help"}:
                raise ValueError("duplicate or reserved input key")
            if self.output != ["text", "json"]:
                raise ValueError("shared runtime output must be text and json")
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
