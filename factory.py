from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, ValidationError

import create

type Line = Annotated[
    str, StringConstraints(min_length=1, max_length=600, pattern=r"^[^\x00-\x1f\x7f]+$")
]
type Name = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{0,47}$")]


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: Literal["question"]
    topic: Literal["name", "usage", "requirements"]
    question: Line
    options: Annotated[list[Line], Field(min_length=2, max_length=5)]


class Ready(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: Literal["ready"]
    name: Name
    requirements: Annotated[str, StringConstraints(min_length=1, max_length=20000)]


RESPONSE: TypeAdapter[Question | Ready] = TypeAdapter(
    Annotated[Question | Ready, Field(discriminator="status")]
)


class FactoryError(Exception):
    def __init__(self, message: str, status: int = 65) -> None:
        super().__init__(message)
        self.status = status


def model(mode: str, request: str) -> str:
    print(f"ayi / {mode} / thinking...", file=sys.stderr, flush=True)
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
            "--append-system-prompt",
            str(create.ROOT / f"prompts/{mode}.md"),
            "--",
            request,
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode or not result.stdout.strip():
        raise FactoryError(
            f"Pi {mode} unavailable (exit {result.returncode}). {result.stderr.strip()}", 69
        )
    return result.stdout.strip()


def entry(label: str) -> str:
    # Python's readline integration supplies cursor editing without storing prompt history.
    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    if interactive:
        try:
            __import__("readline")
        except ImportError:
            pass
    print(label, file=sys.stderr, flush=True)
    if not interactive:
        print("ayi > ", end="", file=sys.stderr, flush=True)
    try:
        answer = input("ayi > " if interactive else "").strip()
    except EOFError:
        raise FactoryError("cancelled", 130) from None
    if not answer:
        raise FactoryError("cancelled (empty answer)", 130)
    return answer


def choose(question: Question, action: str) -> str:
    width = max(20, shutil.get_terminal_size().columns - 4)
    options = [*question.options, "Write a different answer..."]
    rows = [f"{index}  {option}" for index, option in enumerate(options, 1)]
    if sys.stdin.isatty() and sys.stderr.isatty() and shutil.which("fzf"):
        # Do not inherit preview/execute bindings or default commands from shell configuration.
        env = {
            key: value for key, value in os.environ.items() if not key.startswith("FZF_DEFAULT_")
        }
        result = subprocess.run(
            [
                "fzf",
                "--height=~18",
                "--layout=reverse",
                "--border=sharp",
                "--no-unicode",
                "--no-multi",
                "--no-sort",
                "--wrap",
                "--prompt=ayi > ",
                "--header="
                + f"ayi / {action} / {question.topic}\n"
                + textwrap.fill(question.question, min(36, width - 4))
                + "\nEnter: select / Esc: cancel",
                *(["--no-color"] if "NO_COLOR" in env else []),
            ],
            input="\n".join(rows),
            stdout=subprocess.PIPE,
            text=True,
            env=env,
            check=False,
        )
        if result.returncode in (1, 130):
            raise FactoryError("cancelled", 130)
        if result.returncode:
            raise FactoryError(f"fzf failed (exit {result.returncode})", 69)
        selected = result.stdout.rstrip("\n")
        if selected not in rows:
            raise FactoryError("fzf returned an unknown choice")
        index = rows.index(selected)
        answer = options[index] if index < len(question.options) else entry("Your answer:")
    else:
        print(f"\nayi / {action} / {question.topic}", file=sys.stderr)
        print(textwrap.fill(question.question, width), file=sys.stderr, flush=True)
        for row in rows:
            print(textwrap.fill(row, width, subsequent_indent="   "), file=sys.stderr)
        while True:
            value = entry("Choose a number, or type your answer:")
            if not value.isdecimal():
                answer = value
                break
            if 1 <= int(value) <= len(rows):
                index = int(value) - 1
                answer = options[index] if index < len(question.options) else entry("Your answer:")
                break
            print("Choose a listed number, or type an answer.", file=sys.stderr)
    print(f"Selected: {answer}", file=sys.stderr)
    return answer


def command_context(name: str) -> dict[str, str]:
    if re.fullmatch(r"[a-z][a-z0-9-]{0,47}", name) is None:
        raise FactoryError(f"invalid command name: {name}", 64)
    task, verification, requirements = create.command_paths(name)
    if not task.is_file() or task.is_symlink():
        raise FactoryError(f"unknown command: {name}", 64)
    paths = [task, verification, requirements]
    # Bundled tasks delegate their implementation to workflows.py.
    if name in ("ci", "review"):
        paths.extend([create.ROOT / "workflows.py", create.ROOT / f"prompts/{name}.md"])
    return {
        str(path.relative_to(create.ROOT)): path.read_text()
        for path in paths
        if path.is_file() and not path.is_symlink()
    }


def interview(
    action: str, prompt: str, name: str | None, source: dict[str, str]
) -> tuple[Ready, str]:
    answers: list[dict[str, str]] = []
    for _ in range(20):
        required = (
            "name"
            if name is None
            else (
                "usage"
                if action == "new" and not any(item["topic"] == "usage" for item in answers)
                else None
            )
        )
        request = json.dumps(
            {
                "action": action,
                "request": prompt,
                "name": name,
                "source": source,
                "answers": answers,
                "required_topic": required,
            }
        )
        try:
            response = RESPONSE.validate_json(model("interview", request))
        except ValidationError as error:
            raise FactoryError(
                "Pi returned an invalid interview response; retry the request."
            ) from error
        if isinstance(response, Ready):
            if required is not None or response.name != name:
                raise FactoryError("Pi skipped a required question or changed the agreed name.")
            return response, request
        if (required is not None and response.topic != required) or (
            response.topic == "name" and name is not None
        ):
            raise FactoryError("Pi returned a question out of sequence.")
        if response.topic == "name" and any(
            re.fullmatch(r"[a-z][a-z0-9-]{0,47}", option) is None for option in response.options
        ):
            raise FactoryError("Pi suggested an invalid command name.")
        answer = choose(response, action)
        if response.topic == "name":
            while re.fullmatch(r"[a-z][a-z0-9-]{0,47}", answer) is None:
                answer = entry(
                    "Use 1-48 lowercase letters, digits or hyphens; start with a letter:"
                )
            name = answer
            if any(path.exists() or path.is_symlink() for path in create.command_paths(name)):
                raise FactoryError(f"command already exists: {name}; use ayi modify {name}")
        answers.append({"topic": response.topic, "question": response.question, "answer": answer})
    raise FactoryError("Interview exceeded 20 questions; retry with a more specific request.")


def main(arguments: list[str]) -> int:
    action = arguments[0] if arguments else "new"
    values = arguments[1:]
    if (
        action not in ("new", "modify", "explain")
        or (action in ("modify", "explain") and not values)
        or (action == "explain" and len(values) != 1)
    ):
        raise FactoryError("usage: ayi new [prompt] | modify <name> [prompt] | explain <name>", 64)
    name = values.pop(0) if action != "new" else None
    source = command_context(name) if name is not None else {}
    original: create.Original | None = None
    if action == "modify" and name is not None:
        snapshot = create.read_original(name)
        if isinstance(snapshot, create.Rejected):
            raise FactoryError(
                "command has uncommitted or untracked changes; commit or resolve them before modifying"
            )
        original = snapshot
        # Generate from the same bytes that registration will check before replacement.
        for path, previous in original.files.items():
            key = str(path.relative_to(create.ROOT))
            if previous is None:
                _ = source.pop(key, None)
            else:
                source[key] = previous[0].decode()
    if shutil.which("pi") is None:
        raise FactoryError("missing dependency: pi", 69)
    if action == "explain":
        print(model("explain", json.dumps({"name": name, "source": source})))
        return 0
    prompt = " ".join(values).strip() or entry(
        "What shell tool should Ayi build?" if action == "new" else f"What should change in {name}?"
    )
    ready, transcript = interview(action, prompt, name, source)
    request = (
        f"The factory interview is complete. Implement the resolved requirements without further questions.\n"
        f"Keep the exact command name: {ready.name}.\n"
        f"Resolved requirements:\n{ready.requirements}\n"
        f"Request, existing source, and user answers (JSON):\n{transcript}"
    )
    return create.main([request], interactive=False, name=ready.name, original=original)


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except FactoryError as error:
        print(f"ayi: {error}", file=sys.stderr)
        raise SystemExit(error.status) from error
    except OSError as error:
        print(f"ayi: local operation failed: {error.strerror}", file=sys.stderr)
        raise SystemExit(74) from error
    except KeyboardInterrupt:
        print("\nayi: cancelled", file=sys.stderr)
        raise SystemExit(130) from None
