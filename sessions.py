"""Interview values and append-only local checkpoints. No model or registry effects."""

from __future__ import annotations

import fcntl
import os
import re
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from install import valid_name

type Line = Annotated[
    str, StringConstraints(min_length=1, max_length=600, pattern=r"^[^\x00-\x1f\x7f]+$")
]
type Name = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{0,47}$")]
type ModelName = Annotated[
    str,
    StringConstraints(
        min_length=1, max_length=200, pattern=r"^[^-\s\x00-\x1f\x7f][^\s\x00-\x1f\x7f]*$"
    ),
]
type UserText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=20000,
        pattern=r"^[^\x00-\x08\x0b-\x1f\x7f]+$",
    ),
]


class Value(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Question(Value):
    status: Literal["question"] = "question"
    topic: Literal["name", "usage", "requirements"]
    question: Line
    options: Annotated[tuple[Line, ...], Field(min_length=2, max_length=5)]


class Ready(Value):
    status: Literal["ready"] = "ready"
    name: Name
    requirements: Annotated[str, StringConstraints(min_length=1, max_length=20000)]


RESPONSE: TypeAdapter[Question | Ready] = TypeAdapter(
    Annotated[Question | Ready, Field(discriminator="status")]
)


class Implementation(Value):
    harness: Literal["pi", "codex"] = "pi"
    model: ModelName | None = None


class Settings(Value):
    design_model: ModelName | None = None
    implementation: Implementation = Implementation()


class Answer(Value):
    question: Question
    answer: UserText


class Next(Value):
    kind: Literal["next"] = "next"


class Ask(Value):
    kind: Literal["ask"] = "ask"
    question: Question


class ReviewAnswer(Value):
    kind: Literal["answer"] = "answer"
    question: Question
    answer: UserText


class ReviewPlan(Value):
    kind: Literal["plan"] = "plan"
    plan: Ready


class ReviewDraft(Value):
    kind: Literal["draft"] = "draft"
    plan: Ready
    directory: str


class Done(Value):
    kind: Literal["done"] = "done"
    name: Name


class Direction(Value):
    after_answer: Annotated[int, Field(ge=0)]
    instruction: UserText
    context: Annotated[Ask | ReviewAnswer | ReviewPlan | ReviewDraft, Field(discriminator="kind")]


type Phase = Annotated[
    Next | Ask | ReviewAnswer | ReviewPlan | ReviewDraft | Done, Field(discriminator="kind")
]


class Session(Value):
    schema_version: Literal[1] = 1
    registry: str
    action: Literal["new", "modify"]
    prompt: UserText
    fixed_name: Name | None
    source: dict[str, str]
    source_modes: dict[str, int]
    force: bool = False
    settings: Settings = Settings()
    answers: Annotated[tuple[Answer, ...], Field(max_length=20)] = ()
    directions: tuple[Direction, ...] = ()
    phase: Phase = Next()

    @property
    def name(self) -> str | None:
        return self.fixed_name or next(
            (a.answer for a in self.answers if a.question.topic == "name"), None
        )

    @property
    def required_topic(self) -> Literal["name", "usage"] | None:
        if self.name is None:
            return "name"
        if self.action == "new" and not any(a.question.topic == "usage" for a in self.answers):
            return "usage"
        return None

    @model_validator(mode="after")
    def coherent(self) -> Self:
        names = [a.answer for a in self.answers if a.question.topic == "name"]
        if len(names) > 1 or (names and self.fixed_name is not None):
            raise ValueError("the command has more than one name")
        if self.name is not None and not valid_name(self.name):
            raise ValueError("invalid command name")
        if self.action == "modify" and (self.fixed_name is None or not self.source):
            raise ValueError("modification needs the original command")
        if self.force and self.fixed_name is None:
            raise ValueError("force needs an explicit name")
        if self.source.keys() != self.source_modes.keys():
            raise ValueError("source and permission snapshots differ")
        if any(d.after_answer > len(self.answers) for d in self.directions):
            raise ValueError("direction refers to an absent answer")
        if isinstance(self.phase, (ReviewPlan, ReviewDraft)) and (
            self.required_topic is not None or self.phase.plan.name != self.name
        ):
            raise ValueError("plan differs from the agreed answers")
        if isinstance(self.phase, Done) and (
            self.required_topic is not None or self.phase.name != self.name
        ):
            raise ValueError("completed command differs from the agreed answers")
        if isinstance(self.phase, (Ask, ReviewAnswer)) and (
            (self.required_topic is not None and self.phase.question.topic != self.required_topic)
            or (self.name is not None and self.phase.question.topic == "name")
        ):
            raise ValueError("pending question is out of sequence")
        return self


class SessionError(Exception):
    pass


def session_root() -> Path:
    return (
        Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "ayi/sessions"
    )


def find(identifier: str) -> Path:
    if re.fullmatch(r"flow-[a-z0-9_]+", identifier) is None:
        raise SessionError("invalid session ID")
    directory = session_root() / identifier
    if directory.is_symlink() or not directory.is_dir():
        raise SessionError("unknown session ID")
    return directory


def allocate() -> Path:
    root = session_root()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return Path(tempfile.mkdtemp(prefix="flow-", dir=root))


@contextmanager
def locked(directory: Path) -> Iterator[None]:
    with (directory / "lock").open("a", encoding="utf-8") as stream:
        os.chmod(stream.name, 0o600)
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SessionError("session is already open in another process") from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def checkpoints(directory: Path) -> list[Path]:
    return sorted((directory / "checkpoints").glob("[0-9][0-9][0-9][0-9][0-9][0-9].json"))


def load(directory: Path, checkpoint: int | None = None) -> Session:
    paths = checkpoints(directory)
    if not paths:
        raise SessionError("session has no checkpoint")
    path = paths[-1] if checkpoint is None else directory / "checkpoints" / f"{checkpoint:06}.json"
    if path not in paths or path.is_symlink():
        raise SessionError("unknown checkpoint")
    try:
        return Session.model_validate_json(path.read_bytes())
    except ValidationError as error:
        raise SessionError("invalid session checkpoint") from error


def save(directory: Path, session: Session) -> None:
    """Caller holds the session lock. Rename publishes only complete snapshots."""
    target = directory / "checkpoints"
    target.mkdir(mode=0o700, exist_ok=True)
    paths = checkpoints(directory)
    number = int(paths[-1].stem) + 1 if paths else 1
    descriptor, temporary = tempfile.mkstemp(prefix=".pending-", dir=target)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            _ = output.write(session.model_dump_json(indent=2) + "\n")
            output.flush()
            os.fsync(output.fileno())
        os.rename(temporary, target / f"{number:06}.json")
        directory_fd = os.open(target, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        Path(temporary).unlink(missing_ok=True)
