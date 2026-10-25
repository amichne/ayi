from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import NoReturn, assert_never, override

from pydantic import ValidationError

import create
import install
import sessions
import ui
from completion import commands, read_interface
from sessions import (
    RESPONSE,
    Answer,
    Ask,
    Direction,
    Done,
    Next,
    Ready,
    ReviewAnswer,
    ReviewDraft,
    ReviewPlan,
    Session,
    Settings,
)


class FactoryError(Exception):
    def __init__(self, message: str, status: int = 65) -> None:
        super().__init__(message)
        self.status = status


class CommandParser(argparse.ArgumentParser):
    @override
    def error(self, message: str) -> NoReturn:
        raise FactoryError(f"{message}. See {self.prog} --help.", 64)


def model(mode: str, request: str, model_name: str | None = None) -> str:
    with ui.activity(f"ayi / {mode} / thinking..."):
        return model_response(mode, request, model_name)


def model_response(mode: str, request: str, model_name: str | None = None) -> str:
    if shutil.which("pi") is None:
        raise FactoryError("missing dependency: pi", 69)
    # Every design, discussion, and explanation receives the same source-owned policy.
    payload = json.loads(request)
    payload["cli_policy"] = (create.ROOT / "prompts/cli-policy.md").read_text()
    payload["cli_components"] = (create.ROOT / "prompts/cli-components.md").read_text()
    request = json.dumps(payload)
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
            *(["--model", model_name] if model_name is not None else []),
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


def command_context(name: str) -> dict[str, str]:
    if not install.valid_name(name):
        raise FactoryError(f"invalid command name: {name}", 64)
    paths = create.command_paths(name)
    if not paths[0].is_file() or paths[0].is_symlink():
        raise FactoryError(f"unknown command: {name}", 64)
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


def request_for(session: Session) -> str:
    return json.dumps(
        {
            "action": session.action,
            "request": session.prompt,
            "name": session.name,
            "source": session.source,
            "answers": [
                {"topic": a.question.topic, "question": a.question.question, "answer": a.answer}
                for a in session.answers
            ],
            "directions": [d.model_dump() for d in session.directions],
            "required_topic": session.required_topic,
        }
    )


def overview(session: Session) -> None:
    ui.status(f"Request: {session.prompt}")
    for index, answer in enumerate(session.answers, 1):
        ui.status(f"{index}. {answer.question.question}\n   {answer.answer}")
    for direction in session.directions:
        ui.status(f"Direction after answer {direction.after_answer}: {direction.instruction}")
    if isinstance(session.phase, (ReviewPlan, ReviewDraft)):
        ui.review("Proposed approach", session.phase.plan.requirements.encode())
    ui.status(f"Design: pi / {session.settings.design_model or 'default'}")
    implementation = session.settings.implementation
    ui.status(f"Implementation: {implementation.harness} / {implementation.model or 'default'}")


def select_settings(current: Settings) -> Settings:
    design = ui.entry(
        "Design model for Pi (default uses Pi settings):", initial=current.design_model or "default"
    )
    harness = ui.entry(
        "Implementation harness:", ("pi", "codex"), initial=current.implementation.harness
    )
    model_name = ui.entry(
        "Implementation model (default uses harness settings):",
        initial=current.implementation.model or "default",
    )
    return Settings.model_validate(
        {
            "design_model": None if design == "default" else design,
            "implementation": {
                "harness": harness,
                "model": None if model_name == "default" else model_name,
            },
        }
    )


def original_for(session: Session) -> create.Original | None:
    if session.registry != str(create.ROOT.resolve()):
        raise FactoryError("session belongs to another registry")
    name = session.name
    if name is None:
        return None
    check_destination(name, session.force)
    if session.source:
        original = create.read_original(name)
        if isinstance(original, create.Rejected):
            raise FactoryError("command changed since the session started")
        source = {
            str(p.relative_to(create.ROOT)): v[0].decode()
            for p, v in original.files.items()
            if v is not None
        }
        modes = {
            str(p.relative_to(create.ROOT)): v[1]
            for p, v in original.files.items()
            if v is not None
        }
        if source != session.source or modes != session.source_modes:
            raise FactoryError("command changed since the session started")
        return original
    if any(path.exists() or path.is_symlink() for path in create.command_paths(name)):
        raise FactoryError(f"command already exists: {name}; use ayi modify {name}")
    return None


