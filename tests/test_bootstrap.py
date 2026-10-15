from __future__ import annotations

import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from bootstrap import API, InstallError, install, locations

from .conftest import Shell, copy_runtime, init_git

SHA = "a" * 40


def source_archive(shell: Shell, bad: str = "") -> bytes:
    copy_runtime(shell, "install.py", "create.py", ".gitignore")
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for file in shell.root.rglob("*"):
            if file.is_file():
                archive.add(file, arcname=f"ayi-{SHA}/{file.relative_to(shell.root)}")
        if bad:
            item = tarfile.TarInfo(bad)
            archive.addfile(item)
    return output.getvalue()


def test_archive_installs_a_git_registry_and_preserves_registered_commands(shell: Shell) -> None:
    content = source_archive(shell)
    target = locations(shell.env)

    def fetch(url: str) -> bytes:
        return json.dumps({"sha": SHA}).encode() if url == API else content

    install(target, shell.env, fetch)
    assert (target.root / ".git").is_dir()
    assert (target.root / ".ayi-source").read_text() == SHA + "\n"
    installed = Path(shell.env["HOME"]) / ".local/bin/ayi"
    result = subprocess.run(
        [str(installed), "list"], env=shell.env, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert "Fixture one" in result.stdout
    for key, value in (("user.name", "Acceptance"), ("user.email", "test@example.invalid")):
        result = subprocess.run(
            ["git", "-C", str(target.root), "config", key, value], env=shell.env, check=False
        )
        assert result.returncode == 0
    script = (
        "import create\n"
        "draft = create.Draft('added', b'#!/bin/bash\\n#MISE description=\\\"Added\\\"\\necho added\\n', b'#!/bin/bash\\nexit 0\\n', b'Added command\\n')\n"
        "verified = create.verify(draft)\n"
        "assert not isinstance(verified, create.VerificationFailed)\n"
        "raise SystemExit(create.register(verified))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=target.root,
        env=shell.env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    before = subprocess.check_output(
        ["git", "-C", str(target.root), "rev-parse", "HEAD"], env=shell.env
    )

    def no_download(_url: str) -> bytes:
        pytest.fail("a repeated installation must keep the existing registry")

    install(target, shell.env, no_download)
    assert (
        subprocess.check_output(["git", "-C", str(target.root), "rev-parse", "HEAD"], env=shell.env)
        == before
    )
    assert (target.root / "tasks/added").read_text().endswith("echo added\n")
    assert not (Path(shell.env["HOME"]) / ".zshrc").exists()


def test_conflicting_source_is_preserved_without_a_download(shell: Shell) -> None:
    target = locations(shell.env)
    target.root.mkdir(parents=True)
    file = target.root / "keep"
    _ = file.write_text("existing data")

    def no_download(_url: str) -> bytes:
        pytest.fail("a conflicting source must prevent download")

    with pytest.raises(InstallError) as result:
        install(target, shell.env, no_download)
    assert result.value.status == 65
    assert file.read_text() == "existing data"


@pytest.mark.parametrize(
    "bad", ["../outside", f"ayi-{SHA}/../../outside", f"ayi-{SHA}/.git/config"]
)
def test_invalid_archive_creates_no_registry_or_links(shell: Shell, bad: str) -> None:
    content = source_archive(shell, bad)
    target = locations(shell.env)
    with pytest.raises(InstallError) as result:
        install(
            target,
            shell.env,
            lambda url: json.dumps({"sha": SHA}).encode() if url == API else content,
        )
    assert result.value.status == 65
    assert not target.root.exists()
    assert not (Path(shell.env["HOME"]) / ".local/bin/ayi").exists()


def test_link_conflict_rolls_back_only_the_new_source(shell: Shell) -> None:
    content = source_archive(shell)
    target = locations(shell.env)
    destination = Path(shell.env["HOME"]) / ".local/bin/ayi"
    destination.parent.mkdir(parents=True)
    _ = destination.write_text("user command")
    with pytest.raises(InstallError) as result:
        install(
            target,
            shell.env,
            lambda url: json.dumps({"sha": SHA}).encode() if url == API else content,
        )
    assert result.value.status == 65
    assert destination.read_text() == "user command"
    assert not target.root.exists()


def test_ambient_git_index_stays_outside_the_new_registry(shell: Shell) -> None:
    content = source_archive(shell)
    init_git(shell)
    index = shell.root / ".git/index"
    before = index.read_bytes()
    env = dict(
        shell.env,
        GIT_DIR=str(shell.root / ".git"),
        GIT_WORK_TREE=str(shell.root),
        GIT_INDEX_FILE=str(index),
    )
    target = locations(env)
    install(target, env, lambda url: json.dumps({"sha": SHA}).encode() if url == API else content)
    assert (target.root / ".git/HEAD").is_file()
    assert index.read_bytes() == before
