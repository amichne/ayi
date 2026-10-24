from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

import install
import ui
from interface import Interface
from sessions import Implementation

ROOT = Path(__file__).resolve().parent
REPO = ROOT


class Rejection(StrEnum):
    INVALID = "invalid draft"
    UNRESOLVED = "needs-input: resolve requirements in Pi before registration"
    CHANGED = "verification changed its inputs"
    EXISTS = "command already exists"


@dataclass(frozen=True)
class Rejected:
    reason: Rejection


@dataclass(frozen=True)
class Draft:
    name: str
    task: bytes
    verification: bytes
    requirements: bytes
    interface: bytes


class ScriptName(StrEnum):
    TASK = "task"
    VERIFICATION = "verify"


@dataclass(frozen=True)
class ScriptLock:
    script: ScriptName
    content: bytes

    @property
    def filename(self) -> str:
        return f"{self.script}.lock"


@dataclass(frozen=True)
class _PreparedDraft:
    draft: Draft
    locks: tuple[ScriptLock, ...]


@dataclass(frozen=True)
class PreparationFailed:
    script: ScriptName
    status: int


@dataclass(frozen=True)
class _VerifiedDraft:
    prepared: _PreparedDraft


@dataclass(frozen=True)
class VerificationFailed:
    status: int


@dataclass(frozen=True)
class Original:
    name: str
    files: dict[Path, tuple[bytes, int] | None]


def command_paths(name: str) -> tuple[Path, Path, Path, Path, Path, Path]:
    return (
        ROOT / "tasks" / name,
        ROOT / "verification" / name,
        ROOT / "requirements" / f"{name}.md",
        ROOT / "interface" / f"{name}.json",
        ROOT / "tasks" / f"{name}.lock",
        ROOT / "verification" / f"{name}.lock",
    )


def read_original(name: str) -> Original | Rejected:
    paths = command_paths(name)
    relative = [str(path.relative_to(REPO)) for path in paths]
    status = subprocess.run(
        git_arguments("status", "--porcelain", "--untracked-files=all", "--", *relative),
        capture_output=True,
        check=False,
    )
    tracked = subprocess.run(
        git_arguments("ls-files", "--", *relative), capture_output=True, text=True, check=False
    )
    if status.returncode or tracked.returncode or status.stdout or not paths[0].is_file():
        return Rejected(Rejection.CHANGED)
    files: dict[Path, tuple[bytes, int] | None] = {}
    for path, name_in_git in zip(paths, relative, strict=True):
        if path.is_symlink() or (
            path.exists() and (not path.is_file() or name_in_git not in tracked.stdout.splitlines())
        ):
            return Rejected(Rejection.CHANGED)
        files[path] = (
            (path.read_bytes(), stat.S_IMODE(path.stat().st_mode)) if path.exists() else None
        )
    return Original(name, files)


def read_draft(directory: Path) -> Draft | Rejected:
    try:
        proposal_file = directory / "proposal.toml"
        if proposal_file.is_symlink():
            return Rejected(Rejection.INVALID)
        proposal = tomllib.loads(proposal_file.read_text())
        if proposal.get("status") == "needs-input":
            return Rejected(Rejection.UNRESOLVED)
        name = proposal.get("name")
        if (
            set(proposal) != {"name", "status"}
            or proposal.get("status") != "ready"
            or not isinstance(name, str)
            or not install.valid_name(name)
        ):
            return Rejected(Rejection.INVALID)
        contents: list[bytes] = []
        for filename in ("task", "verify", "requirements.md"):
            source = directory / filename
            if source.is_symlink() or not source.is_file():
                return Rejected(Rejection.INVALID)
            content = source.read_bytes()
            _ = content.decode()
            contents.append(content)
        task, verification, requirements = contents
        lines = task.decode().splitlines()
        # Reject native marker aliases outside the declared single-header form.
        if any(
            re.match(r"^\s*(?:#|//|::)\s*(?:\[MISE\]|MISE)(?=\s|$)", line)
            and not line.startswith("#MISE ")
            for line in lines
        ):
            return Rejected(Rejection.INVALID)
        metadata = tomllib.loads(
            "\n".join(line.removeprefix("#MISE ") for line in lines if line.startswith("#MISE "))
        )
        if (
            not task.startswith(b"#!")
            or not verification.startswith(b"#!")
            or not requirements.strip()
            or set(metadata) != {"description"}
            or not isinstance(metadata["description"], str)
            or not metadata["description"].strip()
        ):
            return Rejected(Rejection.INVALID)
        interface_path = directory / "interface.json"
        if interface_path.is_symlink() or not interface_path.is_file():
            return Rejected(Rejection.INVALID)
        interface = interface_path.read_bytes()
        if Interface.model_validate_json(interface).name != name:
            return Rejected(Rejection.INVALID)
        return Draft(name, task, verification, requirements, interface)
    except (OSError, UnicodeError, tomllib.TOMLDecodeError, ValidationError):
        return Rejected(Rejection.INVALID)


