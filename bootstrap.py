#!/usr/bin/env -S uv run --system-certs --locked --script
# /// script
# requires-python = ">=3.12,<3.14"
# dependencies = []
# [tool.uv]
# no-build = true
# required-version = ">=0.12.9"
# [[tool.uv.index]]
# name = "pypi"
# url = "https://pypi.org/simple"
# default = true
# ///
"""Install an Ayi source archive and preserve its local command registry."""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from collections.abc import Callable, Mapping
from contextlib import ExitStack
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import TypeGuard

API = "https://api.github.com/repos/amichne/ayi/commits/main"
MAX_SOURCE = 32 * 1024 * 1024
MARKER = ".ayi-source"
CURL = "/usr/bin/curl" if sys.platform == "darwin" else "curl"


class InstallError(Exception):
    def __init__(self, status: int, message: str) -> None:
        if status == 0:
            raise ValueError("an installation failure needs a nonzero status")
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass(frozen=True)
class _Revision:
    sha: str


@dataclass(frozen=True)
class _Locations:
    root: Path
    parent: Path


@dataclass(frozen=True)
class DownloadFailed:
    url: str
    reason: str


class _Absent(Enum):
    FILE = "absent"


@dataclass(frozen=True)
class _File:
    content: bytes
    executable: bool


@dataclass(frozen=True)
class _Change:
    relative: Path
    before: _File | _Absent
    after: _File | _Absent


def mapping(value: object) -> TypeGuard[dict[str, object]]:
    return isinstance(value, dict)


def revision(value: object) -> _Revision:
    if mapping(value):
        sha = value.get("sha")
        if isinstance(sha, str) and re.fullmatch(r"[a-f0-9]{40}", sha):
            return _Revision(sha)
    raise InstallError(69, "GitHub did not supply a source commit")


def latest(fetch: Callable[[str], bytes | DownloadFailed]) -> _Revision:
    metadata = source(API, fetch)
    try:
        return revision(json.loads(metadata))
    except (json.JSONDecodeError, UnicodeError) as error:
        raise InstallError(69, f"source commit response is invalid: {API}") from error


def owned_revision(root: Path) -> _Revision:
    marker = root / MARKER
    if (
        root.is_symlink()
        or marker.is_symlink()
        or not marker.is_file()
        or (root / ".git").is_symlink()
        or not (root / ".git").is_dir()
        or re.fullmatch(rb"[a-f0-9]{40}\n", marker.read_bytes()) is None
    ):
        raise InstallError(
            65, f"existing source stays unchanged: {root}. Update developer checkouts with Git."
        )
    return _Revision(marker.read_text().strip())


def git_environment(env: Mapping[str, str]) -> dict[str, str]:
    return {
        key: value
        for key, value in env.items()
        if not key.startswith("GIT_")
        or key in {"GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_NOSYSTEM"}
    } | {"GIT_OPTIONAL_LOCKS": "0"}


def dependencies(env: Mapping[str, str]) -> None:
    for tool in ("uv", "git", "mise", CURL):
        if shutil.which(tool, path=env.get("PATH", "")) is None:
            raise InstallError(69, f"missing dependency: {tool}")


def locations(env: Mapping[str, str]) -> _Locations:
    home = Path(env.get("HOME", ""))
    data = Path(env.get("XDG_DATA_HOME", str(home / ".local/share")))
    config = Path(env.get("XDG_CONFIG_HOME", str(home / ".config")))
    if not all(path.is_absolute() for path in (home, data, config)):
        raise InstallError(65, "HOME and XDG paths must be absolute")
    parent = data / "ayi"
    return _Locations(parent / "source", parent)


