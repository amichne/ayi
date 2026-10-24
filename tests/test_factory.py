from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from .conftest import Shell, copy_runtime, init_git


def prepare(shell: Shell) -> Path:
    copy_runtime(shell, "create.py", "factory.py", "install.py", "prompts", ".gitignore")
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
    print("Usage: " + request["name"] + " NAME. Prints hello NAME.")
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
        reply = dict(status="question", topic="usage", question=f"How should {name} receive a name?", options=[f"{name} NAME", f"echo NAME | {name}"])
    elif not any(a["topic"] == "requirements" for a in request["answers"]):
        reply = dict(status="question", topic="requirements", question="Which greeting style?", options=["Friendly", "Formal"])
    else:
        assert request["answers"][-1]["answer"] == "Use hello, preserve spaces"
        if request["action"] == "modify":
            assert "tasks/greet" in request["source"]
            if (home / "concurrent-edit").exists():
                root = Path(args[args.index("--append-system-prompt") + 1]).parents[1]
                (root / (home / "concurrent-edit").read_text()).write_text("concurrent edit\\n")
        (home / "agreed-name").write_text(request["name"])
        reply = dict(status="ready", name=request["name"], requirements="Print hello NAME, preserving spaces. No network.")
    print(json.dumps(reply))
else:
    assert "--print" in args
    assert sys.stdin.read() == ""
    assert "Use hello, preserve spaces" in args[-1]
    name = (home / "agreed-name").read_text()
    Path("proposal.toml").write_text(f'name = "{name}"\\nstatus = "ready"\\n')
    Path("requirements.md").write_text("Print hello NAME, preserving spaces. No network.\\n")
    Path("interface.json").write_text(json.dumps(dict(schemaVersion=1, name=name, summary="Greet a name", positionals=[dict(name="NAME", required=True, completion=dict(kind="none"))], examples=[name + " Ada"])))
    Path("task").write_text(f'#!/bin/bash\\n#MISE description="Greet a name"\\ncase "${{1:-}}" in --help|-h) printf "Usage: {name} NAME\\\\n"; exit 0;; -*) exit 64;; esac\\nprintf "hello %s\\\\n" "$1"\\n')
    Path("verify").write_text('#!/bin/bash\\nset -eu\\ntest -s "$2"\\ntest "$("$1" "space name")" = "hello space name"\\n')
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