def approved(question: str) -> bool:
    return ui.approved(question)


def isolated_environment(home: Path) -> dict[str, str]:
    # PATH selects installed executables. No inherited tokens or host HOME.
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_DATA_HOME": str(home / ".local/share"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "XDG_STATE_HOME": str(home / ".local/state"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "UV_NO_BUILD": "1",
        "UV_SYSTEM_CERTS": "1",
        "UV_NO_CONFIG": "1",
    }


def prepare_script_locks(
    draft: Draft, directory: Path
) -> _PreparedDraft | PreparationFailed | Rejected:
    locks: list[ScriptLock] = []
    with tempfile.TemporaryDirectory(prefix="ayi-lock-") as temporary:
        workspace = Path(temporary)
        home = workspace / "home"
        home.mkdir()
        for name, source in (
            (ScriptName.TASK, draft.task),
            (ScriptName.VERIFICATION, draft.verification),
        ):
            try:
                header = shlex.split(source.decode().splitlines()[0][2:])
                uses_uv = any(Path(word).name == "uv" for word in header)
                has_metadata = b"# /// script" in source.splitlines()
                if not uses_uv and not has_metadata:
                    continue
                if (
                    not uses_uv
                    or not {"run", "--locked", "--script"}.issubset(header)
                    or not has_metadata
                ):
                    return Rejected(Rejection.INVALID)
                script = workspace / name
                _ = script.write_bytes(source)
                destination = directory / f"{name}.lock"
                if destination.is_symlink() or (destination.exists() and not destination.is_file()):
                    return Rejected(Rejection.INVALID)
                existing = destination.read_bytes() if destination.exists() else None
                lock_path = workspace / destination.name
                if existing is not None:
                    _ = lock_path.write_bytes(existing)
                ui.status(f"Prepare script lock: {name}")
                result = subprocess.run(
                    [
                        "uv",
                        "lock",
                        "--script",
                        str(script),
                        "--no-build",
                        "--system-certs",
                        "--no-config",
                        *(["--check"] if existing is not None else []),
                    ],
                    cwd=workspace,
                    env=isolated_environment(home),
                    stdout=sys.stderr,
                    check=False,
                )
                if result.returncode:
                    return PreparationFailed(name, result.returncode)
                content = lock_path.read_bytes()
                _ = content.decode()
                if existing is None:
                    with destination.open("xb") as output:
                        _ = output.write(content)
                elif destination.is_symlink() or destination.read_bytes() != existing:
                    return Rejected(Rejection.CHANGED)
                if (directory / name).is_symlink() or (directory / name).read_bytes() != source:
                    return Rejected(Rejection.CHANGED)
                locks.append(ScriptLock(name, content))
            except (OSError, UnicodeError, ValueError):
                return Rejected(Rejection.INVALID)
    return _PreparedDraft(draft, tuple(locks))