def download(url: str) -> bytes | DownloadFailed:
    # Apple's curl uses Keychain trust, unlike uv-managed Python's OpenSSL store.
    with tempfile.TemporaryDirectory(prefix="ayi-source-") as temporary:
        output = Path(temporary) / "response"
        result = subprocess.run(
            [
                CURL,
                "--disable",
                "--fail",
                "--silent",
                "--show-error",
                "--location",
                "--proto",
                "=https",
                "--proto-redir",
                "=https",
                "--max-time",
                "30",
                "--max-filesize",
                str(MAX_SOURCE),
                "--user-agent",
                "ayi-installer",
                "--output",
                str(output),
                "--write-out",
                "%{http_code}",
                url,
            ],
            env=os.environ,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 63:
            raise InstallError(65, "source response exceeds 32 MiB")
        if result.returncode:
            reason = (
                f"HTTP {result.stdout.strip()}"
                if result.returncode == 22
                else result.stderr.strip() or f"curl exited with status {result.returncode}"
            )
            return DownloadFailed(url, reason)
        with output.open("rb") as response:
            content = response.read(MAX_SOURCE + 1)
    if len(content) > MAX_SOURCE:
        raise InstallError(65, "source response exceeds 32 MiB")
    return content


def source(url: str, fetch: Callable[[str], bytes | DownloadFailed]) -> bytes:
    result = fetch(url)
    if isinstance(result, DownloadFailed):
        reason = "".join(char if char.isprintable() else " " for char in result.reason)[:300]
        raise InstallError(69, f"source download failed: {result.url}: {reason}")
    return result


def unpack(content: bytes, selected: _Revision, root: Path) -> None:
    with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as archive:
        members: list[tarfile.TarInfo] = []
        total = 0
        for member in archive.getmembers():
            name = PurePosixPath(member.name)
            if (
                name.is_absolute()
                or not name.parts
                or name.parts[0] != f"ayi-{selected.sha}"
                or ".." in name.parts
                or ".git" in name.parts
                or (len(name.parts) > 1 and name.parts[1] in {MARKER, ".venv"})
                or not (member.isfile() or member.isdir())
            ):
                raise InstallError(65, "source archive contains an invalid entry")
            if len(name.parts) == 1:
                continue
            total += member.size
            if total > MAX_SOURCE:
                raise InstallError(65, "source archive exceeds 32 MiB")
            members.append(member.replace(name=str(PurePosixPath(*name.parts[1:]))))
        archive.extractall(root, members=members, filter="data")
    for name in ("bin/ayi", "install.py", "mise.toml", "pyproject.toml", "uv.lock"):
        if not (root / name).is_file():
            raise InstallError(65, f"source archive lacks {name}")


def run(arguments: list[str], target: _Locations, env: Mapping[str, str]) -> None:
    result = subprocess.run(arguments, cwd=target.parent, env=env, check=False)
    if result.returncode:
        raise InstallError(result.returncode, f"{arguments[0]} failed")


def apply(target: _Locations, env: Mapping[str, str]) -> None:
    run(
        ["uv", "sync", "--locked", "--no-dev", "--no-build", "--project", str(target.root)],
        target,
        env,
    )
    run(
        [
            "uv",
            "run",
            "--locked",
            "--no-dev",
            "--no-build",
            "--project",
            str(target.root),
            "python",
            str(target.root / "install.py"),
            "apply",
        ],
        target,
        env,
    )


def install(
    target: _Locations,
    env: Mapping[str, str],
    fetch: Callable[[str], bytes | DownloadFailed] = download,
) -> None:
    dependencies(env)
    if target.root.exists() or target.root.is_symlink():
        _ = owned_revision(target.root)
        apply(target, env)
        return
    selected = latest(fetch)
    content = source(f"https://github.com/amichne/ayi/archive/{selected.sha}.tar.gz", fetch)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.root.mkdir()
    with ExitStack() as rollback:
        _ = rollback.callback(shutil.rmtree, target.root)
        unpack(content, selected, target.root)
        _ = (target.root / MARKER).write_text(selected.sha + "\n")
        git_env = git_environment(env)
        run(["git", "init", "-q", str(target.root)], target, git_env)
        run(["git", "-C", str(target.root), "add", "--", "."], target, git_env)
        run(
            [
                "git",
                "-C",
                str(target.root),
                "-c",
                "user.name=Ayi installer",
                "-c",
                "user.email=installer@ayi.invalid",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "-qm",
                f"chore: seed Ayi from {selected.sha}",
            ],
            target,
            git_env,
        )
        apply(target, env)
        _ = rollback.pop_all()


def git(root: Path, env: Mapping[str, str], *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments], env=env, capture_output=True, text=True, check=False
    )
    if result.returncode:
        raise InstallError(result.returncode, f"update Git step failed: {arguments[0]}")
    return result.stdout.strip()


def clean_head(root: Path, env: Mapping[str, str]) -> str:
    if Path(git(root, env, "rev-parse", "--show-toplevel")).resolve() != root.resolve():
        raise InstallError(65, "registry root differs; source stays unchanged")
    if git(root, env, "status", "--porcelain", "--untracked-files=all"):
        raise InstallError(
            65,
            "registry has local changes; source stays unchanged. Commit or move them before ayi update.",
        )
    return git(root, env, "rev-parse", "HEAD")


def file_state(root: Path, relative: Path) -> _File | _Absent:
    path = root / relative
    if path.is_symlink() or any(
        (root / parent).is_symlink() or ((root / parent).exists() and not (root / parent).is_dir())
        for parent in relative.parents
    ):
        raise InstallError(65, f"source path conflicts; source stays unchanged: {relative}")
    if not path.exists():
        return _Absent.FILE
    if not path.is_file():
        raise InstallError(65, f"source path conflicts; source stays unchanged: {relative}")
    return _File(path.read_bytes(), bool(path.stat().st_mode & stat.S_IXUSR))


