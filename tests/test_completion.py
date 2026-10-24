from __future__ import annotations

import fcntl
import json
import os
import pty
import select
import struct
import subprocess
import termios
import time
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
        (["~"], ["~/"]),
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


def test_native_zsh_tab_preserves_values_and_quotes_paths(shell: Shell) -> None:
    prepare(shell)
    assert shell.run("install", "apply").returncode == 0
    home = Path(shell.env["HOME"])
    master, slave = pty.openpty()
    _ = fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 100, 0, 0))
    env = dict(shell.env, TERM="xterm-256color", PATH=f"{home}/.local/bin:{shell.env['PATH']}")
    process = subprocess.Popen(
        ["/bin/zsh", "-dfi"], stdin=slave, stdout=slave, stderr=slave, env=env, cwd=shell.cwd
    )
    os.close(slave)
    transcript = bytearray()

    def wait_file(path: Path) -> None:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if select.select([master], [], [], 0.05)[0]:
                transcript.extend(os.read(master, 65536))
            if path.exists():
                return
        pytest.fail(transcript.decode(errors="replace"))

    try:
        _ = os.write(
            master,
            b'bindkey -e; autoload -Uz compinit; compinit -D -i; eval "$(ayi shell init zsh)"\n',
        )
        _ = os.write(
            master,
            b'capture-buffer() { print -r -- "$BUFFER" > "$HOME/buffer"; zle .send-break; }; zle -N capture-buffer; bindkey "^X" capture-buffer; print ready > "$HOME/ready"\n',
        )
        wait_file(home / "ready")
        for typed, expected in (
            ("one --sort si\t", "one --sort size "),
            ("one --sort=si\t", "one --sort=size "),
            ("one fo\t", "one folder\\ space/"),
            ("ayi mod\t", "ayi modify "),
        ):
            buffer = home / "buffer"
            buffer.unlink(missing_ok=True)
            _ = os.write(master, (typed + "\x18").encode())
            wait_file(buffer)
            assert buffer.read_text().rstrip("\n") == expected, transcript.decode(errors="replace")
        registration = f'cp "{shell.root}/tasks/one" "{shell.root}/tasks/late-tool"; ln -s "{shell.root}/bin/ayi-tool" "$HOME/.local/bin/late-tool"\n'
        _ = os.write(master, registration.encode())
        _ = os.write(master, b'print -r -- "$_comps[late-tool]" > "$HOME/registered"\n')
        wait_file(home / "registered")
        assert (home / "registered").read_text().strip() == "_ayi"
        assert not (home / "unexpected-execution").exists()
    finally:
        _ = os.write(master, b"exit\n")
        try:
            _ = process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            _ = process.wait(timeout=2)
        os.close(master)


def test_discovery_uses_manifest_and_preserves_legacy_tools(shell: Shell) -> None:
    prepare(shell)
    result = shell.run("list")
    assert result.returncode == 0, result.stderr
    assert "one\tInspect files\tone --help" in result.stdout
    assert "two\tFixture two\ttwo --help" in result.stdout
    assert not (Path(shell.env["HOME"]) / "unexpected-execution").exists()


def test_ayi_actions_complete_flags_and_command_context(shell: Shell) -> None:
    prepare(shell)
    for args, expected in (
        (["new", "--f"], ["--force"]),
        (["res"], ["resume"]),
        (["new", "--design"], ["--design-model"]),
        (["new", "--implementation-harness", "c"], ["codex"]),
        (["fork", "flow-example", "--c"], ["--checkpoint"]),
        (["modify", "--f"], ["--force"]),
        (["modify", "--force", "t"], ["two"]),
        (["modify", "--design-model", "designer", "t"], ["two"]),
        (["modify", "--implementation-model=builder", "t"], ["two"]),
        (["explain", "o"], ["one"]),
        (["install", "a"], ["apply"]),
        (["shell", "init", "z"], ["zsh"]),
    ):
        result = shell.run(
            "complete",
            input_text=json.dumps(
                {"command": "ayi", "args": args, "cursor": len(args) - 1, "cwd": str(shell.cwd)}
            ),
        )
        assert result.returncode == 0, result.stderr
        assert [row["value"] for row in json.loads(result.stdout)] == expected


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
