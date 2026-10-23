from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Shell:
    root: Path
    cwd: Path
    env: dict[str, str]

    def run(self, *args: str, input_text: str = "") -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(self.root / "bin/ayi"), *args],
            cwd=self.cwd,
            env=self.env,
            input=input_text,
            capture_output=True,
            text=True,
            check=False,
        )

    def tool(self, name: str, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(Path(self.env["HOME"]) / ".local/bin" / name), *args],
            cwd=self.cwd,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )


@pytest.fixture
def shell(tmp_path: Path) -> Shell:
    root = tmp_path / "checkout with spaces"
    (root / "bin").mkdir(parents=True)
    (root / "tasks").mkdir(parents=True)
    for name in (
        "bin/ayi",
        "bin/ayi-tool",
        "mise.toml",
        "pyproject.toml",
        "uv.lock",
        ".python-version",
        "ui.py",
        "interface.py",
        "completion.py",
        "shell.py",
        "factory.py",
        "create.py",
        "install.py",
    ):
        source = SOURCE / name
        if source.exists():
            _ = shutil.copy2(source, root / name)
    _ = shutil.copytree(SOURCE / "shell", root / "shell")
    home = tmp_path / "home"
    home.mkdir()
    tools = tmp_path / "bin"
    tools.mkdir()
    mise = shutil.which("mise")
    assert mise is not None, "mise is necessary for acceptance"
    (tools / "mise").symlink_to(Path(mise).resolve())
    uv = shutil.which("uv")
    assert uv is not None
    # Resolve a mise shim before the disposable HOME removes its tool registry.
    resolved = subprocess.run([uv, "--version"], capture_output=True, text=True, check=False)
    assert resolved.returncode == 0
    if "shims" in uv or Path(uv).resolve().name == "mise":
        resolved = subprocess.run(
            [mise, "which", "uv"], capture_output=True, text=True, check=False
        )
        assert resolved.returncode == 0
        uv = resolved.stdout.strip()
    (tools / "uv").symlink_to(Path(uv).resolve())
    (tools / "python3.12").symlink_to(sys.executable)
    shellcheck = shutil.which("shellcheck")
    assert shellcheck is not None
    (tools / "shellcheck").symlink_to(Path(shellcheck).resolve())
    # Tests receive no host credentials or shell configuration.
    env = {
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_DATA_HOME": str(home / ".local/share"),
        "XDG_STATE_HOME": str(home / ".local/state"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "PATH": f"{tools}:/usr/bin:/bin",
        "MISE_AUTO_INSTALL": "false",
        "MISE_TASK_RUN_AUTO_INSTALL": "false",
        "MISE_COLOR": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
    }
    for name in ("one", "two"):
        task = root / "tasks" / name
        _ = task.write_text(
            f'#!/bin/bash\n#MISE description="Fixture {name}"\nprintf "%s\\n" "$PWD" "$@"\n',
            encoding="utf-8",
        )
        task.chmod(0o755)
        requirements = root / "requirements" / f"{name}.md"
        requirements.parent.mkdir(exist_ok=True)
        _ = requirements.write_text(f"Fixture {name}\n")
    if (root / "mise.toml").exists():
        result = subprocess.run(
            [str(tools / "mise"), "trust", str(root / "mise.toml")],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
    return Shell(root, tmp_path, env)


def copy_runtime(shell: Shell, *names: str) -> None:
    for name in names:
        source = SOURCE / name
        target = shell.root / name
        if source.is_dir():
            _ = shutil.copytree(source, target)
        elif source.exists():
            _ = shutil.copy2(source, target)


def init_git(shell: Shell) -> None:
    for args in (
        ["init", "-q", str(shell.root)],
        ["-C", str(shell.root), "config", "user.name", "Acceptance"],
        ["-C", str(shell.root), "config", "user.email", "acceptance@example.invalid"],
        ["-C", str(shell.root), "add", "."],
        ["-C", str(shell.root), "commit", "-qm", "fixture"],
    ):
        result = subprocess.run(
            ["/usr/bin/git", *args], env=shell.env, capture_output=True, text=True, check=False
        )
        assert result.returncode == 0, result.stderr