def next_phase(session: Session) -> Ask | ReviewPlan:
    request = request_for(session)
    try:
        response = RESPONSE.validate_json(
            model("interview", request, session.settings.design_model)
        )
    except ValidationError as error:
        raise FactoryError("Pi returned an invalid interview response; resume to retry.") from error
    required = session.required_topic
    if isinstance(response, Ready):
        if required is not None or response.name != session.name:
            raise FactoryError("Pi skipped a required question or changed the agreed name.")
        return ReviewPlan(plan=response)
    if len(session.answers) >= 20:
        raise FactoryError(
            "Interview reached 20 answers. Fork an earlier checkpoint to revise the scope."
        )
    if (required is not None and response.topic != required) or (
        response.topic == "name" and session.name is not None
    ):
        raise FactoryError("Pi returned a question out of sequence.")
    if response.topic == "name" and any(
        not install.valid_name(option) for option in response.options
    ):
        raise FactoryError("Pi suggested an invalid command name.")
    return Ask(question=response)


CONTROLS = (
    "/clarify <direction> · /show-me · /unclear · /review · /edit N · /settings · /fork · /pause"
)


def run_session(directory: Path, session: Session) -> int:
    ui.status(f"Session: {directory.name}. Resume: ayi resume {directory.name}")
    _ = original_for(session) if not isinstance(session.phase, Done) else None
    while True:
        phase = session.phase
        if isinstance(phase, Done):
            ui.status(f"Session complete: {phase.name}")
            return 0
        if isinstance(phase, Next):
            session = session.model_copy(update={"phase": next_phase(session)})
            sessions.save(directory, session)
            continue
        ui.status(CONTROLS)
        match phase:
            case Ask(question=question):
                value = ui.entry(
                    f"{session.action} / {question.topic}\n{question.question}", question.options
                )
            case ReviewAnswer(question=question, answer=answer):
                ui.status(f"Review answer: {question.question}\n{answer}")
                value = ui.entry(
                    "Submit this answer to the design model?", ("Submit answer", "Edit answer")
                )
            case ReviewPlan() | ReviewDraft():
                overview(session)
                value = ui.entry(
                    "Review the complete design before the next step:",
                    (
                        "Implement" if isinstance(phase, ReviewPlan) else "Review draft",
                        "/settings",
                        "/clarify",
                        "/show-me",
                        "/pause",
                    ),
                )
            case _:
                assert_never(phase)
        value = {
            "Submit answer": "/submit",
            "Edit answer": "/edit",
            "Implement": "/implement",
            "Review draft": "/draft",
            "clarify": "/clarify",
            "show me": "/show-me",
            "/show me": "/show-me",
            "unclear": "/unclear",
        }.get(value, value)
        command, _, detail = value.partition(" ")
        if command == "/pause":
            ui.status(f"Saved. Resume: ayi resume {directory.name}")
            return 0
        if command == "/fork":
            child = sessions.allocate()
            with sessions.locked(child):
                forked = (
                    session.model_copy(update={"phase": ReviewPlan(plan=phase.plan)})
                    if isinstance(phase, ReviewDraft)
                    else session
                )
                sessions.save(child, forked)
            ui.status(f"Fork saved. Continue: ayi resume {child.name}")
            return 0
        if command == "/review":
            overview(session)
            continue
        if command == "/settings":
            settings = select_settings(session.settings)
            # A different implementation choice needs a fresh draft and explicit invocation.
            updated_phase = ReviewPlan(plan=phase.plan) if isinstance(phase, ReviewDraft) else phase
            session = session.model_copy(update={"settings": settings, "phase": updated_phase})
        elif command in ("/show-me", "/unclear"):
            explanation = model(
                "discuss",
                json.dumps(
                    {
                        "interview": json.loads(request_for(session)),
                        "current": phase.model_dump(),
                        "request": detail
                        or "Explain the approach with a concrete example and tradeoffs. Do not change the design.",
                    }
                ),
                session.settings.design_model,
            )
            ui.explain(explanation)
            continue
        elif command == "/clarify":
            direction = Direction(
                after_answer=len(session.answers),
                instruction=detail or entry("What direction must change?"),
                context=phase,
            )
            session = session.model_copy(
                update={"directions": (*session.directions, direction), "phase": Next()}
            )
        elif command == "/edit" and detail:
            if not detail.isdecimal() or not 1 <= int(detail) <= len(session.answers):
                ui.status("Use /edit N with a listed answer number.")
                continue
            index = int(detail) - 1
            answer = session.answers[index]
            session = session.model_copy(
                update={
                    "answers": session.answers[:index],
                    "directions": tuple(d for d in session.directions if d.after_answer <= index),
                    "phase": ReviewAnswer(question=answer.question, answer=answer.answer),
                }
            )
            ui.status("Later answers leave this branch. Earlier checkpoints preserve them.")
        elif command == "/edit" and isinstance(phase, ReviewAnswer):
            edited = ui.entry("Edit your answer:", initial=phase.answer)
            session = session.model_copy(
                update={"phase": ReviewAnswer(question=phase.question, answer=edited)}
            )
        elif command == "/submit" and isinstance(phase, ReviewAnswer):
            if phase.question.topic == "name" and not install.valid_name(phase.answer):
                ui.status("Use /edit to enter a valid command name.")
                continue
            session = session.model_copy(
                update={
                    "answers": (
                        *session.answers,
                        Answer(question=phase.question, answer=phase.answer),
                    ),
                    "phase": Next(),
                }
            )
            _ = original_for(session)
        elif command == "/implement" and isinstance(phase, ReviewPlan):
            _ = original_for(session)
            request = (
                f"The factory interview is complete. Implement the resolved requirements without further questions.\n"
                f"Keep the exact command name: {phase.plan.name}.\n"
                f"Resolved requirements:\n{phase.plan.requirements}\n"
                f"Request, existing source, and user answers (JSON):\n{request_for(session)}"
            )
            drafts = directory / "drafts"
            drafts.mkdir(exist_ok=True, mode=0o700)
            draft_directory = create.new_directory(drafts)
            status = create.generate(request, draft_directory, session.settings.implementation)
            if status:
                return status
            session = session.model_copy(
                update={"phase": ReviewDraft(plan=phase.plan, directory=str(draft_directory))}
            )
        elif command == "/draft" and isinstance(phase, ReviewDraft):
            original = original_for(session)
            status = create.finish(
                Path(phase.directory), name=phase.plan.name, original=original, force=session.force
            )
            if status:
                return status
            session = session.model_copy(update={"phase": Done(name=phase.plan.name)})
        elif value.startswith("/"):
            ui.status("That action is unavailable here. " + CONTROLS)
            continue
        elif isinstance(phase, Ask) and value:
            session = session.model_copy(
                update={"phase": ReviewAnswer(question=phase.question, answer=value)}
            )
        else:
            ui.status("Choose an action or use /pause to save and exit.")
            continue
        sessions.save(directory, session)