def verify(prepared: _PreparedDraft) -> _VerifiedDraft | VerificationFailed:
    draft = prepared.draft
    with tempfile.TemporaryDirectory(prefix="ayi-verify-") as temporary:
        directory = Path(temporary)
        task = directory / "task"
        verification = directory / "verify"
        interface_path = directory / "interface.json"
        inputs = {
            task: draft.task,
            verification: draft.verification,
            interface_path: draft.interface,
            **{directory / lock.filename: lock.content for lock in prepared.locks},
        }
        for path, content in inputs.items():
            _ = path.write_bytes(content)
        task.chmod(0o755)
        verification.chmod(0o755)
        home = directory / "home"
        home.mkdir()
        env = isolated_environment(home)
        if b"bash" in draft.task.splitlines()[0]:
            for args in (["bash", "-n", str(task)], ["shellcheck", "-S", "warning", str(task)]):
                result = subprocess.run(
                    args, cwd=directory, env=env, stdout=sys.stderr, check=False
                )
                if result.returncode:
                    return VerificationFailed(result.returncode)
        result = subprocess.run(
            [str(verification), str(task), str(interface_path)],
            cwd=directory,
            env=env,
            stdout=sys.stderr,
            check=False,
        )
        if any(
            not path.is_file() or path.is_symlink() or path.read_bytes() != content
            for path, content in inputs.items()
        ):
            print(f"ayi: {Rejection.CHANGED}", file=sys.stderr)
            return VerificationFailed(65)
        if result.returncode:
            return VerificationFailed(result.returncode)
        interface = Interface.model_validate_json(draft.interface)
        invalid = "--ayi-invalid-option"
        declared = {name for option in interface.options for name in option.names}
        while invalid in declared:
            invalid += "-x"
        for flag in ("--help", "-h", invalid):
            result = subprocess.run(
                [str(task), flag], cwd=directory, env=env, capture_output=True, check=False
            )
            if any(
                not path.is_file() or path.is_symlink() or path.read_bytes() != content
                for path, content in inputs.items()
            ):
                print(f"ayi: {Rejection.CHANGED}", file=sys.stderr)
                return VerificationFailed(65)
            if flag == invalid:
                if result.returncode == 0:
                    print(
                        "ayi: tool must reject unknown options with a nonzero exit", file=sys.stderr
                    )
                    return VerificationFailed(65)
            else:
                if result.returncode or not result.stdout.strip():
                    print(
                        f"ayi: tool must provide nonempty {flag} output and exit 0", file=sys.stderr
                    )
                    return VerificationFailed(65)
                help_text = result.stdout.decode(errors="replace")
                missing = [
                    name
                    for option in interface.options
                    for name in option.names
                    if not re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", help_text)
                ]
                if missing:
                    print(
                        f"ayi: declared options missing from {flag}: {', '.join(missing)}",
                        file=sys.stderr,
                    )
                    return VerificationFailed(65)
        return _VerifiedDraft(prepared)


def git_arguments(*arguments: str) -> list[str]:
    return ["git", "-C", str(REPO), *arguments]


