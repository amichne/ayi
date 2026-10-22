from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from .conftest import Shell, copy_runtime, init_git


def prepare(shell: Shell) -> Path:
    copy_runtime(shell, "create.py", "factory.py", "prompts", ".gitignore")
    init_git(shell)
    pi = Path(shell.env["PATH"].split(":")[0]) / "pi"
    _ = pi.write_text(
        f"#!{sys.executable}\n"
        + """
import json
import os
import sys
from pathlib import Path

home = Path(os.environ["HOME"])
args = sys.argv[1:]
mode = Path(args[args.index("--append-system-prompt") + 1]).stem
with (home / "calls").open("a") as output:
    output.write(json.dumps({"mode": mode, "args": args}) + "\\n")
if mode == "explain":
    request = json.loads(args[-1])
    print("Usage: ayi run " + request["name"] + " NAME. Prints hello NAME.")
elif mode == "interview":
    request = json.loads(args[-1])
    if (home / "bad-response").exists():
        print((home / "bad-response").read_text())
        sys.exit(0)
    topic = request["required_topic"]
    if topic == "name":
        reply = dict(status="question", topic="name", question="Name your greeting tool?", options=["greet", "welcome"])
    elif topic == "usage":
        name = request["name"]
        reply = dict(status="question", topic="usage", question=f"How should {name} receive a name?", options=[f"ayi run {name} NAME", f"echo NAME | ayi run {name}"])
    elif not any(a["topic"] == "requirements" for a in request["answers"]):
        reply = dict(status="question", topic="requirements", question="Which greeting style?", options=["Friendly", "Formal"])
    else:
        assert request["answers"][-1]["answer"] == "Use hello, preserve spaces"
        if request["action"] == "modify":
            assert "tasks/greet" in request["source"]
            if (home / "concurrent-edit").exists():
                root = Path(args[args.index("--append-system-prompt") + 1]).parents[1]
                (root / "tasks/greet").write_text("concurrent edit\\n")
        reply = dict(status="ready", name=request["name"], requirements="Print hello NAME, preserving spaces. No network.")
    print(json.dumps(reply))
else:
    assert "--print" in args
    assert sys.stdin.read() == ""
    assert "Use hello, preserve spaces" in args[-1]
    Path("proposal.toml").write_text('name = "greet"\\nstatus = "ready"\\n')
    Path("requirements.md").write_text("Print hello NAME, preserving spaces. No network.\\n")
    Path("task").write_text('#!/bin/bash\\n#MISE description="Greet a name"\\nprintf "hello %s\\\\n" "$1"\\n')
    Path("verify").write_text('#!/bin/bash\\nset -eu\\ntest "$("$1" "space name")" = "hello space name"\\n')
"""
    )
    pi.chmod(0o755)
    return Path(shell.env["HOME"])


def git(shell: Shell, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(shell.root), *args],
        env=shell.env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def seed_command(shell: Shell) -> None:
    task = shell.root / "tasks/greet"
    _ = task.write_text('#!/bin/bash\n#MISE description="Old greeting"\nprintf "old %s\\n" "$1"\n')
    task.chmod(0o755)
    _ = git(shell, "add", "tasks/greet")
    _ = git(shell, "commit", "-qm", "original")


@pytest.mark.parametrize("args", [("new", "Greet", "a person"), ("new",), ()])
def test_adaptive_factory_registers_runnable_tool(shell: Shell, args: tuple[str, ...]) -> None:
    home = prepare(shell)
    prompt = "Greet a person\n" if len(args) < 2 else ""
    result = shell.run(*args, input_text=prompt + "1\n2\nUse hello, preserve spaces\ny\ny\n")
    assert result.returncode == 0, result.stderr + result.stdout
    assert "How should greet receive a name?" in result.stderr
    assert "Which greeting style?" in result.stderr
    assert shell.run("run", "greet", "Ada Lovelace").stdout == "hello Ada Lovelace\n"
    calls = [json.loads(line) for line in (home / "calls").read_text().splitlines()]
    last_interview = json.loads(calls[-2]["args"][-1])
    assert last_interview["request"] == "Greet a person"
    assert last_interview["answers"][1]["answer"] == "echo NAME | ayi run greet"
    assert "--no-tools" in calls[0]["args"]
    assert git(shell, "status", "--porcelain") == ""