def session_command(arguments: list[str]) -> int:
    action = arguments[0]
    if action == "sessions":
        if len(arguments) != 1:
            raise FactoryError("usage: ayi sessions", 64)
        for directory in sorted(sessions.session_root().glob("flow-*")):
            if directory.is_dir() and not directory.is_symlink():
                state = sessions.load(directory)
                ui.status(
                    f"{directory.name}  {state.phase.kind}  {state.name or '(unnamed)'}  {state.prompt}"
                )
        return 0
    parser = CommandParser(prog=f"ayi {action}")
    _ = parser.add_argument("session")
    _ = parser.add_argument("--checkpoint", type=int)
    options = parser.parse_args(arguments[1:])
    directory = sessions.find(str(options.session))
    checkpoint = options.checkpoint
    if action == "resume" and checkpoint is not None:
        raise FactoryError("Use fork --checkpoint N to continue an earlier checkpoint.", 64)
    with sessions.locked(directory):
        state = sessions.load(directory, checkpoint)
        if action == "review":
            overview(state)
            for path in sessions.checkpoints(directory):
                ui.status(
                    f"Checkpoint {int(path.stem)}: {sessions.load(directory, int(path.stem)).phase.kind}"
                )
            return 0
        if action == "resume":
            return run_session(directory, state)
    child = sessions.allocate()
    # Forks never share a mutable generated draft with the parent.
    if isinstance(state.phase, ReviewDraft):
        state = state.model_copy(update={"phase": ReviewPlan(plan=state.phase.plan)})
    if isinstance(state.phase, Done):
        raise FactoryError("Fork an earlier checkpoint to change a completed session.", 64)
    with sessions.locked(child):
        sessions.save(child, state)
        return run_session(child, state)