def register(
    verified: _VerifiedDraft, original: Original | None = None, *, force: bool = False
) -> int:
    draft = verified.prepared.draft
    link = install.command_link(draft.name)
    if link.state == install.LinkState.CONFLICT and (
        not force or not (link.destination.is_file() or link.destination.is_symlink())
    ):
        print(
            f"ayi: installation conflict: {link.destination}; --force can replace files or symlinks, never directories",
            file=sys.stderr,
        )
        return 65
    destinations = command_paths(draft.name)
    files = dict(
        zip(
            destinations[:4],
            (draft.task, draft.verification, draft.requirements, draft.interface),
            strict=True,
        )
    )
    for lock in verified.prepared.locks:
        match lock.script:
            case ScriptName.TASK:
                destination = destinations[4]
            case ScriptName.VERIFICATION:
                destination = destinations[5]
        files[destination] = lock.content
    if original is not None and (
        draft.name != original.name or read_original(original.name) != original
    ):
        print("ayi: command changed during modification; no files replaced", file=sys.stderr)
        return 75
    if original is None and any(path.exists() or path.is_symlink() for path in destinations):
        print(f"ayi: {Rejection.EXISTS}", file=sys.stderr)
        return 65
    paths = [str(path.relative_to(REPO)) for path in destinations]
    tracked = subprocess.run(
        git_arguments("ls-files", "--cached", "--with-tree=HEAD", "--", *paths),
        stdout=subprocess.PIPE,
        check=False,
    )
    if tracked.returncode:
        return tracked.returncode
    if original is None and tracked.stdout:
        print(f"ayi: {Rejection.EXISTS}", file=sys.stderr)
        return 65
    removed = [
        path
        for path in destinations
        if path not in files and original is not None and original.files[path] is not None
    ]
    paths = [str(path.relative_to(REPO)) for path in (*files, *removed)]
    written: list[Path] = []
    committed = False
    linked = False
    backup: Path | None = None
    try:
        for path in removed:
            path.unlink()
            written.append(path)
        for path, content in files.items():
            path.parent.mkdir(exist_ok=True)
            with path.open("wb" if original is not None else "xb") as output:
                written.append(path)
                _ = output.write(content)
            path.chmod(0o644 if path.suffix in (".md", ".json", ".lock") else 0o755)
        if link.state != install.LinkState.OWNED:
            link.destination.parent.mkdir(parents=True, exist_ok=True)
            if link.state == install.LinkState.CONFLICT:
                backup = (
                    Path(tempfile.mkdtemp(prefix=".ayi-backup-", dir=link.destination.parent))
                    / draft.name
                )
                _ = link.destination.rename(backup)
            link.destination.symlink_to(link.source)
            linked = True
        added = subprocess.run(git_arguments("add", "--", *paths), stdout=sys.stderr, check=False)
        if added.returncode:
            return added.returncode
        result = subprocess.run(
            git_arguments(
                "commit",
                "--only",
                "-m",
                f"feat: {'modify' if original else 'add'} ayi command {draft.name}",
                "--",
                *paths,
            ),
            stdout=sys.stderr,
            check=False,
        )
        committed = result.returncode == 0
        return result.returncode
    finally:
        if not committed:
            if linked and install.command_link(draft.name).state == install.LinkState.OWNED:
                link.destination.unlink()
            if backup is not None and (backup.exists() or backup.is_symlink()):
                if link.destination.exists() or link.destination.is_symlink():
                    print(
                        f"ayi: installation target changed; original preserved at {backup}",
                        file=sys.stderr,
                    )
                else:
                    _ = backup.rename(link.destination)
            _ = subprocess.run(git_arguments("reset", "-q", "--", *paths), check=False)
            for path in written:
                previous = original.files[path] if original is not None else None
                if previous is None:
                    path.unlink()
                else:
                    content, mode = previous
                    _ = path.write_bytes(content)
                    path.chmod(mode)
        if backup is not None:
            if committed and (backup.exists() or backup.is_symlink()):
                backup.unlink()
            if not any(backup.parent.iterdir()):
                backup.parent.rmdir()


