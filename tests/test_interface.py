from __future__ import annotations

import subprocess
from pathlib import Path

from .conftest import Shell


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
    assert help_result.stdout.startswith("Usage: ayi list |")
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
    assert rejected.stderr == "ayi: unknown command: absent\n"


def test_fresh_shell_runs_both_tasks(shell: Shell) -> None:
    for name in ("one", "two"):
        result = subprocess.run(
            [
                "/bin/zsh",
                "-f",
                "-c",
                'exec "$1" run "$2"',
                "zsh",
                str(shell.root / "bin/ayi"),
                name,
            ],
            env=shell.env,
            cwd=shell.cwd,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines() == [str(shell.cwd.resolve())]


def test_arguments_arrive_unchanged(shell: Shell) -> None:
    args = ("space value", "$(touch forbidden)", "--flag", "", "::: aaa", ":::", "two", "*")
    result = shell.run("run", "one", *args)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [str(shell.cwd.resolve()), *args]
    assert not (shell.cwd / "forbidden").exists()


def test_discovery_and_source(shell: Shell) -> None:
    listed = shell.run("list")
    assert listed.returncode == 0, listed.stderr
    assert "one" in listed.stdout and "Fixture two" in listed.stdout
    inspected = shell.run("inspect", "one")
    assert inspected.returncode == 0, inspected.stderr
    assert "tasks/one" in inspected.stdout
    assert "Invocation: ayi run one" in inspected.stdout
    assert 'printf "%s\\n" "$PWD" "$@"' in inspected.stdout


def test_unknown_and_traversal_fail(shell: Shell) -> None:
    for args in (("run", "absent"), ("inspect", "../mise.toml"), ("run", "*"), ("bogus",)):
        result = shell.run(*args)
        assert result.returncode != 0
        assert "ayi:" in result.stderr


def test_task_failure_reaches_caller(shell: Shell) -> None:
    task = shell.root / "tasks/one"
    _ = task.write_text("#!/bin/bash\nexit 23\n", encoding="utf-8")
    assert shell.run("run", "one").returncode == 23