def main(arguments: list[str]) -> int:
    action = arguments[0] if arguments else "new"
    if action in ("sessions", "resume", "fork", "review"):
        return session_command(arguments)
    if action == "list":
        if arguments != ["list"]:
            raise FactoryError("usage: ayi list", 64)
        rows: list[tuple[str, str, str]] = []
        for command in commands():
            interface = read_interface(command)
            if interface is not None:
                summary = interface.summary
                invocation = interface.examples[0] if interface.examples else command + " --help"
            else:
                source_text = (create.ROOT / "tasks" / command).read_text()
                metadata = tomllib.loads(
                    "\n".join(
                        line.removeprefix("#MISE ")
                        for line in source_text.splitlines()
                        if line.startswith("#MISE ")
                    )
                )
                summary = str(metadata.get("description", "Legacy command (no interface)"))
                invocation = command + " --help"
            rows.append((command, summary, invocation))
        ui.command_list(rows)
        return 0
    parser = CommandParser(prog=f"ayi {action}")
    _ = parser.add_argument("--force", action="store_true")
    _ = parser.add_argument("--design-model")
    _ = parser.add_argument("--implementation-harness", choices=("pi", "codex"), default="pi")
    _ = parser.add_argument("--implementation-model")
    _ = parser.add_argument("values", nargs="*")
    options = parser.parse_intermixed_args(arguments[1:])
    values: list[str] = options.values
    force: bool = options.force
    settings = Settings.model_validate(
        {
            "design_model": options.design_model,
            "implementation": {
                "harness": options.implementation_harness,
                "model": options.implementation_model,
            },
        }
    )
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
        ui.explain(
            model("explain", json.dumps({"name": name, "source": source}), settings.design_model)
        )
        return 0
    prompt = " ".join(values).strip() or entry(
        "What shell tool should Ayi build?" if action == "new" else f"What should change in {name}?"
    )
    state = Session(
        registry=str(create.ROOT.resolve()),
        action="modify" if action == "modify" else "new",
        prompt=prompt,
        fixed_name=name,
        source=source,
        source_modes={
            str(p.relative_to(create.ROOT)): value[1]
            for p, value in original.files.items()
            if value is not None
        }
        if original
        else {},
        force=force,
        settings=settings,
    )
    directory = sessions.allocate()
    with sessions.locked(directory):
        sessions.save(directory, state)
        return run_session(directory, state)


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except (sessions.SessionError, EOFError) as error:
        print(f"ayi: {error or 'cancelled; resume the saved session'}", file=sys.stderr)
        raise SystemExit(130 if isinstance(error, EOFError) else 65) from error
    except FactoryError as error:
        print(f"ayi: {error}", file=sys.stderr)
        raise SystemExit(error.status) from error
    except (ValueError, UnicodeError) as error:
        print(f"ayi: invalid command metadata: {error}", file=sys.stderr)
        raise SystemExit(65) from error
    except OSError as error:
        print(f"ayi: local operation failed: {error.strerror}", file=sys.stderr)
        raise SystemExit(74) from error
    except KeyboardInterrupt:
        print("\nayi: cancelled", file=sys.stderr)
        raise SystemExit(130) from None
