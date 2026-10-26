from __future__ import annotations

import json
from pathlib import Path

from .conftest import Shell
from .test_factory import prepare


def session_files(shell: Shell) -> list[Path]:
    return sorted((Path(shell.env["XDG_STATE_HOME"]) / "ayi/sessions").glob("*/checkpoints/*.json"))


def test_answers_advance_immediately_with_checkpoints_resume_and_fork(shell: Shell) -> None:
    home = prepare(shell)
    first = shell.run(
        "new",
        "greet",
        "Greet a person",
        input_text="1\nUse hello, preserve spaces\n/pause\n",
    )
    assert first.returncode == 0, first.stderr
    checkpoints = session_files(shell)
    assert checkpoints, "Every answer needs a durable checkpoint"
    latest = json.loads(checkpoints[-1].read_text())
    assert latest["phase"]["kind"] == "plan"
    assert [answer["answer"] for answer in latest["answers"]] == [
        "greet NAME",
        "Use hello, preserve spaces",
    ]
    submitted = [
        json.loads(path.read_text())
        for path in checkpoints
        if json.loads(path.read_text())["phase"]["kind"] == "next"
    ]
    assert [len(state["answers"]) for state in submitted] == [0, 1, 2]
    assert "Submit this answer" not in first.stderr
    calls = [json.loads(line) for line in (home / "calls").read_text().splitlines()]
    assert [call["mode"] for call in calls] == ["interview"] * 3
    assert json.loads(calls[-1]["args"][-1])["answers"][-1]["answer"] == (
        "Use hello, preserve spaces"
    )
    session_id = checkpoints[-1].parents[1].name
    before = [path.read_bytes() for path in checkpoints]
    forked = shell.run("fork", session_id, input_text="/pause\n")
    assert forked.returncode == 0, forked.stderr
    assert [path.read_bytes() for path in checkpoints] == before
    resumed = shell.run("resume", session_id, input_text="/implement\n/draft\ny\ny\n")
    assert resumed.returncode == 0, resumed.stderr
    assert shell.tool("greet", "Space Name").stdout == "hello Space Name\n"
    calls = [json.loads(line) for line in (home / "calls").read_text().splitlines()]
    assert sum(call["mode"] == "create" for call in calls) == 1
    assert "#!/bin/bash" not in resumed.stdout + resumed.stderr


def test_controls_preserve_question_and_steer_only_future_design(shell: Shell) -> None:
    home = prepare(shell)
    pi = Path(shell.env["PATH"].split(":")[0]) / "pi"
    source = pi.read_text().replace(
        'if mode == "explain":',
        'if mode == "discuss":\n    print("Example: greet Ada prints hello Ada. No network is necessary.")\nelif mode == "explain":',
    )
    _ = pi.write_text(source)
    result = shell.run(
        "new",
        "greet",
        "Greet a person",
        "--design-model",
        "design/example:high",
        "--implementation-model",
        "implementation/example",
        input_text="/show-me\n/unclear\n/clarify Keep all output local\n1\nUse hello, preserve spaces\n/implement\n/draft\ny\ny\n",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.count("Example: greet Ada") == 2
    calls = [json.loads(line) for line in (home / "calls").read_text().splitlines()]
    assert [c["mode"] for c in calls[:4]] == ["interview", "discuss", "discuss", "interview"]
    continued = json.loads(calls[3]["args"][-1])
    assert continued["answers"] == []
    direction = continued["directions"][0]
    assert direction["after_answer"] == 0
    assert direction["instruction"] == "Keep all output local"
    assert direction["context"]["question"]["question"] == "How should greet receive a name?"
    assert direction["context"]["question"]["options"] == ["greet NAME", "echo NAME | greet"]
    for call in calls:
        arguments = call["args"]
        model = arguments[arguments.index("--model") + 1]
        assert model == (
            "implementation/example" if call["mode"] == "create" else "design/example:high"
        )
    assert "Keep all output local" in calls[-1]["args"][-1]


def test_edit_answer_invalidates_later_answers_but_keeps_checkpoints(shell: Shell) -> None:
    _ = prepare(shell)
    result = shell.run(
        "new",
        "greet",
        "Greet a person",
        input_text="1\nUse hello, preserve spaces\n/edit 1\n/pause\n",
    )
    assert result.returncode == 0, result.stderr
    path = session_files(shell)[-1]
    assert json.loads(path.read_text())["phase"]["answer"] == "greet NAME"
    result = shell.run("resume", path.parents[1].name, input_text="+ with a flag\n/pause\n")
    assert result.returncode == 0, result.stderr
    paths = session_files(shell)
    snapshots = [json.loads(path.read_text()) for path in paths]
    assert any(len(snapshot["answers"]) == 2 for snapshot in snapshots)
    assert snapshots[-1]["answers"][0]["answer"] == "greet NAME with a flag"
    assert len(snapshots[-1]["answers"]) == 1
    assert snapshots[-1]["phase"]["kind"] == "ask"
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in paths)
    assert paths[-1].parents[1].stat().st_mode & 0o777 == 0o700


