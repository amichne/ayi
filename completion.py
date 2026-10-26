"""Deterministic completion. Never imports the model client or executes a task."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError, model_validator

import install
from interface import Closed, Completion, Interface, Text, UsageError, parser

ROOT = Path(__file__).resolve().parent
MAX_CANDIDATES = 1000
PROVIDER_TIMEOUT = 0.15


class Request(Closed):
    command: str
    args: list[str] = Field(max_length=1024)
    cursor: int = Field(ge=0)
    offset: int | None = Field(default=None, ge=0)
    cwd: str

    @model_validator(mode="after")
    def cursor_bounds(self) -> Request:
        if self.cursor > len(self.args):
            raise ValueError("cursor must identify a token or the trailing empty token")
        token = self.args[self.cursor] if self.cursor < len(self.args) else ""
        if self.offset is not None and self.offset > len(token):
            raise ValueError("offset must be within the current token")
        return self


class Candidate(Closed):
    value: Text
    description: str = ""
    kind: Literal["value", "file", "directory"] = "value"


class ProviderUnavailable(StrEnum):
    PROCESS = "cannot inspect local references"
    TIMEOUT = "local reference inspection timed out"
    REPOSITORY = "local references unavailable"
    ENCODING = "local references are not UTF-8"


@dataclass(frozen=True)
class ProviderValues:
    values: tuple[str, ...]


def git_branches(cwd: str, prefix: str = "") -> ProviderValues | ProviderUnavailable:
    """The only provider effect: bounded local ref inspection, never a task.

    Ignore ambient repository selectors and configuration. Git's fixed local
    plumbing command neither invokes hooks nor contacts configured remotes.
    """
    if any(char in prefix for char in "*?[\\"):
        return ProviderValues(())
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_") and key not in ("PAGER", "GIT_PAGER")
    }
    env.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_OPTIONAL_LOCKS="0",
        GIT_TERMINAL_PROMPT="0",
        LC_ALL="C",
    )
    try:
        result = subprocess.run(
            [
                "git",
                "--no-pager",
                "for-each-ref",
                "--format=%(refname:strip=2)",
                "--sort=refname",
                f"--count={MAX_CANDIDATES}",
                "refs/heads/" + prefix + ("*" if prefix else ""),
                *(["refs/heads/" + prefix + "*/**"] if prefix else []),
            ],
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=PROVIDER_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return ProviderUnavailable.TIMEOUT
    except OSError:
        return ProviderUnavailable.PROCESS
    if result.returncode:
        return ProviderUnavailable.REPOSITORY
    try:
        lines = result.stdout.decode("utf-8").splitlines()
    except UnicodeError:
        return ProviderUnavailable.ENCODING
    return ProviderValues(
        tuple(sorted({value for value in lines if clean(value) and value.startswith(prefix)}))
    )


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
        case "provider":
            match completion.id:
                case "git.branches":
                    source = git_branches(cwd, prefix)
            if isinstance(source, ProviderUnavailable):
                return []
            return [Candidate(value=value) for value in source.values if value.startswith(prefix)]
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
    token = request.args[request.cursor] if request.cursor < len(request.args) else ""
    prefix = token[: request.offset] if request.offset is not None else token
    before = request.args[: request.cursor]
    if request.command == "ayi":
        if not before:
            return [
                Candidate(value=name, description=description)
                for name, description in (
                    ("new", "Create a shell tool through a reviewed interview"),
                    ("modify", "Modify an existing tool through review and verification"),
                    ("explain", "Explain a saved tool without changing it"),
                    ("sessions", "List saved tool-design sessions"),
                    ("resume", "Resume a saved session"),
                    ("fork", "Fork a saved session without changing its parent"),
                    ("review", "Review a saved session or checkpoint"),
                    ("list", "List saved tools and their invocations"),
                    ("install", "Plan, apply, or remove installed command links"),
                    ("update", "Update the installed source from main"),
                    ("shell", "Generate native shell integration"),
                    ("--help", "Show usage"),
                    ("--version", "Show version and installed source revision"),
                    ("-V", "Show version and installed source revision"),
                )
                if name.startswith(prefix) and (not name.startswith("-") or prefix.startswith("-"))
            ]
        if before[0] in ("new", "modify") and "--" not in before and prefix.startswith("-"):
            return [
                Candidate(value=name, description=description)
                for name, description in (
                    ("--force", "Replace an existing command after review"),
                    ("--design-model", "Choose the interview model"),
                    ("--implementation-harness", "Choose Pi or Codex for implementation"),
                    ("--implementation-model", "Choose the implementation model"),
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
                [Candidate(value="--checkpoint", description="Choose a saved checkpoint")]
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
        choices = {
            "install": (
                ("plan", "Show link ownership and proposed installation changes"),
                ("apply", "Install owned command links"),
                ("uninstall", "Remove owned command links"),
            ),
            "shell": (("init", "Emit shell integration for the current session"),),
        }
        if len(before) == 1:
            return [
                Candidate(value=name, description=description)
                for name, description in choices.get(before[0], ())
                if name.startswith(prefix)
            ]
        if before == ["shell", "init"] and "zsh".startswith(prefix):
            return [Candidate(value="zsh", description="Native Zsh completion and registration")]
        return []
    interface = read_interface(request.command)
    if interface is None:
        return []
    options = {
        name: option
        for option in interface.options
        for name in option.names + (option.negative_names if interface.runtime == "python" else [])
    }
    marker = "\x00ayi-cursor"
    while marker in before:
        marker += "-"
    grammar = parser(interface, cursor=marker)
    try:
        namespace, extra = grammar.parse_known_args([*before, marker])
    except UsageError:
        return []
    if extra not in ([], [marker]):
        return []
    parsed: dict[str, object] = vars(namespace)
    if parsed.get("help"):
        return []
    literal = "--" in before

    def at_cursor(value: object) -> bool:
        return value == marker or (isinstance(value, list) and marker in value)

    def accepted(candidates: list[Candidate]) -> list[Candidate]:
        result: list[Candidate] = []
        for candidate in candidates[:MAX_CANDIDATES]:
            try:
                _, extra = grammar.parse_known_args([*before, candidate.value, marker])
            except UsageError:
                continue
            if extra in ([], [marker]):
                result.append(candidate)
        return result

    for option in interface.options:
        if option.value and at_cursor(parsed.get(option.key)):
            return accepted(values(option.value.completion, prefix, request.cwd))
    if not literal and "=" in prefix:
        name, fragment = prefix.split("=", 1)
        option = options.get(name)
        if option and option.value:
            return accepted(
                [
                    Candidate(value=name + "=" + candidate.value, kind=candidate.kind)
                    for candidate in values(option.value.completion, fragment, request.cwd)
                    if clean(name + "=" + candidate.value)
                ]
            )
        return []
    if not literal and prefix.startswith("-") and not prefix.startswith("--") and len(prefix) > 2:
        option = options.get(prefix[:2])
        if option and option.value:
            return accepted(
                [
                    Candidate(value=prefix[:2] + candidate.value, kind=candidate.kind)
                    for candidate in values(option.value.completion, prefix[2:], request.cwd)
                    if clean(prefix[:2] + candidate.value)
                ]
            )
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
            if name.startswith(prefix) and (option.repeatable or option.key not in parsed)
        )
        result.extend(
            Candidate(value=name, description="Show help")
            for name in ("--help", "-h")
            if name not in options and name.startswith(prefix)
        )
    if literal or not prefix.startswith("-"):
        for positional in interface.positionals:
            if at_cursor(parsed.get(positional.name)):
                result.extend(values(positional.completion, prefix, request.cwd))
                break
    return accepted(result)


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
