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
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Callable, Mapping
from contextlib import ExitStack
from dataclasses import dataclass
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


def mapping(value: object) -> TypeGuard[dict[str, object]]:
    return isinstance(value, dict)


def revision(value: object) -> _Revision:
    if mapping(value):
        sha = value.get("sha")
        if isinstance(sha, str) and re.fullmatch(r"[a-f0-9]{40}", sha):
            return _Revision(sha)
    raise InstallError(69, "GitHub did not supply a source commit")


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
    for tool in ("uv", "git", "mise", CURL):
        if shutil.which(tool, path=env.get("PATH", "")) is None:
            raise InstallError(69, f"missing dependency: {tool}")
    if target.root.exists() or target.root.is_symlink():
        marker = target.root / MARKER
        if (
            target.root.is_symlink()
            or marker.is_symlink()
            or not marker.is_file()
            or not (target.root / ".git").is_dir()
            or re.fullmatch(rb"[a-f0-9]{40}\n", marker.read_bytes()) is None
        ):
            raise InstallError(65, f"existing source stays unchanged: {target.root}")
        apply(target, env)
        return
    metadata = source(API, fetch)
    try:
        selected = revision(json.loads(metadata))
    except (json.JSONDecodeError, UnicodeError) as error:
        raise InstallError(69, f"source commit response is invalid: {API}") from error
    content = source(f"https://github.com/amichne/ayi/archive/{selected.sha}.tar.gz", fetch)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.root.mkdir()
    with ExitStack() as rollback:
        _ = rollback.callback(shutil.rmtree, target.root)
        unpack(content, selected, target.root)
        _ = (target.root / MARKER).write_text(selected.sha + "\n")
        git_env = {
            key: value
            for key, value in env.items()
            if not key.startswith("GIT_")
            or key in {"GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_NOSYSTEM"}
        }
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


def launcher(source: str) -> str:
    return (
        "#!/bin/sh\n# Generated from bootstrap.py. Do not edit this file.\n"
        "exec uv run --system-certs --no-project --no-build --python 3.12 python - \"$@\" <<'AYI_BOOTSTRAP'\n"
        + source
        + "\nAYI_BOOTSTRAP\n"
    )


def main(arguments: list[str]) -> int:
    if arguments:
        print("ayi: usage: install.sh", file=sys.stderr)
        return 64
    try:
        install(locations(os.environ), os.environ)
    except InstallError as error:
        print(f"ayi: {error.message}", file=sys.stderr)
        return error.status
    except tarfile.TarError:
        print("ayi: source archive is invalid", file=sys.stderr)
        return 65
    except OSError as error:
        print(f"ayi: installation failed: {error.strerror}", file=sys.stderr)
        return 74
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
