from __future__ import annotations

import fcntl
import json
import os
import pty
import select
import struct
import subprocess
import sys
import termios
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

import completion
from cli import parser
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
    difficult = [
        ("space", "space name"),
        ("single", "single'quote"),
        ("double", 'double"quote'),
        ("dollar", "dollar$HOME"),
        ("paren", "paren(a)"),
        ("wild", "wild[?]*"),
        ("sub", "sub$(touch injected)"),
        ("unicode", "unicode東京"),
        ("-lead", "-leading"),
        (".hidden", ".hidden-file"),
    ]
    for _, name in difficult:
        _ = (shell.cwd / name).write_text("literal")
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
        buffer = home / "buffer"
        for typed, expected in (
            ("one --sort si\t", "one --sort size "),
            ("one --sort=si\t", "one --sort=size "),
            ("one --sort name\x1b[D\x1b[D\t", "one --sort name "),
            ("one fo\t", "one folder\\ space/"),
            ("ayi mod\t", "ayi modify "),
        ):
            buffer.unlink(missing_ok=True)
            _ = os.write(master, (typed + "\x18").encode())
            wait_file(buffer)
            assert buffer.read_text().rstrip("\n") == expected, transcript.decode(errors="replace")
        # A failing completion backend must leave the editing buffer and the
        # next shell command usable, without falling through to task execution.
        interface = shell.root / "interface/one.json"
        _ = interface.write_text('{"schemaVersion":99}')
        buffer.unlink()
        _ = os.write(master, b"one --sort si\t\x18")
        wait_file(buffer)
        assert buffer.read_text().rstrip("\n") == "one --sort si"
        _ = interface.write_text(json.dumps(manifest()))
        # Execute only a shell argument sink, never the generated task. This
        # observes literal arguments after native Zsh quoting and expansion.
        _ = os.write(
            master,
            b'one() { printf "%s\\0" "$@" > "$HOME/arguments"; }; print ready > "$HOME/sink-ready"\n',
        )
        wait_file(home / "sink-ready")
        for prefix, name in difficult:
            arguments = home / "arguments"
            arguments.unlink(missing_ok=True)
            _ = os.write(master, f"one -- second {prefix}\t\n".encode())
            wait_file(arguments)
            assert arguments.read_bytes().split(b"\0") == [b"--", b"second", name.encode(), b""]
        assert not (shell.cwd / "injected").exists()
        registration = f'cp "{shell.root}/tasks/one" "{shell.root}/tasks/late-tool"; ln -s "{shell.root}/bin/ayi-tool" "$HOME/.local/bin/late-tool"\n'
        _ = os.write(master, registration.encode())
        _ = os.write(master, b'print -r -- "$_comps[late-tool]" > "$HOME/registered"\n')
        wait_file(home / "registered")
        assert (home / "registered").read_text().strip() == "_ayi"
        _ = os.write(master, b'rm "$HOME/.local/bin/late-tool"\n')
        _ = os.write(master, b'print -r -- "${_comps[late-tool]-absent}" > "$HOME/removed"\n')
        wait_file(home / "removed")
        assert (home / "removed").read_text().strip() == "absent"
        # Replacing the target without changing the link's path must also
        # invalidate registration at the following prompt.
        _ = os.write(master, b'ln -sf /usr/bin/true "$HOME/.local/bin/one"\n')
        _ = os.write(master, b'print -r -- "${_comps[one]-absent}" > "$HOME/retargeted"\n')
        wait_file(home / "retargeted")
        assert (home / "retargeted").read_text().strip() == "absent"
        _ = os.write(
            master, f'ln -sf "{shell.root}/bin/ayi-tool" "$HOME/.local/bin/one"\n'.encode()
        )
        _ = os.write(master, b'print -r -- "$_comps[one]" > "$HOME/reinstalled"\n')
        wait_file(home / "reinstalled")
        assert (home / "reinstalled").read_text().strip() == "_ayi"
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
        (["up"], ["update"]),
        (["--v"], ["--version"]),
        (["-V"], ["-V"]),
        (["update", "--f"], []),
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