def test_legacy_pending_answer_resumes_in_editor_without_confirmation(shell: Shell) -> None:
    home = prepare(shell)
    first = shell.run("new", "greet", "Greet a person", input_text="/pause\n")
    assert first.returncode == 0, first.stderr
    path = session_files(shell)[-1]
    state = json.loads(path.read_text())
    state["phase"] = {
        "kind": "answer",
        "question": state["phase"]["question"],
        "answer": "echo NAME | greet",
    }
    _ = path.write_text(json.dumps(state))
    resumed = shell.run(
        "resume", path.parents[1].name, input_text="\nUse hello, preserve spaces\n/pause\n"
    )
    assert resumed.returncode == 0, resumed.stderr
    assert "Current answer: echo NAME | greet" in resumed.stderr
    assert "Submit this answer" not in resumed.stderr
    calls = [json.loads(line) for line in (home / "calls").read_text().splitlines()]
    request = json.loads(calls[-1]["args"][-1])
    assert [answer["answer"] for answer in request["answers"]] == [
        "echo NAME | greet",
        "Use hello, preserve spaces",
    ]
    assert json.loads(session_files(shell)[-1].read_text())["phase"]["kind"] == "plan"


def test_implementation_needs_complete_design_and_explicit_action(shell: Shell) -> None:
    home = prepare(shell)
    result = shell.run("new", "greet", "Greet a person", input_text="/implement\n/pause\n")
    assert result.returncode == 0
    calls = [json.loads(line) for line in (home / "calls").read_text().splitlines()]
    assert [call["mode"] for call in calls] == ["interview"]
    assert not (shell.root / "tasks/greet").exists()


def test_generation_failure_keeps_plan_and_hides_model_output(shell: Shell) -> None:
    home = prepare(shell)
    pi = Path(shell.env["PATH"].split(":")[0]) / "pi"
    _ = pi.write_text(
        pi.read_text()
        + '\nif mode == "create":\n    print("PRIVATE SCRIPT BODY")\n    print("PRIVATE ERROR BODY", file=sys.stderr)\n    sys.exit(17)\n'
    )
    result = shell.run(
        "new",
        "greet",
        "Greet a person",
        input_text="1\nUse hello, preserve spaces\n/implement\n",
    )
    assert result.returncode == 17
    assert "PRIVATE" not in result.stdout + result.stderr
    paths = session_files(shell)
    assert json.loads(paths[-1].read_text())["phase"]["kind"] == "plan"
    log = next(paths[-1].parents[1].glob("drafts/*/generation.log"))
    assert "PRIVATE SCRIPT BODY" in log.read_text()
    assert "PRIVATE ERROR BODY" in log.read_text()
    assert not (home / ".local/bin/greet").exists()