@pytest.mark.parametrize("args", [("new", "Greet a person"), ("new",), ()])
def test_adaptive_factory_registers_runnable_tool(shell: Shell, args: tuple[str, ...]) -> None:
    home = prepare(shell)
    prompt = "Greet a person\n" if len(args) < 2 else ""
    result = shell.run(
        *args,
        input_text=prompt
        + "1\n/submit\n2\n/submit\nUse hello, preserve spaces\n/submit\n/implement\n/draft\ny\ny\n",
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert "How should greet receive a name?" in result.stderr
    assert "Which greeting style?" in result.stderr
    assert shell.tool("greet", "Ada Lovelace").stdout == "hello Ada Lovelace\n"
    calls = [json.loads(line) for line in (home / "calls").read_text().splitlines()]
    last_interview = json.loads(calls[-2]["args"][-1])
    assert last_interview["request"] == "Greet a person"
    assert last_interview["answers"][1]["answer"] == "echo NAME | greet"
    assert "--no-tools" in calls[0]["args"]
    assert git(shell, "status", "--porcelain") == ""


@pytest.mark.parametrize("with_prompt", [False, True])
def test_named_tool_is_available_on_path_with_attributed_help(
    shell: Shell, with_prompt: bool
) -> None:
    home = prepare(shell)
    arguments = ("new", "abc", "Greet", "a person") if with_prompt else ("new", "abc")
    answer = "" if with_prompt else "Greet a person\n"
    result = shell.run(
        *arguments,
        input_text=answer
        + "1\n/submit\nUse hello, preserve spaces\n/submit\n/implement\n/draft\ny\ny\n",
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert "Name your greeting tool?" not in result.stderr
    assert "Invocation: abc [args]" in result.stdout
    env = dict(shell.env, PATH=f"{home / '.local/bin'}:{shell.env['PATH']}")
    execution = subprocess.run(
        ["/bin/zsh", "-f", "-c", 'command -v abc; abc "Ada Lovelace"; abc --help'],
        cwd=shell.cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert execution.returncode == 0, execution.stderr
    assert execution.stdout == (
        f"{home / '.local/bin/abc'}\nhello Ada Lovelace\nUsage: abc NAME\n"
        "\nCreated with ayi. Manage this tool with: ayi modify abc\n"
    )
    assert execution.stderr == ""
    assert git(shell, "status", "--porcelain") == ""


@pytest.mark.parametrize("name", ["../escape", "BadName", "ayi", "ayi-tool"])
def test_invalid_explicit_name_is_rejected_before_model(shell: Shell, name: str) -> None:
    home = prepare(shell)
    result = shell.run("new", name, "Greet a person")
    assert result.returncode == 64
    assert not (home / "calls").exists()


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
            ("tasks/greet.lock", "Old task lock\n"),
            ("verification/greet.lock", "Old verification lock\n"),
            (
                "interface/greet.json",
                '{"schemaVersion":1,"name":"greet","summary":"Old greeting"}\n',
            ),
        ):
            path = shell.root / name
            path.parent.mkdir(exist_ok=True)
            _ = path.write_text(content)
        _ = git(
            shell,
            "add",
            "verification/greet",
            "requirements/greet.md",
            "interface/greet.json",
            "tasks/greet.lock",
            "verification/greet.lock",
        )
        _ = git(shell, "commit", "-qm", "supporting files")
    originals = {
        path: (path.read_bytes(), path.stat().st_mode)
        for path in (
            shell.root / "tasks/greet",
            shell.root / "verification/greet",
            shell.root / "requirements/greet.md",
            shell.root / "interface/greet.json",
            shell.root / "tasks/greet.lock",
            shell.root / "verification/greet.lock",
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
        "modify",
        "greet",
        "Use hello",
        input_text="Use hello, preserve spaces\n/submit\n/implement\n/draft\ny\ny\n",
    )
    assert result.returncode != 0
    assert "Verification passed" in result.stderr
    for path, expected in originals.items():
        assert (path.read_bytes(), path.stat().st_mode) == expected
    assert (shell.root / "verification/greet").exists() == has_supporting_files
    assert (shell.root / "interface/greet.json").exists() == has_supporting_files
    assert (shell.root / "tasks/greet.lock").exists() == has_supporting_files
    assert (shell.root / "verification/greet.lock").exists() == has_supporting_files
    assert git(shell, "ls-files", "--stage") == before_index
    hook.unlink()
    result = shell.run(
        "modify",
        "greet",
        input_text="Use hello\nUse hello, preserve spaces\n/submit\n/implement\n/draft\ny\ny\n",
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert shell.tool("greet", "Grace Hopper").stdout == "hello Grace Hopper\n"
    assert git(shell, "diff", "--cached", "--name-only") == "unrelated\n"
    expected = {
        "tasks/greet",
        "verification/greet",
        "requirements/greet.md",
        "interface/greet.json",
    }
    if has_supporting_files:
        expected.update(("tasks/greet.lock", "verification/greet.lock"))
    assert set(git(shell, "show", "--format=", "--name-only", "HEAD").splitlines()) == expected
    assert not (shell.root / "tasks/greet.lock").exists()
    assert not (shell.root / "verification/greet.lock").exists()


@pytest.mark.parametrize("state", ["dirty", "staged", "concurrent", "decline"])
@pytest.mark.parametrize(
    "relative",
    ["tasks/greet", "interface/greet.json", "tasks/greet.lock", "verification/greet.lock"],
)
def test_modify_never_overwrites_unapproved_changes(
    shell: Shell, state: str, relative: str
) -> None:
    home = prepare(shell)
    seed_command(shell)
    interface = shell.root / "interface/greet.json"
    interface.parent.mkdir()
    _ = interface.write_text('{"schemaVersion":1,"name":"greet","summary":"Old greeting"}')
    _ = git(shell, "add", "interface/greet.json")
    _ = git(shell, "commit", "-qm", "old interface")
    if relative.endswith(".lock"):
        lock = shell.root / relative
        lock.parent.mkdir(exist_ok=True)
        _ = lock.write_text("Old script lock\n")
        _ = git(shell, "add", relative)
        _ = git(shell, "commit", "-qm", "old script lock")
    task = shell.root / relative
    if state in ("dirty", "staged"):
        _ = task.write_text("user changes\n")
        if state == "staged":
            _ = git(shell, "add", relative)
    if state == "concurrent":
        _ = (home / "concurrent-edit").write_text(relative)
    before = task.read_text()
    head = git(shell, "rev-parse", "HEAD")
    answer = "n\n" if state == "decline" else "y\n"
    result = shell.run(
        "modify",
        "greet",
        "Use hello",
        input_text="Use hello, preserve spaces\n/submit\n/implement\n/draft\ny\n" + answer,
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
    result = shell.run("new", "Greet a person", input_text="1\ny\ny\n")
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
    assert result.stdout.startswith("Usage: greet")
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
    assert shell.run("new", "Greet a person", input_text="").returncode == 130
    assert not (shell.root / "drafts").exists()


@pytest.mark.parametrize("kind", ["file", "symlink", "directory", "fifo"])
def test_force_replaces_only_files_or_links_and_restores_them_on_commit_failure(
    shell: Shell, kind: str
) -> None:
    home = prepare(shell)
    destination = home / ".local/bin/abc"
    destination.parent.mkdir(parents=True)
    if kind == "directory":
        destination.mkdir()
        _ = (destination / "keep").write_text("keep directory")
    elif kind == "fifo":
        os.mkfifo(destination)
    elif kind == "symlink":
        destination.symlink_to("missing-original-target")
    else:
        _ = destination.write_text("original executable")
        destination.chmod(0o751)
    args = ("new", "abc", "Greet a person")
    assert shell.run(*args).returncode == 65
    assert not (home / "calls").exists()
    hook = shell.root / ".git/hooks/pre-commit"
    _ = hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    failed = shell.run(
        *args,
        "--force",
        input_text="1\n/submit\nUse hello, preserve spaces\n/submit\n/implement\n/draft\ny\ny\n",
    )
    assert failed.returncode != 0
    assert not (shell.root / "tasks/abc").exists()
    assert git(shell, "status", "--porcelain") == ""
    if kind in ("directory", "fifo"):
        if kind == "directory":
            assert (destination / "keep").read_text() == "keep directory"
        else:
            assert destination.is_fifo()
        assert not (home / "calls").exists()
        return
    if kind == "symlink":
        assert destination.readlink() == Path("missing-original-target")
    else:
        assert destination.read_text() == "original executable"
        assert destination.stat().st_mode & 0o777 == 0o751
    hook.unlink()
    passed = shell.run(
        "new",
        "--force",
        "abc",
        "Greet a person",
        input_text="1\n/submit\nUse hello, preserve spaces\n/submit\n/implement\n/draft\ny\ny\n",
    )
    assert passed.returncode == 0, passed.stderr + passed.stdout
    assert shell.tool("abc", "after replacement").stdout == "hello after replacement\n"
    assert not list(destination.parent.glob(".ayi-backup-*"))


@pytest.mark.parametrize("help_body", ["exit 0", "printf 'Usage: abc NAME\\n'; exit 23"])
def test_invalid_help_prevents_forced_installation(shell: Shell, help_body: str) -> None:
    home = prepare(shell)
    destination = home / ".local/bin/abc"
    destination.parent.mkdir(parents=True)
    _ = destination.write_text("keep original")
    pi = Path(shell.env["PATH"].split(":")[0]) / "pi"
    task = (
        '#!/bin/bash\n#MISE description="Greet a name"\n'
        f'if [ "${{1:-}}" = --help ]; then {help_body}; fi\n'
        'printf "hello %s\\n" "$1"\n'
    )
    with pi.open("a") as script:
        _ = script.write(f"\nif mode == 'create': Path('task').write_text({task!r})\n")
    result = shell.run(
        "new",
        "--force",
        "abc",
        "Greet a person",
        input_text="1\n/submit\nUse hello, preserve spaces\n/submit\n/implement\n/draft\ny\ny\n",
    )
    assert result.returncode == 65, result.stderr + result.stdout
    assert "tool must provide nonempty --help output and exit 0" in result.stderr
    assert destination.read_text() == "keep original"
    assert not (shell.root / "tasks/abc").exists()
    assert git(shell, "status", "--porcelain") == ""


def test_force_rebuilds_clean_tool_without_bypassing_review_or_dirty_protection(
    shell: Shell,
) -> None:
    home = prepare(shell)
    seed_command(shell)
    old = (shell.root / "tasks/greet").read_bytes()
    assert shell.run("new", "greet", "Use hello").returncode == 65
    args = ("new", "--force", "greet", "Use hello")
    denied = shell.run(
        *args, input_text="1\n/submit\nUse hello, preserve spaces\n/submit\n/implement\n/draft\nn\n"
    )
    assert denied.returncode == 1
    assert (shell.root / "tasks/greet").read_bytes() == old
    assert not (home / ".local/bin/greet").exists()
    result = shell.run(
        *args,
        input_text="1\n/submit\nUse hello, preserve spaces\n/submit\n/implement\n/draft\ny\ny\n",
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert shell.tool("greet", "recreated").stdout == "hello recreated\n"
    _ = (shell.root / "tasks/greet").write_text("uncommitted change\n")
    assert shell.run(*args).returncode == 65
    assert (shell.root / "tasks/greet").read_text() == "uncommitted change\n"
