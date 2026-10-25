"""Deterministic completion. Never imports the model client or executes a task."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError

import install
from interface import Closed, Completion, Interface, Option, Text

ROOT = Path(__file__).resolve().parent


class Request(Closed):
    command: str
    args: list[str]
    cursor: int = Field(ge=0)
    cwd: str


class Candidate(Closed):
    value: Text
    description: str = ""
    kind: Literal["value", "file", "directory"] = "value"


def read_interface(name: str) -> Interface | None:
    if not install.valid_name(name):
        return None
    path = ROOT / "interface" / f"{name}.json"
    if not path.is_file() or path.is_symlink():
        return None
    interface = Interface.model_validate_json(path.read_bytes())
    if interface.name != name:
        raise ValueError("interface name differs from filename")
    return interface


def commands(*, installed: bool = False) -> list[str]:
    return (
        [
            path.name
            for path in sorted((ROOT / "tasks").iterdir())
            if install.valid_name(path.name)
            and path.is_file()
            and not path.is_symlink()
            and (not installed or install.command_link(path.name).state == install.LinkState.OWNED)
        ]
        if (ROOT / "tasks").is_dir()
        else []
    )


def values(completion: Completion, prefix: str, cwd: str) -> list[Candidate]:
    match completion.kind:
        case "none":
            return []
        case "choices":
            return [
                Candidate(value=value) for value in completion.values if value.startswith(prefix)
            ]
        case "file" | "directory":
            if prefix == "~":
                return [Candidate(value="~/", kind="directory")]
            expanded = Path(os.path.expanduser(prefix))
            base = expanded if prefix.endswith("/") else expanded.parent
            partial = "" if prefix.endswith("/") else expanded.name if prefix else ""
            base = base if base.is_absolute() else Path(cwd) / base
            head = prefix[: len(prefix) - len(partial)] if partial else prefix
            result: list[Candidate] = []
            for path in sorted(base.iterdir()):
                if not path.name.startswith(partial) or (
                    path.name.startswith(".") and not partial.startswith(".")
                ):
                    continue
                directory = path.is_dir()
                if completion.kind == "directory" and not directory:
                    continue
                insertion = head + path.name + ("/" if directory else "")
                if clean(insertion):
                    result.append(
                        Candidate(value=insertion, kind="directory" if directory else "file")
                    )
            return result


def clean(value: str) -> bool:
    return (
        bool(value)
        and len(value) <= 600
        and all(ord(char) >= 32 and not 127 <= ord(char) <= 159 for char in value)
    )


def resolve(request: Request) -> list[Candidate]:
    if request.cursor > len(request.args):
        raise ValueError(
            "cursor is a zero-based token index, including an optional trailing empty token"
        )
    prefix = request.args[request.cursor] if request.cursor < len(request.args) else ""
    before = request.args[: request.cursor]
    if request.command == "ayi":
        if not before:
            return [
                Candidate(value=name)
                for name in (
                    "new",
                    "modify",
                    "explain",
                    "sessions",
                    "resume",
                    "fork",
                    "review",
                    "list",
                    "install",
                    "update",
                    "shell",
                    "--help",
                    "--version",
                    "-V",
                )
                if name.startswith(prefix)
            ]
        if before[0] in ("new", "modify") and "--" not in before and prefix.startswith("-"):
            return [
                Candidate(value=name)
                for name in (
                    "--force",
                    "--design-model",
                    "--implementation-harness",
                    "--implementation-model",
                )
                if name.startswith(prefix) and name not in before
            ]
        if (
            before[0] in ("new", "modify")
            and "--" not in before
            and before[-1] == "--implementation-harness"
        ):
            return [Candidate(value=name) for name in ("pi", "codex") if name.startswith(prefix)]
        if before[0] in ("fork", "review") and prefix.startswith("-"):
            return (
                [Candidate(value="--checkpoint")]
                if "--checkpoint".startswith(prefix) and "--checkpoint" not in before
                else []
            )
        if before[0] in ("modify", "explain"):
            value_options = ("--design-model", "--implementation-harness", "--implementation-model")
            if before[-1] in value_options:
                return []
            tokens = iter(before[1:])
            factory_positionals: list[str] = []
            for word in tokens:
                if word in value_options:
                    _ = next(tokens, None)
                elif word != "--force" and word.partition("=")[0] not in value_options:
                    factory_positionals.append(word)
            if not factory_positionals:
                return [Candidate(value=name) for name in commands() if name.startswith(prefix)]
        choices = {"install": ("plan", "apply", "uninstall"), "shell": ("init",)}
        if len(before) == 1:
            return [
                Candidate(value=name)
                for name in choices.get(before[0], ())
                if name.startswith(prefix)
            ]
        if before == ["shell", "init"] and "zsh".startswith(prefix):
            return [Candidate(value="zsh")]
        return []
    interface = read_interface(request.command)
    if interface is None:
        return []
    options = {
        name: option
        for option in interface.options
        for name in option.names + (option.negative_names if interface.runtime == "python" else [])
    }
    pending: Option | None = None
    used: set[str] = set()
    positional = 0
    literal = False
    for word in before:
        if pending is not None:
            pending = None
        elif word == "--" and not literal:
            literal = True
        elif not literal and word.split("=", 1)[0] in options:
            option = options[word.split("=", 1)[0]]
            used.update(
                option.names + (option.negative_names if interface.runtime == "python" else [])
                if not option.repeatable
                else []
            )
            if option.value is not None and "=" not in word:
                pending = option
        elif literal or not word.startswith("-"):
            positional += 1
    if pending is not None:
        return values(pending.value.completion, prefix, request.cwd) if pending.value else []
    if not literal and "=" in prefix:
        name, fragment = prefix.split("=", 1)
        option = options.get(name)
        if option and option.value:
            return [
                candidate.model_copy(update={"value": name + "=" + candidate.value})
                for candidate in values(option.value.completion, fragment, request.cwd)
            ]
        return []
    result: list[Candidate] = []
    if not literal:
        if interface.runtime == "python":
            result.extend(
                Candidate(value=name, description=description)
                for name, description in (
                    ("--json", "Emit JSON"),
                    ("--no-input", "Disable prompts"),
                )
                if name.startswith(prefix) and name not in before
            )
        result.extend(
            Candidate(value=name, description=option.description)
            for name, option in options.items()
            if name.startswith(prefix) and name not in used
        )
        result.extend(
            Candidate(value=name, description="Show help")
            for name in ("--help", "-h")
            if name not in options and name.startswith(prefix)
        )
    if literal or not prefix.startswith("-"):
        if positional < len(interface.positionals):
            result.extend(values(interface.positionals[positional].completion, prefix, request.cwd))
        elif interface.positionals and interface.positionals[-1].repeatable:
            result.extend(values(interface.positionals[-1].completion, prefix, request.cwd))
    return result


def main(arguments: list[str]) -> int:
    try:
        if arguments == ["--commands"]:
            print("\n".join(commands(installed=True)))
            return 0
        if arguments[:1] == ["--zsh"] and len(arguments) >= 3:
            request = Request(
                command=arguments[1], cursor=int(arguments[2]), cwd=os.getcwd(), args=arguments[3:]
            )
        elif not arguments:
            request = Request.model_validate_json(sys.stdin.read())
        else:
            raise ValueError("usage: ayi complete < request.json")
        candidates = resolve(request)
        if arguments[:1] == ["--zsh"]:
            for candidate in candidates:
                print(f"{candidate.kind}\t{candidate.value}\t{candidate.description}")
        else:
            print(json.dumps([candidate.model_dump() for candidate in candidates]))
        return 0
    except (OSError, ValueError, ValidationError) as error:
        print(f"ayi: completion unavailable: {error}", file=sys.stderr)
        return 65


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
