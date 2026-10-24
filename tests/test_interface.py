from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from .conftest import Shell, copy_runtime


def prepare(shell: Shell) -> None:
    copy_runtime(shell, "install.py")
    assert shell.run("install", "apply").returncode == 0


@pytest.mark.parametrize("flag", ["--version", "-V"])
def test_version_uses_project_metadata_without_runtime_sync(shell: Shell, flag: str) -> None:
    prepare(shell)
    project = shell.root / "pyproject.toml"
    _ = project.write_text(project.read_text().replace('version = "0.1.0"', 'version = "9.8.7"'))
    # An installed runtime must not need uv, Git, or network access to show its version.
    env = dict(shell.env, PATH="/usr/bin:/bin")
    result = subprocess.run(
        [str(Path(shell.env["HOME"]) / ".local/bin/ayi"), flag],
        env=env,
        cwd=shell.cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "ayi 9.8.7\n"
    assert result.stderr == ""
    assert shell.run(flag, "extra").returncode == 64


def test_update_launcher_reaches_installer_and_rejects_arguments(shell: Shell) -> None:
    result = shell.run("update")
    assert result.returncode == 65, result.stderr
    assert "Git" in result.stderr
    assert shell.run("update", "--force").returncode == 64


def test_public_ayi_command_is_offline_and_rejects_unknown_names(tmp_path: Path) -> None:
    launcher = Path(__file__).resolve().parents[1] / "bin/ayi"
    assert launcher.is_file(), "The public ayi command must exist"
    env = {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}
    help_result = subprocess.run(
        [str(launcher), "--help"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert help_result.returncode == 0, help_result.stderr
    assert help_result.stdout.startswith("Usage: ayi new [--force] <name> [prompt]")
    assert "modify [--force] <name> [prompt] | explain <name>" in help_result.stdout
    assert help_result.stderr == ""
    rejected = subprocess.run(
        [str(launcher), "run", "absent"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert rejected.returncode == 64
    assert rejected.stderr == "ayi: unknown action: run\n"


def test_fresh_shell_runs_both_tasks(shell: Shell) -> None:
    prepare(shell)
    env = dict(shell.env, PATH=f"{shell.env['HOME']}/.local/bin:{shell.env['PATH']}")
    for name in ("one", "two"):
        result = subprocess.run(
            [
                "/bin/zsh",
                "-f",
                "-c",
                'exec "$1"',
                "zsh",
                name,
            ],
            env=env,
            cwd=shell.cwd,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines() == [str(shell.cwd.resolve())]


def test_arguments_arrive_unchanged(shell: Shell) -> None:
    prepare(shell)
    args = (
        "space value",
        "$(touch forbidden)",
        "--flag",
        "",
        "::: aaa",
        ":::",
        "two",
        "*",
        "--help",
    )
    result = shell.tool("one", *args)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [str(shell.cwd.resolve()), *args]
    assert not (shell.cwd / "forbidden").exists()


def test_discovery(shell: Shell) -> None:
    listed = shell.run("list")
    assert listed.returncode == 0, listed.stderr
    assert "one" in listed.stdout and "Fixture two" in listed.stdout


def test_unknown_and_traversal_fail(shell: Shell) -> None:
    for args in (
        ("run", "absent"),
        ("inspect", "../mise.toml"),
        ("create", "Greet"),
        ("ci",),
        ("review",),
        ("bogus",),
    ):
        result = shell.run(*args)
        assert result.returncode != 0
        assert "ayi:" in result.stderr


def test_task_failure_reaches_caller(shell: Shell) -> None:
    prepare(shell)
    task = shell.root / "tasks/one"
    _ = task.write_text("#!/bin/bash\nexit 23\n", encoding="utf-8")
    assert shell.tool("one").returncode == 23
    help_result = shell.tool("one", "--help")
    assert help_result.returncode == 23
    assert "Created with ayi" in help_result.stdout
