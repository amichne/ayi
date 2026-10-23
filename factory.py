from __future__ import annotations

import json
import shutil
import subprocess
import sys
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, ValidationError

import create
import install
import ui

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
    with ui.activity(f"ayi / {mode} / thinking..."):
        return model_response(mode, request)


def model_response(mode: str, request: str) -> str:
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
    try:
        answer = ui.entry(label)
    except EOFError:
        raise FactoryError("cancelled", 130) from None
    if not answer:
        raise FactoryError("cancelled (empty answer)", 130)
    return answer


def choose(question: Question, action: str) -> str:
    options = [*question.options, "Write a different answer..."]
    while True:
        try:
            answer = ui.entry(f"{action} / {question.topic}\n{question.question}", options)
        except EOFError:
            raise FactoryError("cancelled", 130) from None
        if not answer:
            raise FactoryError("cancelled (empty answer)", 130)
        if not answer.isdecimal():
            break
        ui.status("Choose a listed number, or type an answer.")
    if answer == options[-1]:
        answer = entry("Your answer:")
    ui.status(f"Selected: {answer}")
    return answer


def command_context(name: str) -> dict[str, str]:
    if not install.valid_name(name):
        raise FactoryError(f"invalid command name: {name}", 64)
    task, verification, requirements = create.command_paths(name)
    if not task.is_file() or task.is_symlink():
        raise FactoryError(f"unknown command: {name}", 64)
    paths = [task, verification, requirements]
    return {
        str(path.relative_to(create.ROOT)): path.read_text()
        for path in paths
        if path.is_file() and not path.is_symlink()
    }


def check_destination(name: str, force: bool = False) -> None:
    if not install.valid_name(name):
        raise FactoryError(f"invalid command name: {name}", 64)
    link = install.command_link(name)
    if link.state == install.LinkState.CONFLICT and (
        not force or not (link.destination.is_file() or link.destination.is_symlink())
    ):
        raise FactoryError(
            f"installation conflict: {link.destination}; --force can replace files or symlinks, never directories"
        )


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
            not install.valid_name(option) for option in response.options
        ):
            raise FactoryError("Pi suggested an invalid command name.")
        answer = choose(response, action)
        if response.topic == "name":
            while not install.valid_name(answer):
                answer = entry(
                    "Use 1-48 lowercase letters, digits or hyphens; start with a letter:"
                )
            name = answer
            check_destination(name)
            if any(path.exists() or path.is_symlink() for path in create.command_paths(name)):
                raise FactoryError(f"command already exists: {name}; use ayi modify {name}")
        answers.append({"topic": response.topic, "question": response.question, "answer": answer})
    raise FactoryError("Interview exceeded 20 questions; retry with a more specific request.")


def main(arguments: list[str]) -> int:
    action = arguments[0] if arguments else "new"
    values: list[str] = []
    force = False
    literal = False
    for value in arguments[1:]:
        if value == "--" and not literal:
            literal = True
        elif value == "--force" and not literal:
            force = True
        else:
            values.append(value)
    if (
        action not in ("new", "modify", "explain")
        or (action in ("modify", "explain") and not values)
        or (action == "explain" and (len(values) != 1 or force))
    ):
        raise FactoryError(
            "usage: ayi new [--force] <name> [prompt] | modify [--force] <name> [prompt] | explain <name>",
            64,
        )
    name = None
    if values and (action != "new" or len(values) > 1 or not any(c.isspace() for c in values[0])):
        name = values.pop(0)
    if force and name is None:
        raise FactoryError("--force requires an explicit command name", 64)
    source: dict[str, str] = {}
    existing = False
    if name is not None:
        if not install.valid_name(name):
            raise FactoryError(f"invalid command name: {name}", 64)
        existing = any(path.exists() or path.is_symlink() for path in create.command_paths(name))
        if action == "new" and existing and not force:
            raise FactoryError(
                f"command already exists: {name}; use ayi modify {name} or new --force"
            )
        if action != "new" or existing:
            source = command_context(name)
        if action != "explain":
            check_destination(name, force)
    original: create.Original | None = None
    if action != "explain" and existing and name is not None:
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
        ui.explain(model("explain", json.dumps({"name": name, "source": source})))
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
    return create.main([request], name=ready.name, original=original, force=force)


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