@pytest.mark.parametrize("has_supporting_files", [False, True])
def test_modify_preserves_unrelated_index_and_rolls_back_rejected_commit(
    shell: Shell,
    has_supporting_files: bool,
) -> None:
    _ = prepare(shell)
    seed_command(shell)
    if has_supporting_files:
        for name, content in (
            ("verification/greet", "#!/bin/sh\nexit 0\n"),
            ("requirements/greet.md", "Old requirements\n"),
        ):
            path = shell.root / name
            path.parent.mkdir(exist_ok=True)
            _ = path.write_text(content)
        _ = git(shell, "add", "verification/greet", "requirements/greet.md")
        _ = git(shell, "commit", "-qm", "supporting files")
    originals = {
        path: (path.read_bytes(), path.stat().st_mode)
        for path in (
            shell.root / "tasks/greet",
            shell.root / "verification/greet",
            shell.root / "requirements/greet.md",
        )
        if path.exists()
    }
    unrelated = shell.root / "unrelated"
    _ = unrelated.write_text("keep staged")
    _ = git(shell, "add", "unrelated")
    before_index = git(shell, "ls-files", "--stage")
    hook = shell.root / ".git/hooks/pre-commit"
    _ = hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    result = shell.run(
        "modify", "greet", "Use hello", input_text="Use hello, preserve spaces\ny\ny\n"
    )
    assert result.returncode != 0
    assert "Verification passed" in result.stdout
    for path, expected in originals.items():
        assert (path.read_bytes(), path.stat().st_mode) == expected
    assert (shell.root / "verification/greet").exists() == has_supporting_files
    assert git(shell, "ls-files", "--stage") == before_index
    hook.unlink()
    result = shell.run(
        "modify", "greet", input_text="Use hello\nUse hello, preserve spaces\ny\ny\n"
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert shell.run("run", "greet", "Grace Hopper").stdout == "hello Grace Hopper\n"
    assert git(shell, "diff", "--cached", "--name-only") == "unrelated\n"
    assert set(git(shell, "show", "--format=", "--name-only", "HEAD").splitlines()) == {
        "tasks/greet",
        "verification/greet",
        "requirements/greet.md",
    }


@pytest.mark.parametrize("state", ["dirty", "staged", "concurrent", "decline"])
def test_modify_never_overwrites_unapproved_changes(shell: Shell, state: str) -> None:
    home = prepare(shell)
    seed_command(shell)
    task = shell.root / "tasks/greet"
    if state in ("dirty", "staged"):
        _ = task.write_text("user changes\n")
        if state == "staged":
            _ = git(shell, "add", "tasks/greet")
    if state == "concurrent":
        _ = (home / "concurrent-edit").write_text("")
    before = task.read_text()
    head = git(shell, "rev-parse", "HEAD")
    answer = "n\n" if state == "decline" else "y\n"
    result = shell.run(
        "modify", "greet", "Use hello", input_text="Use hello, preserve spaces\ny\n" + answer
    )
    assert result.returncode != 0
    assert task.read_text() == ("concurrent edit\n" if state == "concurrent" else before)
    assert git(shell, "rev-parse", "HEAD") == head
    if state in ("dirty", "staged"):
        assert not (home / "calls").exists()


@pytest.mark.parametrize(
    "reply",
    [
        "not json",
        '{"status":"ready","name":"greet","requirements":"skipped questions"}',
        '{"status":"question","topic":"name","question":"Name?","options":["../escape","safe"]}',
    ],
)
def test_invalid_interview_cannot_generate_or_register(shell: Shell, reply: str) -> None:
    home = prepare(shell)
    _ = (home / "bad-response").write_text(reply)
    result = shell.run("new", "Greet", input_text="1\ny\ny\n")
    assert result.returncode == 65
    assert not (shell.root / "drafts").exists()
    assert not (shell.root / "tasks/greet").exists()


def test_explain_reads_without_executing_or_writing(shell: Shell) -> None:
    home = prepare(shell)
    task = shell.root / "tasks/greet"
    _ = task.write_text('#!/bin/bash\ntouch "$HOME/should-not-execute"\n')
    task.chmod(0o755)
    before = git(shell, "status", "--porcelain")
    result = shell.run("explain", "greet")
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("Usage: ayi run greet")
    assert not (home / "should-not-execute").exists()
    assert git(shell, "status", "--porcelain") == before
    call = json.loads((home / "calls").read_text())
    assert "--no-tools" in call["args"]
    assert "should-not-execute" in json.loads(call["args"][-1])["source"]["tasks/greet"]


def test_unknown_command_and_cancel_do_not_generate(shell: Shell) -> None:
    home = prepare(shell)
    for action in ("modify", "explain"):
        for name in ("absent", "../escape"):
            assert shell.run(action, name).returncode == 64
    assert not (home / "calls").exists()
    assert shell.run("new", "Greet", input_text="").returncode == 130
    assert not (shell.root / "drafts").exists()
