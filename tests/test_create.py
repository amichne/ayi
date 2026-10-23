from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from .conftest import Shell, copy_runtime, init_git


def new_command(
    shell: Shell, prompt: str, *, input_text: str = ""
) -> subprocess.CompletedProcess[str]:
    return shell.run("new", "greet", prompt, input_text="1\n" + input_text)


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
    _ = fake.write_text(
        f"#!{sys.executable}\nfrom pathlib import Path\nimport sys,json\n"
        "if '--no-tools' in sys.argv:\n"
        " request = json.loads(sys.argv[-1])\n"
        " reply = dict(status='question', topic='usage', question='How should greet receive input?', options=['greet NAME','greet --name NAME']) if request['required_topic'] else dict(status='ready', name='greet', requirements='Print a literal greeting.')\n"
        " print(json.dumps(reply))\n sys.exit(0)\n"
        "Path('pi-args').write_text(repr(sys.argv[1:]))\n"
        f"Path('proposal.toml').write_text('name = \\\"greet\\\"\\nstatus = \\\"{'ready' if ready else 'needs-input'}\\\"\\n')\n"
        "Path('requirements.md').write_text('Print a literal greeting. No network.\\n')\n"
        'Path(\'task\').write_text(\'#!/bin/bash\\n#MISE description="Print a greeting"\\nprintf \\"hello %s\\\\n\\" \\"$1\\"\\n\')\n'
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
    assert list((shell.root / "drafts").glob("*/task"))


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
