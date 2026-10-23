from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from interface import Interface

from .conftest import Shell, copy_runtime


def manifest(name: str = "one") -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "name": name,
        "summary": "Inspect files",
        "positionals": [
            {"name": "directory", "completion": {"kind": "directory"}},
            {"name": "file", "repeatable": True, "completion": {"kind": "file"}},
        ],
        "options": [
            {
                "names": ["--sort", "-s"],
                "description": "Sort results",
                "value": {
                    "name": "ORDER",
                    "completion": {"kind": "choices", "values": ["size", "name", "modified"]},
                },
            },
            {"names": ["--quiet"], "description": "No diagnostics"},
            {
                "names": ["--limit"],
                "description": "Result limit",
                "value": {"name": "COUNT", "completion": {"kind": "none"}},
            },
        ],
    }


def prepare(shell: Shell) -> None:
    copy_runtime(shell, "install.py")
    assert shell.run("install", "plan").returncode == 0
    (shell.root / "interface").mkdir()
    _ = (shell.root / "interface/one.json").write_text(json.dumps(manifest()))
    (shell.cwd / "folder space").mkdir()
    (shell.cwd / "second").mkdir()
    _ = (shell.cwd / "file.txt").write_text("hello")
    _ = (shell.cwd / "bad\x1bname").write_text("never emit controls")
    # A successful completion must never execute either of these programs.
    body = '#!/bin/sh\ntouch "$HOME/unexpected-execution"\nexit 71\n'
    _ = (shell.root / "tasks/one").write_text(body)
    pi = Path(shell.env["PATH"].split(":")[0]) / "pi"
    _ = pi.write_text(body)
    pi.chmod(0o755)


@pytest.mark.parametrize(
    "args,expected",
    [
        (["--s"], ["--sort"]),
        (["--sort", ""], ["size", "name", "modified"]),
        (["--sort", "m"], ["modified"]),
        (["--sort=si"], ["--sort=size"]),
        (["-s", "name", "--s"], []),
        (["--limit", ""], []),
        (["fo"], ["folder space/"]),
        (["--", "fo"], ["folder space/"]),
        (["--sort", "size", "--", "folder space", "fi"], ["file.txt"]),
        (["second", "file.txt", "fo"], ["folder space/"]),
        (["--", "--s"], []),
    ],
)
def test_contextual_completion_is_read_only(
    shell: Shell, args: list[str], expected: list[str]
) -> None:
    prepare(shell)
    request = {"command": "one", "args": args, "cursor": len(args) - 1, "cwd": str(shell.cwd)}
    result = shell.run("complete", input_text=json.dumps(request))
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert [row["value"] for row in json.loads(result.stdout)] == expected
    assert "\x1b" not in result.stdout
    assert not (Path(shell.env["HOME"]) / "unexpected-execution").exists()


@pytest.mark.parametrize(
    "completion",
    [
        {"kind": "exec", "command": "pi"},
        {"kind": "choices", "values": ["bad\tvalue"]},
        {"kind": "file", "provider": "task"},
    ],
)
def test_closed_contract_rejects_unsafe_or_implicit_providers(
    completion: dict[str, object],
) -> None:
    document = manifest()
    document["positionals"] = [{"name": "input", "completion": completion}]
    with pytest.raises(ValidationError):
        _ = Interface.model_validate(document)


def test_legacy_missing_and_invalid_completion(shell: Shell) -> None:
    prepare(shell)
    for command in ("two", "absent", "../outside"):
        result = shell.run(
            "complete",
            input_text=json.dumps(
                {"command": command, "args": [], "cursor": 0, "cwd": str(shell.cwd)}
            ),
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == []
    assert shell.run("complete", input_text='{"command":"one"}').returncode == 65
    _ = (shell.root / "interface/one.json").write_text('{"schemaVersion":99}')
    result = shell.run(
        "complete",
        input_text=json.dumps({"command": "one", "args": [], "cursor": 0, "cwd": str(shell.cwd)}),
    )
    assert result.returncode == 65
    assert result.stdout == ""


def test_zsh_refresh_registers_new_tools_without_startup_edits(shell: Shell) -> None:
    prepare(shell)
    assert shell.run("install", "apply").returncode == 0
    script = """
autoload -Uz compinit
compinit -D -i
eval "$("$1/bin/ayi" shell init zsh)"
[[ $_comps[one] == _ayi && $_comps[ayi] == _ayi ]] || exit 20
cp "$1/tasks/one" "$1/tasks/new-tool"
ln -s "$1/bin/ayi-tool" "$HOME/.local/bin/new-tool"
_ayi_refresh
[[ $_comps[new-tool] == _ayi ]] || exit 21
rm "$HOME/.local/bin/new-tool"
_ayi_refresh
[[ -z ${_comps[new-tool]-} ]] || exit 22
eval "$("$1/bin/ayi" shell init zsh)"
[[ ${#${(M)precmd_functions:#_ayi_refresh}} == 1 ]] || exit 23
print refreshed
"""
    result = subprocess.run(
        ["/bin/zsh", "-f", "-c", script, "zsh", str(shell.root)],
        env=shell.env,
        cwd=shell.cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert result.stdout == "refreshed\n"
    assert not (Path(shell.env["HOME"]) / ".zshrc").exists()
    assert not (Path(shell.env["HOME"]) / "unexpected-execution").exists()
