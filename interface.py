"""Validated public command contracts, independent of executable implementation."""

from __future__ import annotations

import argparse
from typing import Annotated, Literal, NoReturn, override

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


class Provider(Closed):
    kind: Literal["provider"]
    id: Literal["git.branches"]


type Completion = Annotated[
    NoCompletion | Choices | Filesystem | Provider, Field(discriminator="kind")
]


class Value(Closed):
    name: Text
    completion: Completion
    type: Literal["text", "integer"] = "text"

    @model_validator(mode="after")
    def integer_choices(self) -> Value:
        if self.type == "integer" and isinstance(self.completion, Choices):
            for value in self.completion.values:
                _ = int(value)
        return self


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
    schema_version: Literal[1, 2] = Field(alias="schemaVersion")
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
        match self.schema_version:
            case 1:
                values = [*self.positionals, *(o.value for o in self.options if o.value)]
                if any(isinstance(value.completion, Provider) for value in values):
                    raise ValueError("provider completion requires schemaVersion 2")
            case 2:
                pass
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


class UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    @override
    def error(self, message: str) -> NoReturn:
        raise UsageError(message)


def parser(interface: Interface, *, cursor: str | None = None) -> argparse.ArgumentParser:
    """One grammar for execution and incomplete command lines.

    A completion cursor is an internal operand, not a task invocation. Required
    operands may still be missing, and finite choices accept the cursor marker.
    All option spellings, actions, arities and token interpretation stay shared.
    """
    result = _Parser(
        prog=interface.name,
        description=interface.summary,
        allow_abbrev=False,
        add_help=False,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n  "
        + "\n  ".join(interface.examples)
        + f"\n\nSettings: $XDG_CONFIG_HOME/ayi-tools/{interface.name}.toml"
        "\nPriority: arguments > declared environment > [defaults] settings > manifest defaults."
        "\nJSON: one success value on stdout; one error value on stderr."
        "\nExit: 0 success, 1 declined, 64 usage, 65 config, 69 unavailable, 74 I/O, 130 interrupted.",
    )
    declared = {name for option in interface.options for name in option.names}
    help_names = [name for name in ("-h", "--help") if name not in declared]
    if help_names:
        _ = result.add_argument(
            *help_names, action="help" if cursor is None else "store_true", help="Show help"
        )
    if interface.runtime == "python":
        for name, description in (
            ("--json", "Emit a stable JSON result; never prompt"),
            ("--no-input", "Reject missing inputs without a prompt"),
        ):
            _ = result.add_argument(
                name,
                action="store_true",
                default=False if cursor is None else argparse.SUPPRESS,
                help=description,
            )
    for option in interface.options:
        description = option.description
        if option.default is not None:
            description += f" (default: {option.default})"
        if option.env:
            description += f" (env: {option.env})"
        if option.required:
            description += " (required)"
        if option.value is None:
            _ = result.add_argument(
                *option.names,
                dest=option.key,
                help=description,
                default=argparse.SUPPRESS,
                action="count"
                if option.repeatable
                else argparse.BooleanOptionalAction
                if interface.runtime == "python"
                else "store_true",
            )
        else:
            choices = option.value.completion
            _ = result.add_argument(
                *option.names,
                dest=option.key,
                help=description,
                default=argparse.SUPPRESS,
                metavar=option.value.name if not isinstance(choices, Choices) else None,
                choices=([*choices.values, cursor] if cursor is not None else choices.values)
                if isinstance(choices, Choices)
                else None,
                action="append" if option.repeatable else "store",
            )
    for positional in interface.positionals:
        choices = positional.completion
        _ = result.add_argument(
            positional.name,
            nargs="+"
            if positional.repeatable and positional.required and cursor is None
            else "*"
            if positional.repeatable
            else None
            if positional.required and cursor is None
            else "?",
            default=argparse.SUPPRESS,
            choices=([*choices.values, cursor] if cursor is not None else choices.values)
            if isinstance(choices, Choices)
            else None,
        )
    return result