def test_resume_rejects_changed_registry_and_invalid_checkpoints(shell: Shell) -> None:
    from .test_factory import git, seed_command

    home = prepare(shell)
    seed_command(shell)
    paused = shell.run("modify", "greet", "Use hello", input_text="/pause\n")
    assert paused.returncode == 0
    checkpoint = session_files(shell)[-1]
    session_id = checkpoint.parents[1].name
    _ = (shell.root / "tasks/greet").write_text("changed after pause\n")
    _ = git(shell, "add", "tasks/greet")
    _ = git(shell, "commit", "-qm", "changed")
    before = (home / "calls").read_bytes()
    result = shell.run("resume", session_id)
    assert result.returncode == 65
    assert "changed since the session started" in result.stderr
    assert (home / "calls").read_bytes() == before
    _ = checkpoint.write_text("{broken")
    assert shell.run("resume", session_id).returncode == 65
    assert shell.run("resume", "../escape").returncode == 65


def test_settings_select_codex_before_implementation(shell: Shell) -> None:
    import sys

    home = prepare(shell)
    codex = Path(shell.env["PATH"].split(":")[0]) / "codex"
    bundle = {
        "proposal": 'name = "greet"\nstatus = "ready"\n',
        "requirements": "Print hello NAME and preserve spaces.\n",
        "task": '#!/bin/bash\n#MISE description="Greet a name"\ncase "${1:-}" in --help|-h) printf "Usage: greet NAME\\n"; exit 0;; -*) exit 64;; esac\nprintf "hello %s\\n" "$1"\n',
        "verification": '#!/bin/bash\nset -eu\ntest "$("$1" "Space Name")" = "hello Space Name"\n',
        "interface": json.dumps(
            {
                "schemaVersion": 1,
                "name": "greet",
                "summary": "Greet a name",
                "positionals": [{"name": "NAME", "required": True, "completion": {"kind": "none"}}],
            }
        ),
    }
    _ = codex.write_text(
        f"#!{sys.executable}\nimport sys,json,os\nfrom pathlib import Path\n"
        "args=sys.argv[1:]\n"
        'Path(os.environ["HOME"], "codex-args").write_text(json.dumps(args))\n'
        f'Path(args[args.index("--output-last-message")+1]).write_text({json.dumps(bundle)!r})\n'
        'print("DO NOT PRINT THIS SCRIPT")\n'
    )
    codex.chmod(0o755)
    result = shell.run(
        "new",
        "greet",
        "Greet a person",
        input_text="1\nUse hello, preserve spaces\n/settings\ndesigner/example\ncodex\nbuilder/example\n/implement\n/draft\ny\ny\n",
    )
    assert result.returncode == 0, result.stderr
    args = json.loads((home / "codex-args").read_text())
    assert args[0] == "exec"
    assert args[args.index("--model") + 1] == "builder/example"
    assert args[args.index("--sandbox") + 1] == "read-only"
    assert "--ignore-user-config" in args
    assert "Use hello, preserve spaces" in args[-1]
    assert "DO NOT PRINT THIS SCRIPT" not in result.stdout + result.stderr
    assert shell.tool("greet", "Space Name").stdout == "hello Space Name\n"
    calls = [json.loads(line) for line in (home / "calls").read_text().splitlines()]
    assert all(call["mode"] != "create" for call in calls)


