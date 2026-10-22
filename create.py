from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

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


@dataclass(frozen=True)
class _VerifiedDraft:
    draft: Draft


@dataclass(frozen=True)
class VerificationFailed:
    status: int


@dataclass(frozen=True)
class Original:
    name: str
    files: dict[Path, tuple[bytes, int] | None]


def command_paths(name: str) -> tuple[Path, Path, Path]:
    return (
        ROOT / "tasks" / name,
        ROOT / "verification" / name,
        ROOT / "requirements" / f"{name}.md",
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
            or re.fullmatch(r"[a-z][a-z0-9-]{0,47}", name) is None
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
        return Draft(name, task, verification, requirements)
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        return Rejected(Rejection.INVALID)


def approved(question: str) -> bool:
    try:
        return input(f"{question} [y/N] ").strip().lower() == "y"
    except EOFError:
        return False


def verify(draft: Draft) -> _VerifiedDraft | VerificationFailed:
    with tempfile.TemporaryDirectory(prefix="ayi-verify-") as temporary:
        directory = Path(temporary)
        task = directory / "task"
        verification = directory / "verify"
        _ = task.write_bytes(draft.task)
        _ = verification.write_bytes(draft.verification)
        task.chmod(0o755)
        verification.chmod(0o755)
        home = directory / "home"
        home.mkdir()
        # PATH selects installed executables. No inherited tokens or host HOME.
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_DATA_HOME": str(home / ".local/share"),
            "XDG_CACHE_HOME": str(home / ".cache"),
            "XDG_STATE_HOME": str(home / ".local/state"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        }
        if b"bash" in draft.task.splitlines()[0]:
            for args in (["bash", "-n", str(task)], ["shellcheck", "-S", "warning", str(task)]):
                result = subprocess.run(args, cwd=directory, env=env, check=False)
                if result.returncode:
                    return VerificationFailed(result.returncode)
        result = subprocess.run([str(verification), str(task)], cwd=directory, env=env, check=False)
        if task.read_bytes() != draft.task or verification.read_bytes() != draft.verification:
            print(f"ayi: {Rejection.CHANGED}", file=sys.stderr)
            return VerificationFailed(65)
        if result.returncode:
            return VerificationFailed(result.returncode)
        return _VerifiedDraft(draft)


def git_arguments(*arguments: str) -> list[str]:
    return ["git", "-C", str(REPO), *arguments]


def register(verified: _VerifiedDraft, original: Original | None = None) -> int:
    draft = verified.draft
    files = dict(
        zip(
            command_paths(draft.name),
            (draft.task, draft.verification, draft.requirements),
            strict=True,
        )
    )
    if original is not None and (
        draft.name != original.name or read_original(original.name) != original
    ):
        print("ayi: command changed during modification; no files replaced", file=sys.stderr)
        return 75
    if original is None and any(path.exists() or path.is_symlink() for path in files):
        print(f"ayi: {Rejection.EXISTS}", file=sys.stderr)
        return 65
    paths = [str(path.relative_to(REPO)) for path in files]
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
    written: list[Path] = []
    committed = False
    try:
        for path, content in files.items():
            path.parent.mkdir(exist_ok=True)
            with path.open("wb" if original is not None else "xb") as output:
                written.append(path)
                _ = output.write(content)
            path.chmod(0o644 if path.suffix == ".md" else 0o755)
        added = subprocess.run(git_arguments("add", "--", *paths), check=False)
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
            check=False,
        )
        committed = result.returncode == 0
        return result.returncode
    finally:
        if not committed:
            _ = subprocess.run(git_arguments("reset", "-q", "--", *paths), check=False)
            for path in written:
                previous = original.files[path] if original is not None else None
                if previous is None:
                    path.unlink()
                else:
                    content, mode = previous
                    _ = path.write_bytes(content)
                    path.chmod(mode)


def main(
    arguments: list[str],
    *,
    interactive: bool = True,
    name: str | None = None,
    original: Original | None = None,
) -> int:
    if len(arguments) != 1 or not arguments[0].strip():
        print('ayi: usage: ayi create "<description>"', file=sys.stderr)
        return 64
    if shutil.which("pi") is None:
        print("ayi: missing dependency: pi", file=sys.stderr)
        return 69
    drafts = ROOT / "drafts"
    drafts.mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="command-", dir=drafts))
    print(f"Draft: {directory}", flush=True)
    result = subprocess.run(
        [
            "pi",
            *([] if interactive else ["--print"]),
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
            "--",
            f"Request: {arguments[0]}\nDraft directory: {directory}\nRegistry: {ROOT / 'tasks'}",
        ],
        cwd=directory,
        stdin=None if interactive else subprocess.DEVNULL,
        check=False,
    )
    if result.returncode:
        return result.returncode
    draft = read_draft(directory)
    if isinstance(draft, Rejected):
        print(f"ayi: {draft.reason}. Draft: {directory}", file=sys.stderr)
        return 65
    if name is not None and draft.name != name:
        print(f"ayi: draft changed the agreed command name. Draft: {directory}", file=sys.stderr)
        return 65
    for label, content in (
        ("Requirements", draft.requirements),
        ("Task", draft.task),
        ("Verification", draft.verification),
    ):
        print(f"\n{label}:\n{content.decode()}")
    if not approved("Run this verification?"):
        return 1
    verified = verify(draft)
    if isinstance(verified, VerificationFailed):
        print(
            f"ayi: verification failed: exit {verified.status}. Draft: {directory}", file=sys.stderr
        )
        return verified.status
    print("Verification passed.")
    if not approved(f"Commit and register {draft.name} in {REPO}?"):
        return 1
    status = register(verified, original)
    if status == 0:
        print(f"Invocation: ayi run {draft.name} [args]")
    return status


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except OSError as error:
        print(f"ayi: local operation failed: {error.strerror}", file=sys.stderr)
        raise SystemExit(74) from error
    except KeyboardInterrupt:
        raise SystemExit(130) from None