def changes(old: Path, new: Path, root: Path) -> tuple[_Change, ...]:
    paths = {
        file.relative_to(tree) for tree in (old, new) for file in tree.rglob("*") if file.is_file()
    }
    result: list[_Change] = []
    for relative in sorted(paths):
        before, after = file_state(old, relative), file_state(new, relative)
        if before != after:
            if file_state(root, relative) != before:
                raise InstallError(
                    65, f"local source conflicts; source stays unchanged: {relative}"
                )
            result.append(_Change(relative, before, after))
    return tuple(result)


def write_state(root: Path, relative: Path, state: _File | _Absent) -> None:
    path = root / relative
    match state:
        case _Absent.FILE:
            path.unlink(missing_ok=True)
        case _File(content, executable):
            path.parent.mkdir(parents=True, exist_ok=True)
            _ = path.write_bytes(content)
            path.chmod(0o755 if executable else 0o644)


def update(
    root: Path, env: Mapping[str, str], fetch: Callable[[str], bytes | DownloadFailed]
) -> None:
    current = owned_revision(root)
    dependencies(env)
    git_env = git_environment(env)
    head = clean_head(root, git_env)
    selected = latest(fetch)
    target = _Locations(root, root.parent)
    if selected == current:
        apply(target, git_env)
        print(f"Ayi is up to date ({current.sha[:12]}).")
        return
    with tempfile.TemporaryDirectory(prefix="ayi-update-") as temporary:
        old, new = Path(temporary) / "old", Path(temporary) / "new"
        for revision_, tree in ((current, old), (selected, new)):
            content = source(
                f"https://github.com/amichne/ayi/archive/{revision_.sha}.tar.gz", fetch
            )
            unpack(content, revision_, tree)
        planned = (
            *changes(old, new, root),
            _Change(
                Path(MARKER),
                _File((current.sha + "\n").encode(), False),
                _File((selected.sha + "\n").encode(), False),
            ),
        )
        if clean_head(root, git_env) != head:
            raise InstallError(65, "registry changed during download; source stays unchanged")
        for change in planned:
            if file_state(root, change.relative) != change.before:
                raise InstallError(
                    65, f"source changed during download; source stays unchanged: {change.relative}"
                )
        paths = [str(change.relative) for change in planned]
        with ExitStack() as rollback:
            # Restore only this operation's files and index entries if the commit fails.
            _ = rollback.callback(git, root, git_env, "reset", "-q", "HEAD", "--", *paths)
            for change in planned:
                _ = rollback.callback(write_state, root, change.relative, change.before)
                write_state(root, change.relative, change.after)
            _ = git(root, git_env, "add", "--", *paths)
            _ = git(
                root,
                git_env,
                "-c",
                "user.name=Ayi installer",
                "-c",
                "user.email=installer@ayi.invalid",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "-qm",
                f"chore: update Ayi to {selected.sha}",
                "--only",
                "--",
                *paths,
            )
            _ = rollback.pop_all()
    try:
        apply(target, git_env)
    except InstallError as error:
        raise InstallError(
            error.status,
            f"source updated to {selected.sha[:12]}, but setup failed. Run ayi update again. {error.message}",
        ) from error
    print(f"Updated Ayi to {selected.sha[:12]}.")


def version(root: Path) -> str:
    with (root / "pyproject.toml").open("rb") as project_file:
        project: object = tomllib.load(project_file).get("project")
    value = project.get("version") if mapping(project) else None
    if not isinstance(value, str) or re.fullmatch(r"[0-9][a-zA-Z0-9.+-]*", value) is None:
        raise InstallError(65, "project version is invalid")
    result = f"ayi {value}"
    if (root / MARKER).exists():
        result += f" ({owned_revision(root).sha[:12]})"
    return result


def launcher(source: str) -> str:
    return (
        "#!/bin/sh\n# Generated from bootstrap.py. Do not edit this file.\n"
        "exec uv run --system-certs --no-project --no-build --python 3.12 python - \"$@\" <<'AYI_BOOTSTRAP'\n"
        + source
        + "\nAYI_BOOTSTRAP\n"
    )


def main(arguments: list[str]) -> int:
    if arguments not in ([], ["update"], ["--version"], ["-V"]):
        print("ayi: usage: ayi update | ayi --version | ayi -V", file=sys.stderr)
        return 64
    try:
        root = (
            locations(os.environ).root if __file__ == "<stdin>" else Path(__file__).resolve().parent
        )
        if arguments == ["update"]:
            update(root, os.environ, download)
        elif arguments:
            print(version(root))
        else:
            install(locations(os.environ), os.environ)
    except InstallError as error:
        print(f"ayi: {error.message}", file=sys.stderr)
        return error.status
    except tarfile.TarError:
        print("ayi: source archive is invalid", file=sys.stderr)
        return 65
    except tomllib.TOMLDecodeError:
        print("ayi: project metadata is invalid", file=sys.stderr)
        return 65
    except OSError as error:
        print(f"ayi: installation failed: {error.strerror}", file=sys.stderr)
        return 74
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
