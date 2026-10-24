from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping
from pathlib import Path

import pytest

import bootstrap

from .conftest import Shell, init_git
from .test_bootstrap import API, SHA, source_archive

NEXT = "b" * 40


def git(root: Path, env: dict[str, str], *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], env=env, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def commit(root: Path, env: dict[str, str]) -> str:
    _ = git(root, env, "add", ".")
    _ = git(
        root,
        env,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-qm",
        "Local tool",
    )
    return git(root, env, "rev-parse", "HEAD")


def prepare(shell: Shell, monkeypatch: pytest.MonkeyPatch) -> Path:
    _ = (shell.root / "upstream.txt").write_text("old source\n")
    _ = (shell.root / "obsolete.txt").write_text("removed upstream\n")
    old = source_archive(shell)
    target = bootstrap.locations(shell.env)
    bootstrap.install(
        target, shell.env, lambda url: json.dumps({"sha": SHA}).encode() if url == API else old
    )
    _ = (shell.root / "upstream.txt").write_text("new source\n")
    (shell.root / "upstream.txt").chmod(0o755)
    (shell.root / "obsolete.txt").unlink()
    _ = (shell.root / "new.txt").write_text("new file\n")
    new = source_archive(shell, sha=NEXT)
    responses = {
        API: json.dumps({"sha": NEXT}).encode(),
        f"https://github.com/amichne/ayi/archive/{SHA}.tar.gz": old,
        f"https://github.com/amichne/ayi/archive/{NEXT}.tar.gz": new,
    }

    def fetch(url: str) -> bytes:
        return responses[url]

    monkeypatch.setattr(bootstrap, "download", fetch)
    monkeypatch.setattr(bootstrap, "__file__", str(target.root / "bootstrap.py"))
    monkeypatch.setattr(bootstrap.os, "environ", shell.env)
    return target.root