class Bundle(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    proposal: str
    requirements: str
    task: str
    verification: str
    interface: str


def new_directory(parent: Path) -> Path:
    parent.mkdir(exist_ok=True, mode=0o700)
    return Path(tempfile.mkdtemp(prefix="command-", dir=parent))


def generate(request: str, directory: Path, implementation: Implementation) -> int:
    harness = implementation.harness
    if shutil.which(harness) is None:
        print(f"ayi: missing dependency: {harness}", file=sys.stderr)
        return 69
    ui.status(f"Proposed draft: {directory}")
    model_args = ["--model", implementation.model] if implementation.model is not None else []
    prompt = f"Request: {request}\nDraft directory: {directory}\nRegistry: {ROOT / 'tasks'}"
    if harness == "pi":
        arguments = [
            "pi",
            "--print",
            "--no-session",
            "--no-extensions",
            "--no-mcp",
            "--no-skills",
            "--no-context-files",
            "--no-prompt-templates",
            "--offline",
            "--tools",
            "read,write,edit",
            "--append-system-prompt",
            str(ROOT / "prompts/create.md"),
            *model_args,
            "--",
            prompt,
        ]
    else:
        schema = directory / "output-schema.json"
        _ = schema.write_text(json.dumps(Bundle.model_json_schema()))
        prompt = (
            (ROOT / "prompts/create.md").read_text()
            + "\n"
            + prompt
            + "\nFor this harness, return the five file contents as JSON strings instead of writing files. "
            "Use keys proposal, requirements, task, verification, interface. "
            "Do not invoke tools or execute code. The caller writes and reviews these files."
        )
        arguments = [
            "codex",
            "exec",
            "--ignore-user-config",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--color",
            "never",
            "--output-schema",
            str(schema),
            "--output-last-message",
            str(directory / "bundle.json"),
            *model_args,
            "--",
            prompt,
        ]
    log_path = directory / "generation.log"
    with (
        log_path.open("w", encoding="utf-8") as log,
        ui.activity(f"Implementation: {harness} / {implementation.model or 'default'}"),
    ):
        log_path.chmod(0o600)
        result = subprocess.run(
            arguments, cwd=directory, stdin=subprocess.DEVNULL, stdout=log, stderr=log, check=False
        )
    if result.returncode:
        ui.status(f"Implementation failed: exit {result.returncode}. Details: {log_path}")
        return result.returncode
    if harness == "codex":
        try:
            bundle = Bundle.model_validate_json((directory / "bundle.json").read_bytes())
        except (OSError, ValidationError) as error:
            ui.status(
                f"Invalid implementation output. Details: {log_path} ({type(error).__name__})"
            )
            return 65
        for filename, content in (
            ("proposal.toml", bundle.proposal),
            ("requirements.md", bundle.requirements),
            ("task", bundle.task),
            ("verify", bundle.verification),
            ("interface.json", bundle.interface),
        ):
            _ = (directory / filename).write_text(content)
    return 0


def main(
    arguments: list[str],
    *,
    name: str | None = None,
    original: Original | None = None,
    force: bool = False,
) -> int:
    if len(arguments) != 1 or not arguments[0].strip():
        print('ayi: usage: ayi new <name> "<description>"', file=sys.stderr)
        return 64
    directory = new_directory(ROOT / "drafts")
    status = generate(arguments[0], directory, Implementation())
    if status:
        return status
    return finish(directory, name=name, original=original, force=force)


def finish(
    directory: Path,
    *,
    name: str | None = None,
    original: Original | None = None,
    force: bool = False,
) -> int:
    draft = read_draft(directory)
    if isinstance(draft, Rejected):
        print(f"ayi: {draft.reason}. Draft: {directory}", file=sys.stderr)
        return 65
    if name is not None and draft.name != name:
        print(f"ayi: draft changed the agreed command name. Draft: {directory}", file=sys.stderr)
        return 65
    prepared = prepare_script_locks(draft, directory)
    if isinstance(prepared, Rejected):
        print(f"ayi: {prepared.reason}. Draft: {directory}", file=sys.stderr)
        return 65
    if isinstance(prepared, PreparationFailed):
        print(
            f"ayi: script lock failed: {prepared.script}, exit {prepared.status}. Draft: {directory}",
            file=sys.stderr,
        )
        return prepared.status
    ui.status(f"Generated: {draft.name} (not yet verified or installed)")
    ui.review("Requirements", draft.requirements)
    for label, filename, content in (
        ("Task", "task", draft.task),
        ("Verification", "verify", draft.verification),
        ("Interface", "interface.json", draft.interface),
    ):
        ui.status(f"{label}: {directory / filename} ({len(content)} bytes)")
    for lock in prepared.locks:
        label = "Task lock" if lock.script == ScriptName.TASK else "Verification lock"
        ui.status(f"{label}: {directory / lock.filename} ({len(lock.content)} bytes)")
    ui.status("Examine the draft files before approval. Source stays in these files.")
    if not approved("Run this verification?"):
        return 1
    ui.status("Verifying reviewed draft...")
    verified = verify(prepared)
    if isinstance(verified, VerificationFailed):
        print(
            f"ayi: verification failed: exit {verified.status}. Draft: {directory}", file=sys.stderr
        )
        return verified.status
    ui.status("Verification passed.")
    destination = install.command_link(draft.name).destination
    if not approved(
        f"Commit and register {draft.name} in {REPO}, and {'replace' if force else 'install'} {destination}?"
    ):
        return 1
    ui.status("Registering verified draft...")
    status = register(verified, original, force=force)
    if status == 0:
        print(f"Installed: {destination}\nInvocation: {draft.name} [args]")
        if str(destination.parent) not in os.get_exec_path():
            print(
                f"ayi: add {destination.parent} to PATH to invoke {draft.name} by name",
                file=sys.stderr,
            )
    return status


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except OSError as error:
        print(f"ayi: local operation failed: {error.strerror}", file=sys.stderr)
        raise SystemExit(74) from error
    except KeyboardInterrupt:
        raise SystemExit(130) from None
