from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from .conftest import Shell, copy_runtime, init_git


def prepare(shell: Shell) -> None:
    copy_runtime(shell, "install.py", ".gitignore")


def test_plan_apply_fresh_shell_uninstall(shell: Shell) -> None:
    prepare(shell)
    home = Path(shell.env["HOME"])
    startup = home / ".zshrc"
    _ = startup.write_text("# User startup stays unchanged\n", encoding="utf-8")
    before = startup.read_bytes()
    plan = shell.run("install", "plan")
    assert plan.returncode == 0, plan.stderr
    assert ".local/bin/ayi" in plan.stdout
    assert not (home / ".local/bin/ayi").exists()
    applied = shell.run("install", "apply")
    assert applied.returncode == 0, applied.stderr
    assert shell.run("install", "apply").returncode == 0
    env = dict(shell.env, PATH=f"{home / '.local/bin'}:{shell.env['PATH']}")
    for name in ("one", "two"):
        result = subprocess.run(
            ["/bin/zsh", "-f", "-c", 'ayi list && "$1" "two words"', "zsh", name],
            cwd=shell.cwd,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert "Fixture one" in result.stdout
        assert result.stdout.endswith("two words\n")
    assert startup.read_bytes() == before
    assert not (home / ".bashrc").exists()
    removed = shell.run("install", "uninstall")
    assert removed.returncode == 0, removed.stderr
    assert not (home / ".local/bin/ayi").is_symlink()
    assert not (Path(shell.env["XDG_CONFIG_HOME"]) / "ayi").is_symlink()
    assert not (home / ".local/bin/one").is_symlink()
    assert not (home / ".local/bin/two").is_symlink()
    assert startup.read_bytes() == before


def test_conflict_preserves_all_targets(shell: Shell) -> None:
    prepare(shell)
    destination = Path(shell.env["XDG_CONFIG_HOME"]) / "ayi"
    destination.mkdir(parents=True)
    _ = (destination / "keep").write_text("user data", encoding="utf-8")
    result = shell.run("install", "apply")
    assert result.returncode == 65
    assert "conflict" in result.stderr
    assert (destination / "keep").read_text() == "user data"
    assert not (Path(shell.env["HOME"]) / ".local/bin/ayi").exists()


@pytest.mark.parametrize(("failure", "status"), [("OSError", 74), ("KeyboardInterrupt", 130)])
def test_partial_install_removes_created_links(shell: Shell, failure: str, status: int) -> None:
    prepare(shell)
    # Inject a failure at the second filesystem effect after the first real link.
    script = (
        "from pathlib import Path\nimport runpy, sys\n"
        "original = Path.symlink_to\n"
        "def interrupted(path, target, target_is_directory=False):\n"
        " if path.parent.name != 'bin':\n"
        f"  raise {failure}()\n"
        " original(path, target, target_is_directory)\n"
        "Path.symlink_to = interrupted\n"
        "sys.argv = [sys.argv[1], 'apply']\n"
        "runpy.run_path(sys.argv[0], run_name='__main__')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(shell.root / "install.py")],
        env=shell.env,
        cwd=shell.cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == status, result.stderr
    assert not (Path(shell.env["HOME"]) / ".local/bin/ayi").is_symlink()
    assert not (Path(shell.env["XDG_CONFIG_HOME"]) / "ayi").is_symlink()


def test_clean_clone_recovers_commands(shell: Shell) -> None:
    prepare(shell)
    # The source fixture is committed. The destination receives only Git content.
    init_git(shell)
    clone = shell.cwd / "fresh clone"
    cloned = subprocess.run(
        ["git", "clone", "--quiet", str(shell.root), str(clone)],
        env=shell.env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert cloned.returncode == 0, cloned.stderr
    fresh = Shell(clone, shell.cwd, shell.env)
    assert fresh.run("install", "apply").returncode == 0
    for name in ("one", "two"):
        result = fresh.tool(name, "from clone")
        assert result.returncode == 0, result.stderr
        assert result.stdout.endswith("from clone\n")
    listed = fresh.run("list")
    assert "one" in listed.stdout and "two" in listed.stdout