def test_fork_specific_checkpoint_and_offline_overview(shell: Shell) -> None:
    home = prepare(shell)
    first = shell.run(
        "new",
        "greet",
        "Greet a person",
        input_text="1\nUse hello, preserve spaces\n/pause\n",
    )
    assert first.returncode == 0
    paths = session_files(shell)
    session_id = paths[-1].parents[1].name
    checkpoint = next(path for path in paths if len(json.loads(path.read_text())["answers"]) == 1)
    prior = (home / "calls").read_bytes()
    overview = shell.run("review", session_id)
    assert overview.returncode == 0
    assert "How should greet receive a name?" in overview.stderr
    assert "Use hello, preserve spaces" in overview.stderr
    assert "Checkpoint" in overview.stderr
    assert (home / "calls").read_bytes() == prior
    assert shell.run("sessions").returncode == 0
    forked = shell.run(
        "fork", session_id, "--checkpoint", str(int(checkpoint.stem)), input_text="/pause\n"
    )
    assert forked.returncode == 0
    directories = {path.parents[1] for path in session_files(shell)}
    child = next(directory for directory in directories if directory.name != session_id)
    child_state = json.loads(min((child / "checkpoints").glob("*.json")).read_text())
    assert child_state["phase"]["kind"] == "next"
    assert [answer["answer"] for answer in child_state["answers"]] == ["greet NAME"]
    assert json.loads(paths[-1].read_text())["phase"]["kind"] == "plan"


def test_eof_keeps_submitted_answer_and_resume_does_not_regenerate_draft(shell: Shell) -> None:
    home = prepare(shell)
    first = shell.run("new", "greet", "Greet a person", input_text="1\n")
    assert first.returncode == 130
    paths = session_files(shell)
    state = json.loads(paths[-1].read_text())
    assert state["phase"]["kind"] == "ask"
    assert state["phase"]["question"]["topic"] == "requirements"
    assert [answer["answer"] for answer in state["answers"]] == ["greet NAME"]
    session_id = paths[-1].parents[1].name
    generated = shell.run(
        "resume",
        session_id,
        input_text="Use hello, preserve spaces\n/implement\n/pause\n",
    )
    assert generated.returncode == 0
    resumed = shell.run("resume", session_id, input_text="/draft\ny\ny\n")
    assert resumed.returncode == 0, resumed.stderr
    calls = [json.loads(line) for line in (home / "calls").read_text().splitlines()]
    assert sum(call["mode"] == "create" for call in calls) == 1


def test_session_lock_rejects_concurrent_resume(shell: Shell) -> None:
    import fcntl

    home = prepare(shell)
    assert shell.run("new", "greet", "Greet a person", input_text="/pause\n").returncode == 0
    directory = session_files(shell)[-1].parents[1]
    before = (home / "calls").read_bytes()
    with (directory / "lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = shell.run("resume", directory.name)
        assert result.returncode == 65
        assert "already open" in result.stderr
    assert (home / "calls").read_bytes() == before


def test_resume_rejects_inconsistent_plan_before_model_or_implementation(shell: Shell) -> None:
    home = prepare(shell)
    result = shell.run(
        "new",
        "greet",
        "Greet a person",
        input_text="1\nUse hello, preserve spaces\n/pause\n",
    )
    assert result.returncode == 0
    path = session_files(shell)[-1]
    state = json.loads(path.read_text())
    before = (home / "calls").read_bytes()
    state["phase"]["plan"]["name"] = "different"
    _ = path.write_text(json.dumps(state))
    rejected = shell.run("resume", path.parents[1].name, input_text="/pause\n")
    assert rejected.returncode == 65
    assert "invalid session checkpoint" in rejected.stderr
    assert (home / "calls").read_bytes() == before


def test_twentieth_answer_can_reach_final_review(shell: Shell) -> None:
    _ = prepare(shell)
    result = shell.run(
        "new",
        "greet",
        "Greet a person",
        input_text="1\nUse hello, preserve spaces\n/pause\n",
    )
    assert result.returncode == 0
    path = session_files(shell)[-1]
    state = json.loads(path.read_text())
    state["answers"] = [state["answers"][0], *[state["answers"][1]] * 19]
    state["phase"] = {"kind": "next"}
    _ = path.write_text(json.dumps(state))
    resumed = shell.run("resume", path.parents[1].name, input_text="/pause\n")
    assert resumed.returncode == 0, resumed.stderr
    assert "Proposed approach" in resumed.stderr
