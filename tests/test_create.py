from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from .conftest import Shell, copy_runtime, init_git


def new_command(
    shell: Shell, prompt: str, *, input_text: str = ""
) -> subprocess.CompletedProcess[str]:
    return shell.run(
        "new", "greet", prompt, input_text="1\n/submit\n/implement\n/draft\n" + input_text
    )


@pytest.mark.parametrize("reject_commit", [False, True])
def test_creation_keeps_the_dotfiles_index_separate(shell: Shell, reject_commit: bool) -> None:
    prepare(shell)
    home = Path(shell.env["HOME"])
    metadata = home / ".dotfiles.git"
    _ = subprocess.run(["git", "init", "--bare", str(metadata)], env=shell.env, check=True)
    base = ["git", f"--git-dir={metadata}", f"--work-tree={home}"]
    for args in (["remote", "add", "origin", "git@github.com:amichne/dotfiles.git"],):
        _ = subprocess.run([*base, *args], env=shell.env, check=True)
    unrelated = home / "unrelated"
    _ = unrelated.write_text("keep staged in dotfiles\n")
    _ = subprocess.run([*base, "add", "--", "unrelated"], env=shell.env, check=True)
    before = subprocess.run(
        [*base, "ls-files", "--stage", "-z"], env=shell.env, capture_output=True, check=True
    ).stdout
    if reject_commit:
        hook = shell.root / ".git/hooks/pre-commit"
        _ = hook.write_text('#!/bin/sh\nprintf attempted > "$HOME/hook-ran"\nexit 1\n')
        hook.chmod(0o755)
    result = new_command(shell, "Greet", input_text="y\ny\n")
    task = shell.root / "tasks/greet"
    if reject_commit:
        assert result.returncode != 0
        assert (home / "hook-ran").read_text() == "attempted"
        assert not task.exists()
    else:
        assert result.returncode == 0, result.stderr + result.stdout
        assert task.exists()
        committed = subprocess.run(
            ["git", "-C", str(shell.root), "show", "--format=", "--name-only", "HEAD"],
            env=shell.env,
            capture_output=True,
            text=True,
            check=True,
        )
        assert set(committed.stdout.splitlines()) == {
            "tasks/greet",
            "verification/greet",
            "requirements/greet.md",
            "interface/greet.json",
        }
    staged = subprocess.run(
        [*base, "diff", "--cached", "--name-only"],
        env=shell.env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert staged.stdout == "unrelated\n"
    after = subprocess.run(
        [*base, "ls-files", "--stage", "-z"], env=shell.env, capture_output=True, check=True
    ).stdout
    assert after == before
    assert (shell.root / ".git").is_dir()


def prepare(shell: Shell, *, ready: bool = True, passes: bool = True) -> None:
    copy_runtime(shell, "create.py", "factory.py", "install.py", "prompts", ".gitignore")
    init_git(shell)
    fake = Path(shell.env["PATH"].split(":")[0]) / "pi"
    task = (
        '#!/bin/bash\n#MISE description="Print a greeting"\n'
        'case "${1:-}" in --help|-h) printf "Usage: greet NAME\\n"; exit 0;; -*) exit 64;; esac\n'
        'printf "hello %s\\n" "$1"\n'
    )
    interface = json.dumps(
        {
            "schemaVersion": 1,
            "name": "greet",
            "summary": "Print a greeting",
            "positionals": [{"name": "NAME", "required": True, "completion": {"kind": "none"}}],
        }
    )
    _ = fake.write_text(
        f"#!{sys.executable}\nfrom pathlib import Path\nimport sys,json\n"
        "if '--no-tools' in sys.argv:\n"
        " request = json.loads(sys.argv[-1])\n"
        " reply = dict(status='question', topic='usage', question='How should greet receive input?', options=['greet NAME','greet --name NAME']) if request['required_topic'] else dict(status='ready', name='greet', requirements='Print a literal greeting.')\n"
        " print(json.dumps(reply))\n sys.exit(0)\n"
        "Path('pi-args').write_text(repr(sys.argv[1:]))\n"
        f"Path('proposal.toml').write_text('name = \\\"greet\\\"\\nstatus = \\\"{'ready' if ready else 'needs-input'}\\\"\\n')\n"
        "Path('requirements.md').write_text('Print a literal greeting. No network.\\n')\n"
        f"Path('task').write_text({task!r})\n"
        f"Path('interface.json').write_text({interface!r})\n"
        "Path('verify').write_text('#!/bin/bash\\nset -eu\\n"
        + ('test \\"$(\\"$1\\" world)\\" = \\"hello world\\"\\n' if passes else "exit 17\\n")
        + "')\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)


def test_review_verify_commit_register(shell: Shell) -> None:
    prepare(shell)
    # Unrelated staged work must remain staged and outside the generated commit.
    unrelated = shell.root / "unrelated"
    _ = unrelated.write_text("keep", encoding="utf-8")
    _ = subprocess.run(
        ["git", "-C", str(shell.root), "add", "unrelated"], env=shell.env, check=True
    )
    result = new_command(shell, "Print a greeting with a name", input_text="y\ny\n")
    assert result.returncode == 0, result.stderr + result.stdout
    assert result.stderr.index("Print a greeting") < result.stderr.index("Run this verification?")
    assert "Verification passed" in result.stderr
    assert "Invocation: greet" in result.stdout
    task = shell.root / "tasks/greet"
    assert task.is_file()
    execution = shell.tool("greet", "space name")
    assert execution.returncode == 0, execution.stderr
    assert execution.stdout == "hello space name\n"
    tracked = subprocess.run(
        ["git", "-C", str(shell.root), "show", "--format=", "--name-only", "HEAD"],
        env=shell.env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert set(tracked.stdout.splitlines()) == {
        "tasks/greet",
        "verification/greet",
        "requirements/greet.md",
        "interface/greet.json",
    }
    assert "unrelated" not in tracked.stdout
    staged = subprocess.run(
        ["git", "-C", str(shell.root), "diff", "--cached", "--name-only"],
        env=shell.env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert staged.stdout == "unrelated\n"


def test_unresolved_requirements_cannot_register(shell: Shell) -> None:
    prepare(shell, ready=False)
    result = new_command(shell, "Greet", input_text="y\ny\n")
    assert result.returncode != 0
    assert "needs-input" in result.stderr
    assert not (shell.root / "tasks/greet").exists()


def test_failed_verification_cannot_register(shell: Shell) -> None:
    prepare(shell, passes=False)
    result = new_command(shell, "Greet", input_text="y\ny\n")
    assert result.returncode == 17, result.stderr + result.stdout
    assert not (shell.root / "tasks/greet").exists()


def test_decline_keeps_draft_unregistered(shell: Shell) -> None:
    prepare(shell)
    result = new_command(shell, "Greet", input_text="n\n")
    assert result.returncode != 0
    assert not (shell.root / "tasks/greet").exists()
    assert list((Path(shell.env["XDG_STATE_HOME"]) / "ayi/sessions").glob("*/drafts/*/task"))


def test_decline_registration_keeps_verified_task_unregistered(shell: Shell) -> None:
    prepare(shell)
    result = new_command(shell, "Greet", input_text="y\nn\n")
    assert "Verification passed" in result.stderr
    assert result.returncode != 0
    assert not (shell.root / "tasks/greet").exists()


def test_commit_failure_removes_unregistered_files(shell: Shell) -> None:
    prepare(shell)
    hook = shell.root / ".git/hooks/pre-commit"
    _ = hook.write_text("#!/bin/bash\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    result = new_command(shell, "Greet", input_text="y\ny\n")
    assert "Verification passed" in result.stderr
    assert result.returncode != 0
    assert not (shell.root / "tasks/greet").exists()
    assert not (shell.root / "verification/greet").exists()
    assert not (Path(shell.env["HOME"]) / ".local/bin/greet").is_symlink()


@pytest.mark.parametrize("state", ["deleted", "staged-deletion", "staged-addition"])
def test_deleted_git_path_cannot_be_reused(shell: Shell, state: str) -> None:
    prepare(shell)
    task = shell.root / "tasks/greet"
    _ = task.write_text("keep the existing source\n", encoding="utf-8")
    commands = [["add", "tasks/greet"]]
    if state != "staged-addition":
        commands.append(["commit", "-qm", "existing command"])
    for command in commands:
        _ = subprocess.run(["git", "-C", str(shell.root), *command], env=shell.env, check=True)
    task.unlink()
    if state == "staged-deletion":
        _ = subprocess.run(
            ["git", "-C", str(shell.root), "add", "tasks/greet"], env=shell.env, check=True
        )
    command = ["git", "-C", str(shell.root), "status", "--porcelain=v1"]
    before = subprocess.run(command, env=shell.env, capture_output=True, check=True).stdout
    head = subprocess.run(
        ["git", "-C", str(shell.root), "rev-parse", "HEAD"],
        env=shell.env,
        capture_output=True,
        check=True,
    ).stdout
    result = new_command(shell, "Greet", input_text="y\ny\n")
    assert result.returncode == 65, result.stderr + result.stdout
    assert "command already exists" in result.stderr
    assert not task.exists()
    assert subprocess.run(command, env=shell.env, capture_output=True, check=True).stdout == before
    assert (
        subprocess.run(
            ["git", "-C", str(shell.root), "rev-parse", "HEAD"],
            env=shell.env,
            capture_output=True,
            check=True,
        ).stdout
        == head
    )


def test_generated_command_survives_clone(shell: Shell) -> None:
    prepare(shell)
    assert new_command(shell, "Greet", input_text="y\ny\n").returncode == 0
    clone = shell.cwd / "generated clone"
    _ = subprocess.run(
        ["git", "clone", "--quiet", str(shell.root), str(clone)],
        env=shell.env,
        check=True,
    )
    fresh_home = shell.cwd / "fresh-home"
    fresh_home.mkdir()
    env = dict(
        shell.env,
        HOME=str(fresh_home),
        XDG_CONFIG_HOME=str(fresh_home / ".config"),
        XDG_DATA_HOME=str(fresh_home / ".local/share"),
        XDG_STATE_HOME=str(fresh_home / ".local/state"),
        XDG_CACHE_HOME=str(fresh_home / ".cache"),
    )
    fresh = Shell(clone, shell.cwd, env)
    assert fresh.run("install", "apply").returncode == 0
    result = fresh.tool("greet", "from Git")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "hello from Git\n"


def test_mutated_verification_input_cannot_register(shell: Shell) -> None:
    prepare(shell)
    pi = Path(shell.env["PATH"].split(":")[0]) / "pi"
    marker = shell.cwd / "unreviewed-code-executed"
    replacement = f"#!{sys.executable}\nfrom pathlib import Path\nPath({str(marker)!r}).touch()\n"
    verification = (
        f"#!{sys.executable}\nfrom pathlib import Path\nimport sys\n"
        f"Path(sys.argv[1]).write_text({replacement!r})\n"
    )
    with pi.open("a") as script:
        _ = script.write(f"Path('verify').write_text({verification!r})\n")
    result = new_command(shell, "Greet", input_text="y\ny\n")
    assert result.returncode == 65, result.stderr
    assert "verification changed its inputs" in result.stderr
    assert not (shell.root / "tasks/greet").exists()
    assert not marker.exists()


@pytest.mark.parametrize("marker", ["# MISE", "# [MISE]", "// MISE", ":: MISE", "#MISE\t"])
def test_alternate_mise_metadata_cannot_bypass_draft_contract(shell: Shell, marker: str) -> None:
    prepare(shell)
    pi = Path(shell.env["PATH"].split(":")[0]) / "pi"
    with pi.open("a") as script:
        line = f'{marker} env={{UNREVIEWED="value"}}\n'
        _ = script.write(f"with Path('task').open('a') as f: f.write({line!r})\n")
    result = new_command(shell, "Greet", input_text="y\ny\n")
    assert result.returncode == 65, result.stderr
    assert "invalid draft" in result.stderr
    assert not (shell.root / "tasks/greet").exists()


@pytest.mark.parametrize(
    "replacement",
    [
        None,
        '{"schemaVersion":1,"name":"other","summary":"Wrong name"}',
        '{"schemaVersion":1,"name":"greet","summary":"Bad provider","positionals":[{"name":"input","completion":{"kind":"exec","command":"pi"}}]}',
    ],
)
def test_missing_or_invalid_manifest_cannot_register(shell: Shell, replacement: str | None) -> None:
    prepare(shell)
    pi = Path(shell.env["PATH"].split(":")[0]) / "pi"
    with pi.open("a") as script:
        _ = script.write(
            "Path('interface.json').unlink()\n"
            if replacement is None
            else f"Path('interface.json').write_text({replacement!r})\n"
        )
    result = new_command(shell, "Greet", input_text="y\ny\n")
    assert result.returncode == 65, result.stderr
    assert "invalid draft" in result.stderr
    assert not (shell.root / "tasks/greet").exists()
    assert not (shell.root / "interface/greet.json").exists()


def test_manifest_is_a_reviewed_immutable_verification_input(shell: Shell) -> None:
    prepare(shell)
    pi = Path(shell.env["PATH"].split(":")[0]) / "pi"
    verification = '#!/bin/bash\nset -eu\ntest -s "$2"\nprintf tampered > "$2"\n'
    with pi.open("a") as script:
        _ = script.write(f"Path('verify').write_text({verification!r})\n")
    result = new_command(shell, "Greet", input_text="y\ny\n")
    assert result.returncode == 65, result.stderr
    assert "verification changed its inputs" in result.stderr
    assert not (shell.root / "interface/greet.json").exists()


@pytest.mark.parametrize("failure", ["undocumented", "short-help", "unknown-option"])
def test_independent_interface_checks_reject_parser_drift(shell: Shell, failure: str) -> None:
    prepare(shell)
    pi = Path(shell.env["PATH"].split(":")[0]) / "pi"
    if failure == "undocumented":
        # A prefix is not the declared option spelling.
        patch = "document=json.loads(Path('interface.json').read_text()); document['options']=[dict(names=['--sort'],description='Sort')]; Path('interface.json').write_text(json.dumps(document))\n"
        patch += "Path('task').write_text(Path('task').read_text().replace('Usage: greet NAME', 'Usage: greet NAME --sorter'))\n"
    elif failure == "short-help":
        patch = (
            "Path('task').write_text(Path('task').read_text().replace('--help|-h)', '--help)'))\n"
        )
    else:
        patch = "Path('task').write_text(Path('task').read_text().replace('-*) exit 64;;', '-*) exit 0;;'))\n"
    with pi.open("a") as script:
        _ = script.write(patch)
    result = new_command(shell, "Greet", input_text="y\ny\n")
    assert result.returncode == 65, result.stderr
    expected = {
        "undocumented": "declared options missing",
        "short-help": "nonempty -h output",
        "unknown-option": "reject unknown options",
    }
    assert expected[failure] in result.stderr
    assert not (shell.root / "interface/greet.json").exists()


def test_generated_options_verify_install_and_complete_together(shell: Shell) -> None:
    prepare(shell)
    task = """#!/bin/bash
#MISE description="Greet with a style"
case "${1:-}" in
    --help|-h) printf 'Usage: greet [--style|-s friendly|formal] NAME\n'; exit 0;;
esac
style=friendly
case "${1:-}" in
    --style|-s)
        [ "$#" -ge 2 ] || exit 64
        style=$2
        shift 2;;
    --style=*) style=${1#*=}; shift;;
esac
[ "$#" -eq 1 ] || exit 64
case "$1" in -*) exit 64;; esac
case "$style" in
    friendly) printf 'hello %s\n' "$1";;
    formal) printf 'Good day, %s\n' "$1";;
    *) exit 64;;
esac
"""
    document = {
        "schemaVersion": 1,
        "name": "greet",
        "summary": "Greet with a style",
        "positionals": [{"name": "NAME", "required": True, "completion": {"kind": "none"}}],
        "options": [
            {
                "names": ["--style", "-s"],
                "description": "Greeting style",
                "value": {
                    "name": "STYLE",
                    "completion": {"kind": "choices", "values": ["friendly", "formal"]},
                },
            }
        ],
        "examples": ["greet --style formal Ada"],
    }
    verification = f"""#!{sys.executable}
import json, subprocess, sys
from pathlib import Path
task, interface = sys.argv[1:]
assert json.loads(Path(interface).read_text())["options"][0]["value"]["completion"]["values"] == ["friendly", "formal"]
for flag in ("--style", "-s"):
    for style, expected in (("friendly", "hello Ada Lovelace\\n"), ("formal", "Good day, Ada Lovelace\\n")):
        result = subprocess.run([task, flag, style, "Ada Lovelace"], capture_output=True, text=True)
        assert result.returncode == 0 and result.stdout == expected
for args in (["--style", "invalid", "Ada"], ["--style"], [], ["--unknown"]):
    assert subprocess.run([task, *args], capture_output=True).returncode != 0
"""
    pi = Path(shell.env["PATH"].split(":")[0]) / "pi"
    with pi.open("a") as script:
        _ = script.write(
            f"Path('task').write_text({task!r})\nPath('interface.json').write_text({json.dumps(document)!r})\nPath('verify').write_text({verification!r})\n"
        )
    result = new_command(shell, "Greet", input_text="y\ny\n")
    assert result.returncode == 0, result.stderr
    assert (
        shell.tool("greet", "--style", "formal", "Grace Hopper").stdout
        == "Good day, Grace Hopper\n"
    )
    assert (
        shell.tool("greet", "--style=formal", "Grace Hopper").stdout == "Good day, Grace Hopper\n"
    )
    candidates = shell.run(
        "complete",
        input_text=json.dumps(
            {"command": "greet", "args": ["--style", "f"], "cursor": 1, "cwd": str(shell.cwd)}
        ),
    )
    assert candidates.returncode == 0, candidates.stderr
    assert [row["value"] for row in json.loads(candidates.stdout)] == ["friendly", "formal"]
    assert json.loads((shell.root / "interface/greet.json").read_text()) == document


UV_HEADER = """#!/usr/bin/env -S uv run --locked --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# [tool.uv]
# no-build = true
# ///
"""


def prepare_uv(shell: Shell, *, verifier_uv: bool = False, patch: str = "") -> None:
    prepare(shell)
    task = (
        UV_HEADER
        + """#MISE description="Print a greeting"
import argparse
parser = argparse.ArgumentParser(prog="greet")
parser.add_argument("NAME")
args = parser.parse_args()
print("hello " + args.NAME)
"""
    )
    verification = (
        (UV_HEADER if verifier_uv else f"#!{sys.executable}\n")
        + """import subprocess
import sys
result = subprocess.run([sys.argv[1], "Ada Lovelace; $HOME"], capture_output=True, text=True)
assert result.returncode == 0, result.stderr
assert result.stdout == "hello Ada Lovelace; $HOME\\n"
assert subprocess.run([sys.argv[1]], capture_output=True).returncode != 0
"""
    )
    pi = Path(shell.env["PATH"].split(":")[0]) / "pi"
    with pi.open("a") as script:
        _ = script.write(
            f"Path('task').write_text({task!r})\n"
            f"Path('verify').write_text({verification!r})\n" + patch
        )


@pytest.mark.parametrize("verifier_uv", [False, True])
def test_locked_uv_draft_survives_verification_registration_and_clone(
    shell: Shell, verifier_uv: bool
) -> None:
    prepare_uv(shell, verifier_uv=verifier_uv)
    result = new_command(shell, "Greet", input_text="y\ny\n")
    assert result.returncode == 0, result.stderr
    installed = shell.tool("greet", "Installed name")
    assert installed.returncode == 0, installed.stderr
    assert installed.stdout == "hello Installed name\n"
    assert "Task lock:" in result.stderr
    draft = next(Path(shell.env["XDG_STATE_HOME"]).glob("ayi/sessions/*/drafts/*"))
    assert (shell.root / "tasks/greet.lock").read_bytes() == (draft / "task.lock").read_bytes()
    assert (shell.root / "verification/greet.lock").exists() == verifier_uv
    if verifier_uv:
        assert "Verification lock:" in result.stderr
        assert (shell.root / "verification/greet.lock").read_bytes() == (
            draft / "verify.lock"
        ).read_bytes()
    clone = shell.cwd / "locked clone with spaces"
    _ = subprocess.run(
        ["git", "clone", "-q", str(shell.root), str(clone)], env=shell.env, check=True
    )
    execution = subprocess.run(
        [str(clone / "tasks/greet"), "Grace Hopper; $HOME"],
        cwd=shell.cwd,
        env=dict(shell.env, UV_OFFLINE="1", UV_PYTHON=sys.executable),
        capture_output=True,
        text=True,
        check=False,
    )
    assert execution.returncode == 0, execution.stderr
    assert execution.stdout == "hello Grace Hopper; $HOME\n"


def test_uv_lock_preparation_never_executes_unapproved_source(shell: Shell) -> None:
    marker = shell.cwd / "unapproved"
    patch = f"with Path('task').open('a') as task: task.write({f'from pathlib import Path; Path({str(marker)!r}).touch()'!r})\n"
    prepare_uv(shell, verifier_uv=True, patch=patch)
    result = new_command(shell, "Greet", input_text="n\n")
    assert "Task lock:" in result.stderr
    assert "Verification lock:" in result.stderr
    assert not marker.exists()
    assert not (shell.root / "tasks/greet").exists()


@pytest.mark.parametrize("filename", ["task.lock", "verify.lock"])
@pytest.mark.parametrize("change", ["rewrite", "delete", "symlink"])
def test_mutated_script_lock_cannot_register(shell: Shell, filename: str, change: str) -> None:
    mutation = {
        "rewrite": f"Path({filename!r}).write_text('changed')\n",
        "delete": f"Path({filename!r}).unlink()\n",
        "symlink": f"Path({filename!r}).unlink()\nPath({filename!r}).symlink_to('interface.json')\n",
    }[change]
    verification = f"#!{sys.executable}\nfrom pathlib import Path\n" + mutation
    if filename == "verify.lock":
        verification = UV_HEADER + verification.split("\n", 1)[1]
    prepare_uv(shell, patch=f"Path('verify').write_text({verification!r})\n")
    result = new_command(shell, "Greet", input_text="y\ny\n")
    assert result.returncode == 65, result.stderr
    assert "verification changed its inputs" in result.stderr
    assert not (shell.root / "tasks/greet").exists()


def test_resume_preserves_reviewed_script_locks(shell: Shell) -> None:
    prepare_uv(shell, verifier_uv=True)
    declined = new_command(shell, "Greet", input_text="n\n")
    assert declined.returncode != 0
    session = next(Path(shell.env["XDG_STATE_HOME"]).glob("ayi/sessions/*"))
    draft = next((session / "drafts").iterdir())
    locks = {name: (draft / name).read_bytes() for name in ("task.lock", "verify.lock")}
    result = shell.run("resume", session.name, input_text="/draft\ny\ny\n")
    assert result.returncode == 0, result.stderr
    assert {name: (draft / name).read_bytes() for name in locks} == locks


@pytest.mark.parametrize("state", ["stale", "symlink", "invalid-metadata"])
def test_invalid_script_lock_cannot_reach_verification(shell: Shell, state: str) -> None:
    patch = {
        "stale": "Path('task.lock').write_text('version = 1\\nrevision = 3\\nrequires-python = \">=3.13\"\\n')\n",
        "symlink": "Path('task.lock').symlink_to('requirements.md')\n",
        "invalid-metadata": "Path('task').write_text(Path('task').read_text().replace('dependencies = []', 'dependencies = nope'))\n",
    }[state]
    prepare_uv(shell, patch=patch)
    result = new_command(shell, "Greet", input_text="y\ny\n")
    assert result.returncode != 0
    assert "Run this verification?" not in result.stderr
    assert not (shell.root / "tasks/greet").exists()