def test_update_preserves_tools_history_and_foreign_git_index(
    shell: Shell, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = prepare(shell, monkeypatch)
    task = root / "tasks/saved"
    _ = task.write_text("#!/bin/sh\nprintf 'saved tool\\n'\n")
    task.chmod(0o755)
    _ = (root / "requirements/saved.md").write_text("Saved tool\n")
    before = commit(root, shell.env)
    init_git(shell)
    foreign_index = shell.root / ".git/index"
    original_index = foreign_index.read_bytes()
    monkeypatch.setattr(
        bootstrap.os,
        "environ",
        dict(
            shell.env,
            GIT_DIR=str(shell.root / ".git"),
            GIT_WORK_TREE=str(shell.root),
            GIT_INDEX_FILE=str(foreign_index),
        ),
    )

    assert bootstrap.main(["update"]) == 0
    assert "Updated Ayi" in capsys.readouterr().out
    assert (root / "upstream.txt").read_text() == "new source\n"
    assert (root / "upstream.txt").stat().st_mode & 0o111 == 0o111
    assert (root / "new.txt").read_text() == "new file\n"
    assert not (root / "obsolete.txt").exists()
    assert (root / ".ayi-source").read_text() == NEXT + "\n"
    assert git(root, shell.env, "rev-parse", "HEAD^") == before
    assert git(root, shell.env, "status", "--porcelain") == ""
    assert foreign_index.read_bytes() == original_index
    result = shell.tool("saved")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "saved tool\n"
    installed = Path(shell.env["HOME"]) / ".local/bin/ayi"
    for flag in ("--version", "-V"):
        result = subprocess.run(
            [str(installed), flag], env=shell.env, capture_output=True, text=True, check=False
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == "ayi 0.1.0 (bbbbbbbbbbbb)\n"


@pytest.mark.parametrize("change", ["unstaged", "staged", "untracked", "committed", "symlink"])
def test_update_rejects_local_changes_without_replacement(
    shell: Shell, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], change: str
) -> None:
    root = prepare(shell, monkeypatch)
    path = root / ("new.txt" if change == "untracked" else "upstream.txt")
    if change == "symlink":
        path.unlink()
        path.symlink_to(shell.root / "upstream.txt")
        _ = commit(root, shell.env)
    else:
        _ = path.write_text("local work\n")
        if change == "staged":
            _ = git(root, shell.env, "add", "upstream.txt")
        elif change == "committed":
            _ = commit(root, shell.env)
    before = git(root, shell.env, "rev-parse", "HEAD")
    contents = path.read_bytes()
    assert bootstrap.main(["update"]) == 65
    assert "unchanged" in capsys.readouterr().err
    assert path.read_bytes() == contents
    assert (root / ".ayi-source").read_text() == SHA + "\n"
    assert git(root, shell.env, "rev-parse", "HEAD") == before
    # Git status can refresh stat metadata; staged entries must remain identical.
    if change == "staged":
        assert git(root, shell.env, "show", ":upstream.txt") == "local work\n"


def test_update_rejects_developer_checkout_before_download(
    shell: Shell, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(bootstrap, "__file__", str(shell.root / "bootstrap.py"))
    monkeypatch.setattr(bootstrap.os, "environ", shell.env)

    def no_download(_url: str) -> bytes:
        pytest.fail("no download for a checkout")

    monkeypatch.setattr(bootstrap, "download", no_download)
    assert bootstrap.main(["update"]) == 65
    assert "Git" in capsys.readouterr().err


def test_update_current_revision_reuses_registry(
    shell: Shell, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = prepare(shell, monkeypatch)

    def fetch(url: str) -> bytes:
        assert url == API, "no archive download"
        return json.dumps({"sha": SHA}).encode()

    monkeypatch.setattr(bootstrap, "download", fetch)
    before = git(root, shell.env, "rev-parse", "HEAD")
    assert bootstrap.main(["update"]) == 0
    assert "up to date" in capsys.readouterr().out
    assert git(root, shell.env, "rev-parse", "HEAD") == before


def test_bootstrap_stdin_can_upgrade_an_older_installation(
    shell: Shell, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = prepare(shell, monkeypatch)
    monkeypatch.setattr(bootstrap, "__file__", "<stdin>")
    assert bootstrap.main(["update"]) == 0
    assert (root / ".ayi-source").read_text() == NEXT + "\n"


def test_update_commit_failure_restores_source_and_index(
    shell: Shell, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = prepare(shell, monkeypatch)
    before = git(root, shell.env, "rev-parse", "HEAD")
    real_git = bootstrap.git

    def fail_commit(root: Path, env: Mapping[str, str], *args: str) -> str:
        if "commit" in args:
            raise bootstrap.InstallError(73, "fixture commit failure")
        return real_git(root, env, *args)

    monkeypatch.setattr(bootstrap, "git", fail_commit)
    assert bootstrap.main(["update"]) == 73
    assert git(root, shell.env, "rev-parse", "HEAD") == before
    assert git(root, shell.env, "status", "--porcelain") == ""
    assert (root / "upstream.txt").read_text() == "old source\n"
    assert (root / "upstream.txt").stat().st_mode & 0o111 == 0
    assert (root / "obsolete.txt").read_text() == "removed upstream\n"
    assert not (root / "new.txt").exists()
    assert (root / ".ayi-source").read_text() == SHA + "\n"


def test_update_setup_failure_keeps_source_commit_and_allows_retry(
    shell: Shell, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = prepare(shell, monkeypatch)
    real_apply = bootstrap.apply

    def fail_setup(*_args: object) -> None:
        raise bootstrap.InstallError(69, "fixture setup failure")

    monkeypatch.setattr(bootstrap, "apply", fail_setup)
    assert bootstrap.main(["update"]) == 69
    assert "source updated" in capsys.readouterr().err
    assert (root / ".ayi-source").read_text() == NEXT + "\n"
    before = git(root, shell.env, "rev-parse", "HEAD")
    monkeypatch.setattr(bootstrap, "apply", real_apply)
    assert bootstrap.main(["update"]) == 0
    assert git(root, shell.env, "rev-parse", "HEAD") == before


@pytest.mark.parametrize("stage", ["metadata", "old", "new"])
def test_update_download_failure_keeps_installed_revision(
    shell: Shell, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    root = prepare(shell, monkeypatch)
    fetch = bootstrap.download
    failed_url = {
        "metadata": API,
        "old": f"https://github.com/amichne/ayi/archive/{SHA}.tar.gz",
        "new": f"https://github.com/amichne/ayi/archive/{NEXT}.tar.gz",
    }[stage]

    def fail_download(url: str) -> bytes | bootstrap.DownloadFailed:
        return (
            bootstrap.DownloadFailed(url, "fixture unavailable")
            if url == failed_url
            else fetch(url)
        )

    monkeypatch.setattr(bootstrap, "download", fail_download)
    before = git(root, shell.env, "rev-parse", "HEAD")
    assert bootstrap.main(["update"]) == 69
    assert (root / ".ayi-source").read_text() == SHA + "\n"
    assert git(root, shell.env, "rev-parse", "HEAD") == before
    assert git(root, shell.env, "status", "--porcelain") == ""
