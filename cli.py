"""Shared command surface. Domain handlers receive resolved, immutable inputs."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum, StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import NoReturn, TypeGuard, override

from pydantic import JsonValue, ValidationError

import ui
from interface import Choices, Interface, Option, Value


class Code(StrEnum):
    USAGE = "usage"
    CONFIG = "config"
    DECLINED = "declined"
    UNAVAILABLE = "unavailable"
    IO = "io"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True)
class Failure:
    code: Code
    message: str

    @property
    def status(self) -> int:
        return {
            Code.USAGE: 64,
            Code.CONFIG: 65,
            Code.DECLINED: 1,
            Code.UNAVAILABLE: 69,
            Code.IO: 74,
            Code.INTERRUPTED: 130,
        }[self.code]


@dataclass(frozen=True)
class Result:
    data: JsonValue
    text: str


class Missing(Enum):
    VALUE = "missing"


type Input = str | int | bool | tuple[str | int, ...] | Missing


@dataclass(frozen=True)
class Invocation:
    values: Mapping[str, Input]
    sources: Mapping[str, str]
    interactive: bool

    def text(self, name: str) -> str:
        value = self.values[name]
        if isinstance(value, Missing) or not isinstance(value, str):
            raise TypeError(f"{name} is not a text input")
        return value

    def optional_text(self, name: str) -> str | Missing:
        value = self.values[name]
        return value if isinstance(value, Missing) else self.text(name)

    def optional_integer(self, name: str) -> int | Missing:
        value = self.values[name]
        return value if isinstance(value, Missing) else self.integer(name)

    def integer(self, name: str) -> int:
        value = self.values[name]
        if type(value) is not int:
            raise TypeError(f"{name} is not an integer input")
        return value

    def flag(self, name: str) -> bool:
        value = self.values[name]
        if not isinstance(value, bool):
            raise TypeError(f"{name} is not a flag")
        return value

    def many(self, name: str) -> tuple[str | int, ...]:
        value = self.values[name]
        if not isinstance(value, tuple):
            raise TypeError(f"{name} is not a repeated input")
        return value


class _Usage(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    @override
    def error(self, message: str) -> NoReturn:
        # argparse exposes expected parse failures through this adapter.
        raise _Usage(message)


def parser(interface: Interface) -> argparse.ArgumentParser:
    result = _Parser(
        prog=interface.name,
        description=interface.summary,
        allow_abbrev=False,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n  "
        + "\n  ".join(interface.examples)
        + f"\n\nSettings: $XDG_CONFIG_HOME/ayi-tools/{interface.name}.toml"
        "\nPriority: arguments > declared environment > [defaults] settings > manifest defaults."
        "\nJSON: one success value on stdout; one error value on stderr."
        "\nExit: 0 success, 1 declined, 64 usage, 65 config, 69 unavailable, 74 I/O, 130 interrupted.",
    )
    _ = result.add_argument(
        "--json", action="store_true", help="Emit a stable JSON result; never prompt"
    )
    _ = result.add_argument(
        "--no-input", action="store_true", help="Reject missing inputs without a prompt"
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
                action="count" if option.repeatable else argparse.BooleanOptionalAction,
            )
        else:
            choices = option.value.completion
            _ = result.add_argument(
                *option.names,
                dest=option.key,
                help=description,
                default=argparse.SUPPRESS,
                metavar=option.value.name if not isinstance(choices, Choices) else None,
                choices=choices.values if isinstance(choices, Choices) else None,
                action="append" if option.repeatable else "store",
            )
    for positional in interface.positionals:
        choices = positional.completion
        _ = result.add_argument(
            positional.name,
            nargs="+"
            if positional.repeatable and positional.required
            else "*"
            if positional.repeatable
            else None
            if positional.required
            else "?",
            default=argparse.SUPPRESS,
            choices=choices.values if isinstance(choices, Choices) else None,
        )
    return result


def _objects(value: object) -> TypeGuard[list[object]]:
    return isinstance(value, list)


def _table(value: object) -> TypeGuard[dict[object, object]]:
    return isinstance(value, dict)


def _strings(value: object) -> TypeGuard[list[str]]:
    return _objects(value) and all(isinstance(item, str) for item in value)


def _value(value: object, spec: Value, key: str, code: Code) -> str | int | Failure:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return Failure(code, f"{key}: expected {spec.type}")
    text = str(value)
    if isinstance(spec.completion, Choices) and text not in spec.completion.values:
        return Failure(code, f"{key}: choose from {', '.join(spec.completion.values)}")
    if spec.type == "integer":
        try:
            return int(text)
        except ValueError:
            return Failure(code, f"{key}: expected an integer")
    if not isinstance(value, str):
        return Failure(code, f"{key}: expected text")
    return text


def _option(value: object, option: Option, code: Code) -> Input | Failure:
    if option.repeatable:
        if option.value is None and type(value) is int:
            return value
        if option.value is not None and _strings(value):
            items: list[str | int] = []
            for item in value:
                parsed = _value(item, option.value, option.key, code)
                if isinstance(parsed, Failure):
                    return parsed
                items.append(parsed)
            return tuple(items)
        return Failure(code, f"{option.key}: repeated values must come from arguments")
    if option.value is None:
        if isinstance(value, bool):
            return value
        if value in ("true", "false"):
            return value == "true"
        return Failure(code, f"{option.key}: expected true or false")
    return _value(value, option.value, option.key, code)


def settings(interface: Interface, env: Mapping[str, str]) -> dict[str, Input] | Failure:
    config = env.get("XDG_CONFIG_HOME")
    home = env.get("HOME")
    if config:
        root = Path(config)
    elif home:
        root = Path(home) / ".config"
    else:
        return Failure(Code.CONFIG, "Set HOME or an absolute XDG_CONFIG_HOME")
    if not root.is_absolute():
        return Failure(Code.CONFIG, "XDG_CONFIG_HOME and HOME must be absolute paths")
    path = root / "ayi-tools" / f"{interface.name}.toml"
    try:
        document: dict[str, object] = tomllib.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        return Failure(Code.CONFIG, f"Cannot read settings: {path}")
    defaults = document.get("defaults", {})
    if set(document) - {"defaults"} or not _table(defaults):
        return Failure(Code.CONFIG, "Settings accept only a [defaults] table")
    result: dict[str, Input] = {}
    allowed = {option.key: option for option in interface.options if not option.repeatable}
    for key, value in defaults.items():
        if not isinstance(key, str) or key not in allowed:
            return Failure(Code.CONFIG, "Unknown or repeated option in settings")
        parsed = _option(value, allowed[key], Code.CONFIG)
        if isinstance(parsed, Failure):
            return parsed
        result[key] = parsed
    return result


def resolve(
    interface: Interface,
    arguments: list[str],
    env: Mapping[str, str],
    *,
    can_prompt: bool,
    entry: Callable[[str, tuple[str, ...]], str] = ui.entry,
) -> Invocation | Failure:
    try:
        namespace = parser(interface).parse_args(arguments)
    except _Usage as error:
        return Failure(Code.USAGE, str(error))
    raw: dict[str, object] = vars(namespace)
    interactive = can_prompt and not raw["json"] and not raw["no_input"]
    defaults = settings(interface, env)
    if isinstance(defaults, Failure):
        return defaults
    values: dict[str, Input] = {}
    sources: dict[str, str] = {}
    for option in interface.options:
        key = option.key
        if key in raw:
            value, source = raw[key], "argument"
        elif option.env and option.env in env:
            value, source = env[option.env], "environment"
        elif key in defaults:
            values[key], sources[key] = defaults[key], "config"
            continue
        else:
            value, source = option.default, "default"
        if value is None and option.prompt and interactive:
            choices = option.value.completion if option.value else None
            value = entry(
                option.prompt, tuple(choices.values) if isinstance(choices, Choices) else ()
            )
            source = "prompt"
        if value is None:
            if option.required:
                return Failure(
                    Code.USAGE, f"Missing {option.names[0]}; see {interface.name} --help"
                )
            values[key] = (
                ()
                if option.repeatable and option.value
                else 0
                if option.repeatable
                else False
                if option.value is None
                else Missing.VALUE
            )
        else:
            parsed = _option(
                value, option, Code.CONFIG if source in ("environment", "config") else Code.USAGE
            )
            if isinstance(parsed, Failure):
                return parsed
            values[key] = parsed
        sources[key] = source
    for positional in interface.positionals:
        value = raw.get(positional.name)
        if value is None:
            values[positional.name] = () if positional.repeatable else Missing.VALUE
        elif _strings(value):
            items: list[str | int] = []
            for item in value:
                parsed = _value(item, positional, positional.name, Code.USAGE)
                if isinstance(parsed, Failure):
                    return parsed
                items.append(parsed)
            values[positional.name] = tuple(items)
        else:
            parsed = _value(value, positional, positional.name, Code.USAGE)
            if isinstance(parsed, Failure):
                return parsed
            values[positional.name] = parsed
        sources[positional.name] = "argument" if value is not None else "default"
    return Invocation(MappingProxyType(values), MappingProxyType(sources), interactive)


def pick(invocation: Invocation, label: str, choices: Mapping[str, str]) -> str | Failure:
    """Select a stable ID. Labels never become executable input."""
    if not choices:
        return Failure(Code.USAGE, "No choices available")
    if not invocation.interactive:
        return Failure(
            Code.USAGE, "Selection needs an explicit argument outside an interactive terminal"
        )
    rows = {f"{key}: {label}": key for key, label in choices.items()}
    try:
        answer = ui.entry(label, tuple(rows))
    except (EOFError, KeyboardInterrupt):
        return Failure(Code.INTERRUPTED, "Selection cancelled")
    if answer not in rows:
        return Failure(Code.USAGE, "Select one of the listed choices")
    return rows[answer]


@dataclass(frozen=True)
class Approved:
    pass


def confirm(invocation: Invocation, question: str) -> Approved | Failure:
    if not invocation.interactive:
        return Failure(
            Code.DECLINED,
            "Approval needs an interactive terminal or an explicit domain apply command",
        )
    try:
        return (
            Approved()
            if ui.entry(f"{question} [y/N]").lower() == "y"
            else Failure(Code.DECLINED, "Declined")
        )
    except (EOFError, KeyboardInterrupt):
        return Failure(Code.INTERRUPTED, "Approval cancelled")


def ask_pi(prompt: str, *, model: str | None = None, timeout: float = 120) -> Result | Failure:
    """Explicit model effect. No tool execution, persisted session, or provider diagnostics."""
    if not prompt.strip() or timeout <= 0:
        return Failure(Code.USAGE, "A model request needs text and a positive timeout")
    try:
        result = subprocess.run(
            [
                "pi",
                "--print",
                "--no-session",
                "--no-tools",
                "--no-extensions",
                "--no-mcp",
                "--no-skills",
                "--no-context-files",
                "--no-prompt-templates",
                "--offline",
                *(["--model", model] if model else []),
            ],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (FileNotFoundError, PermissionError):
        return Failure(
            Code.UNAVAILABLE, "Pi is unavailable; examine its installation and authentication"
        )
    except subprocess.TimeoutExpired:
        return Failure(Code.UNAVAILABLE, "Pi exceeded the request timeout")
    except KeyboardInterrupt:
        return Failure(Code.INTERRUPTED, "Model request interrupted")
    except OSError:
        return Failure(Code.IO, "Cannot start Pi")
    if result.returncode or not result.stdout.strip():
        return Failure(Code.UNAVAILABLE, f"Pi returned no usable result (exit {result.returncode})")
    return Result({"text": result.stdout.strip()}, result.stdout.strip())


def emit(outcome: Result | Failure, *, json_output: bool) -> int:
    if isinstance(outcome, Failure):
        print(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "ok": False,
                    "error": {"code": outcome.code.value, "message": outcome.message},
                }
            )
            if json_output
            else outcome.message,
            file=sys.stderr,
        )
        return outcome.status
    print(
        json.dumps({"schemaVersion": 1, "ok": True, "data": outcome.data}, allow_nan=False)
        if json_output
        else outcome.text
    )
    _ = sys.stdout.flush()
    return 0


def run(handler: Callable[[Invocation], Result | Failure]) -> int:
    arguments = sys.argv[1:]
    json_output = (
        "--json" in arguments[: arguments.index("--")]
        if "--" in arguments
        else "--json" in arguments
    )
    try:
        task = Path(sys.argv[0]).resolve()
        path = Path(
            os.environ.get(
                "AYI_INTERFACE_PATH", str(task.parent.parent / "interface" / f"{task.name}.json")
            )
        )
        interface = Interface.model_validate_json(path.read_bytes())
        if interface.runtime != "python":
            return emit(
                Failure(Code.CONFIG, "This tool needs runtime=python in its interface"),
                json_output=json_output,
            )
        invocation = resolve(interface, arguments, os.environ, can_prompt=ui.can_prompt())
        outcome = invocation if isinstance(invocation, Failure) else handler(invocation)
        return emit(outcome, json_output=json_output)
    except (EOFError, KeyboardInterrupt):
        return emit(Failure(Code.INTERRUPTED, "Cancelled"), json_output=json_output)
    except (ValidationError, UnicodeError):
        return emit(Failure(Code.CONFIG, "Invalid command interface"), json_output=json_output)
    except BrokenPipeError:
        # A consumer stopped reading. Avoid another flush failure at interpreter exit.
        sys.stdout = open(os.devnull, "w")
        return 0
    except OSError:
        return emit(Failure(Code.IO, "Local file operation failed"), json_output=json_output)


HEADER = '''#!/bin/sh
""":"
AYI_RUNTIME_ROOT=${AYI_RUNTIME_ROOT:-$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)}
export PYTHONPATH="$AYI_RUNTIME_ROOT"
exec "$AYI_RUNTIME_ROOT/.venv/bin/python" -B "$0" "$@"
":"""
'''


def scaffold(interface: Interface, body: str) -> str:
    """Assemble the owned launcher before draft review; never run model code here."""
    return (
        HEADER
        + f"#MISE description={json.dumps(interface.summary, ensure_ascii=False)}\n"
        + body
        + (
            '\n\nif __name__ == "__main__":\n'
            "    from cli import run as _ayi_run\n"
            "    raise SystemExit(_ayi_run(handle))\n"
        )
    )