@pytest.mark.parametrize("version", [1, 2])
def test_completion_and_parser_share_token_grammar(
    shell: Shell, version: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepare(shell)
    document = manifest()
    document.update(schemaVersion=version, runtime="python", output=["text", "json"])
    interface = Interface.model_validate(document)
    monkeypatch.setattr(completion, "ROOT", shell.root)
    _ = (shell.root / "interface/one.json").write_text(json.dumps(document))
    # Attached short values, clustered flags, invalid prefixes, option aliases,
    # negative Boolean forms, repeatable positionals, and cursor-in-token values.
    for args, cursor, offset, expected in (
        (["-sname", "--s"], 1, None, []),
        (["--quiet", "--no-q"], 1, None, []),
        (["--unknown", "fo"], 1, None, []),
        (["--sort", "wrong", "fo"], 2, None, []),
        (["--sort", "namejunk"], 1, 2, ["name"]),
        (["--sort=namejunk"], 0, 9, ["--sort=name"]),
        (["-sna"], 0, None, ["-sname"]),
        (["--", "second", "fi"], 2, None, ["file.txt"]),
        (["--no-q"], 0, None, ["--no-quiet"]),
    ):
        request = completion.Request(
            command="one", args=args, cursor=cursor, offset=offset, cwd=str(shell.cwd)
        )
        candidates = completion.resolve(request)
        assert [candidate.value for candidate in candidates] == expected
        for candidate in candidates:
            invocation = [*args[:cursor], candidate.value, *args[cursor + 1 :]]
            _ = parser(interface).parse_args(invocation)


def test_standalone_can_declare_its_native_help_flags(shell: Shell) -> None:
    prepare(shell)
    document = manifest()
    interface = Interface.model_validate(document)
    document["options"] = [
        *(option.model_dump() for option in interface.options),
        {"names": ["--help", "-h"], "description": "Native help"},
    ]
    _ = (shell.root / "interface/one.json").write_text(json.dumps(document))
    result = shell.run(
        "complete",
        input_text=json.dumps(dict(command="one", args=["--s"], cursor=0, cwd=str(shell.cwd))),
    )
    assert result.returncode == 0, result.stderr
    assert [row["value"] for row in json.loads(result.stdout)] == ["--sort"]


def test_completion_offers_hyphen_values_only_in_parser_accepted_contexts(
    shell: Shell, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepare(shell)
    cwd = shell.cwd / "operands"
    cwd.mkdir()
    (cwd / "-operand").mkdir()
    (cwd / "ordinary").mkdir()
    document = manifest()
    document["options"] = [
        {
            "names": ["--sort"],
            "description": "Sort results",
            "value": {
                "name": "ORDER",
                "completion": {
                    "kind": "choices",
                    "values": ["-1", "-declined", "safe"],
                },
            },
        }
    ]
    _ = (shell.root / "interface/one.json").write_text(json.dumps(document))
    monkeypatch.setattr(completion, "ROOT", shell.root)
    interface = Interface.model_validate(document)
    for args, expected in (
        ([""], ["ordinary/"]),
        (["--", ""], ["-operand/", "ordinary/"]),
        (["--sort", ""], ["-1", "safe"]),
        (["--sort="], ["--sort=-1", "--sort=-declined", "--sort=safe"]),
    ):
        candidates = completion.resolve(
            completion.Request(
                command="one",
                args=args,
                cursor=len(args) - 1,
                cwd=str(cwd),
            )
        )
        if args == [""]:
            candidates = [candidate for candidate in candidates if candidate.kind == "directory"]
        values = [candidate.value for candidate in candidates]
        assert values == expected
        for value in values:
            _ = parser(interface).parse_args([*args[:-1], value])


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize(
    "provider",
    [
        {"kind": "provider", "id": "git.branches"},
        {"kind": "provider", "id": "shell", "command": "pi"},
        {"kind": "provider", "id": "https://example.com"},
        {"kind": "provider", "id": "os.system"},
    ],
)
def test_versioned_provider_declarations(version: int, provider: dict[str, object]) -> None:
    document = manifest()
    document.update(schemaVersion=version)
    document["positionals"] = [{"name": "branch", "completion": provider}]
    if version == 2 and provider == {"kind": "provider", "id": "git.branches"}:
        assert Interface.model_validate(document).schema_version == 2
    else:
        with pytest.raises(ValidationError):
            _ = Interface.model_validate(document)


def provider_manifest() -> dict[str, object]:
    return {
        "schemaVersion": 2,
        "name": "one",
        "summary": "Select a local branch",
        "positionals": [
            {"name": "branch", "completion": {"kind": "provider", "id": "git.branches"}}
        ],
    }


def test_git_provider_reads_only_local_branches_from_caller_repository(shell: Shell) -> None:
    prepare(shell)
    _ = (shell.root / "interface/one.json").write_text(json.dumps(provider_manifest()))
    repo = shell.cwd / "local repo"
    repo.mkdir()
    for args in (
        ["init", "-q", "-b", "main"],
        [
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "seed",
            "--allow-empty",
        ],
        ["branch", "feature/api"],
        ["branch", "alpha"],
        ["branch", "éclair"],
        ["update-ref", "refs/remotes/origin/not-local", "HEAD"],
        ["remote", "add", "origin", "https://example.invalid/never-contact"],
    ):
        _ = subprocess.run(["/usr/bin/git", *args], cwd=repo, env=shell.env, check=True)
    nested = repo / "subdirectory"
    nested.mkdir()
    for cwd, prefix, expected in (
        (repo, "", ["alpha", "feature/api", "main", "éclair"]),
        (nested, "f", ["feature/api"]),
        (shell.cwd, "", []),
    ):
        request = dict(command="one", args=["--", prefix], cursor=1, cwd=str(cwd))
        result = shell.run("complete", input_text=json.dumps(request))
        assert result.returncode == 0, result.stderr
        assert result.stderr == ""
        assert [row["value"] for row in json.loads(result.stdout)] == expected
    # A packed set larger than the provider cap is still sorted and bounded.
    refs = "".join(f"create refs/heads/bounded/{index:04d} HEAD\n" for index in range(1100))
    _ = subprocess.run(
        ["/usr/bin/git", "update-ref", "--stdin"],
        input=refs,
        text=True,
        cwd=repo,
        env=shell.env,
        capture_output=True,
        check=True,
    )
    result = shell.run(
        "complete",
        input_text=json.dumps(dict(command="one", args=["--", ""], cursor=1, cwd=str(repo))),
    )
    branches = [row["value"] for row in json.loads(result.stdout)]
    assert len(branches) == 1000
    assert branches == ["alpha", *(f"bounded/{index:04d}" for index in range(999))]
    filtered = shell.run(
        "complete",
        input_text=json.dumps(
            dict(command="one", args=["--", "feature/"], cursor=1, cwd=str(repo))
        ),
    )
    assert [row["value"] for row in json.loads(filtered.stdout)] == ["feature/api"]
    assert not (Path(shell.env["HOME"]) / "unexpected-execution").exists()


@pytest.mark.parametrize("failure", ["exit", "timeout", "encoding", "duplicates"])
def test_provider_effect_boundary_and_unavailability(shell: Shell, failure: str) -> None:
    prepare(shell)
    _ = (shell.root / "interface/one.json").write_text(json.dumps(provider_manifest()))
    trace = shell.cwd / "git-calls.json"
    git = Path(shell.env["PATH"].split(":")[0]) / "git"
    _ = git.write_text(
        f"#!{sys.executable}\nimport json, os, sys, time\nfrom pathlib import Path\n"
        f"Path({str(trace)!r}).write_text(json.dumps(dict(args=sys.argv[1:], env=dict(os.environ), cwd=os.getcwd())))\n"
        + {
            "exit": "sys.exit(17)\n",
            "timeout": "time.sleep(3)\n",
            "encoding": "sys.stdout.buffer.write(b'\\xff')\n",
            "duplicates": "print('zeta\\nalpha\\nalpha\\nbad\\x1bname')\n",
        }[failure]
    )
    git.chmod(0o755)
    env = dict(
        shell.env,
        GIT_DIR="/wrong/repo",
        GIT_CONFIG_COUNT="1",
        GIT_CONFIG_KEY_0="alias.for-each-ref",
        GIT_CONFIG_VALUE_0="!pi",
    )
    result = subprocess.run(
        [str(shell.root / "bin/ayi"), "complete"],
        cwd=shell.cwd,
        env=env,
        input=json.dumps(dict(command="one", args=["--", ""], cursor=1, cwd=str(shell.cwd))),
        capture_output=True,
        text=True,
        timeout=4,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert [row["value"] for row in json.loads(result.stdout)] == (
        ["alpha", "zeta"] if failure == "duplicates" else []
    )
    observed = json.loads(trace.read_text())
    assert observed["args"] == [
        "--no-pager",
        "for-each-ref",
        "--format=%(refname:strip=2)",
        "--sort=refname",
        "--count=1000",
        "refs/heads/",
    ]
    assert observed["cwd"] == str(shell.cwd)
    assert "GIT_DIR" not in observed["env"] and "GIT_CONFIG_COUNT" not in observed["env"]
    assert observed["env"]["GIT_OPTIONAL_LOCKS"] == "0"
    assert not (Path(shell.env["HOME"]) / "unexpected-execution").exists()


def test_provider_missing_git_is_not_a_command_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    assert completion.git_branches(str(tmp_path)) == completion.ProviderUnavailable.PROCESS


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
# Refresh may own its own registrations, not another completion owner's entries.
compdef _files one
cp "$1/tasks/one" "$1/tasks/another"
_ayi_refresh
[[ $_comps[one] == _files ]] || exit 24
# An unchanged prompt performs no backend invocation. A changed but unavailable
# backend leaves the prior registrations intact and never fails the prompt.
mv "$1/bin/ayi" "$1/bin/ayi-real"
print '#!/bin/sh\nexit 71' > "$1/bin/ayi"
chmod +x "$1/bin/ayi"
_ayi_refresh || exit 25
rm "$1/tasks/another"
_ayi_refresh || exit 26
[[ $_comps[ayi] == _ayi && $_comps[one] == _files ]] || exit 27
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
