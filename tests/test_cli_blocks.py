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
import tomllib
from collections.abc import Sequence
from pathlib import Path

import pytest
from pydantic import ValidationError

import cli
import completion
from interface import Interface


def contract() -> Interface:
    return Interface.model_validate(
        {
            "schemaVersion": 1,
            "runtime": "python",
            "name": "sample",
            "summary": "Inspect a sample",
            "options": [
                {
                    "names": ["--name", "-n"],
                    "description": "Sample name",
                    "required": True,
                    "prompt": "Choose a name",
                    "value": {"name": "NAME", "completion": {"kind": "none"}},
                },
                {
                    "names": ["--style", "-s"],
                    "description": "Display style",
                    "default": "short",
                    "env": "SAMPLE_STYLE",
                    "value": {
                        "name": "STYLE",
                        "completion": {"kind": "choices", "values": ["short", "long"]},
                    },
                },
                {
                    "names": ["--count"],
                    "description": "Sample count",
                    "default": "3",
                    "env": "SAMPLE_COUNT",
                    "value": {
                        "name": "COUNT",
                        "type": "integer",
                        "completion": {"kind": "none"},
                    },
                },
                {"names": ["--quiet"], "description": "Suppress optional messages"},
                {
                    "names": ["--tag", "-t"],
                    "description": "Sample tag",
                    "repeatable": True,
                    "value": {"name": "TAG", "completion": {"kind": "none"}},
                },
            ],
            "positionals": [{"name": "files", "repeatable": True, "completion": {"kind": "file"}}],
            "output": ["text", "json"],
        }
    )


def environment(tmp_path: Path) -> dict[str, str]:
    return {
        "HOME": str(tmp_path / "home"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
    }


def write_settings(tmp_path: Path, source: str) -> None:
    path = tmp_path / "config/ayi-tools/sample.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(source)


def test_defaults_config_environment_and_arguments_keep_their_source(tmp_path: Path) -> None:
    env = environment(tmp_path)
    default = cli.resolve(contract(), ["--name", "Ada"], env, can_prompt=False)
    assert isinstance(default, cli.Invocation)
    assert default.text("style") == "short"
    assert default.integer("count") == 3
    assert default.sources["style"] == "default"
    assert default.flag("quiet") is False
    assert default.many("tag") == ()

    write_settings(tmp_path, '[defaults]\nstyle = "long"\ncount = 4\nquiet = true\n')
    configured = cli.resolve(contract(), ["--name", "Ada"], env, can_prompt=False)
    assert isinstance(configured, cli.Invocation)
    assert configured.text("style") == "long"
    assert configured.integer("count") == 4
    assert configured.sources["style"] == "config"
    assert configured.flag("quiet") is True

    env.update(SAMPLE_STYLE="short", SAMPLE_COUNT="5")
    inherited = cli.resolve(contract(), ["--name", "Ada"], env, can_prompt=False)
    assert isinstance(inherited, cli.Invocation)
    assert inherited.text("style") == "short"
    assert inherited.integer("count") == 5
    assert inherited.sources["style"] == "environment"

    explicit = cli.resolve(
        contract(), ["--name", "Ada", "--style=long", "--count", "6"], env, can_prompt=False
    )
    assert isinstance(explicit, cli.Invocation)
    assert explicit.text("style") == "long"
    assert explicit.integer("count") == 6
    assert explicit.sources["style"] == "argument"


@pytest.mark.parametrize(
    "source",
    [
        '[defaults]\nunknown = "value"\n',
        '[defaults]\nstyle = "other"\n',
        "[defaults]\ncount = true\n",
        '[defaults]\ncount = "not-a-number"\n',
        '[defaults]\ntag = ["one"]\n',
        '[defaults]\nquiet = "yes"\n',
        '[execution]\ncommand = "touch forbidden"\n',
        "defaults = 1\n",
        "broken [[",
    ],
)
def test_invalid_settings_reject_before_interaction(tmp_path: Path, source: str) -> None:
    write_settings(tmp_path, source)

    def forbidden_entry(_label: str, _options: tuple[str, ...]) -> str:
        pytest.fail("Invalid settings must reject before a prompt")

    result = cli.resolve(
        contract(), [], environment(tmp_path), can_prompt=True, entry=forbidden_entry
    )
    assert isinstance(result, cli.Failure)
    assert result.code == cli.Code.CONFIG


@pytest.mark.parametrize(
    "arguments,env_updates",
    [
        ([], {}),
        (["--name", "Ada", "--count", "3.5"], {}),
        (["--name", "Ada", "--style", "other"], {}),
        (["--na", "Ada"], {}),
        (["--name", "Ada", "--unknown"], {}),
        (["--name", "Ada"], {"SAMPLE_STYLE": "other"}),
        (["--name", "Ada"], {"SAMPLE_COUNT": "3.5"}),
    ],
)
def test_invalid_inputs_reject_without_a_handler(
    tmp_path: Path, arguments: list[str], env_updates: dict[str, str]
) -> None:
    result = cli.resolve(
        contract(), arguments, environment(tmp_path) | env_updates, can_prompt=False
    )
    assert isinstance(result, cli.Failure)
    assert result.code == (cli.Code.CONFIG if env_updates else cli.Code.USAGE)


def test_literal_operands_and_repeated_options_preserve_order(tmp_path: Path) -> None:
    result = cli.resolve(
        contract(),
        ["-n", "$(touch forbidden)", "-t", "one", "--tag=two words", "--", "--json", "*"],
        environment(tmp_path),
        can_prompt=False,
    )
    assert isinstance(result, cli.Invocation)
    assert result.text("name") == "$(touch forbidden)"
    assert result.many("tag") == ("one", "two words")
    assert result.many("files") == ("--json", "*")
    assert not (tmp_path / "forbidden").exists()


@pytest.mark.parametrize("control", ["--json", "--no-input"])
def test_machine_controls_never_prompt(tmp_path: Path, control: str) -> None:
    def forbidden_entry(_label: str, _options: tuple[str, ...]) -> str:
        pytest.fail("Machine controls must suppress prompts")

    result = cli.resolve(
        contract(), [control], environment(tmp_path), can_prompt=True, entry=forbidden_entry
    )
    assert isinstance(result, cli.Failure)
    assert result.code == cli.Code.USAGE


def test_prompt_resolves_a_required_input(tmp_path: Path) -> None:
    calls: list[tuple[str, tuple[str, ...]]] = []

    def answer(label: str, options: tuple[str, ...]) -> str:
        calls.append((label, options))
        return "Ada"

    result = cli.resolve(contract(), [], environment(tmp_path), can_prompt=True, entry=answer)
    assert isinstance(result, cli.Invocation)
    assert calls == [("Choose a name", ())]
    assert result.text("name") == "Ada"
    assert result.sources["name"] == "prompt"


def test_picker_returns_stable_ids_and_rejects_custom_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invocation = cli.Invocation({}, {}, True)
    choices = {"first-id": "Same label", "second-id": "Same label"}

    def choose(
        _label: str, options: Sequence[str] = (), *, initial: str = "", strict: bool = False
    ) -> str:
        assert options == ("first-id: Same label", "second-id: Same label")
        assert initial == ""
        assert strict
        return options[1]

    monkeypatch.setattr(cli.ui, "entry", choose)
    assert cli.pick(invocation, "Select a record", choices) == "second-id"

    def custom(
        _label: str, _options: Sequence[str] = (), *, initial: str = "", strict: bool = False
    ) -> str:
        assert initial == ""
        assert strict
        return "custom"

    monkeypatch.setattr(cli.ui, "entry", custom)
    rejected = cli.pick(invocation, "Select a record", choices)
    assert isinstance(rejected, cli.Failure)
    assert rejected.code == cli.Code.USAGE


@pytest.mark.parametrize("error", [EOFError, KeyboardInterrupt])
def test_picker_and_confirmation_preserve_cancellation(
    monkeypatch: pytest.MonkeyPatch, error: type[BaseException]
) -> None:
    def cancelled(
        _label: str, _options: Sequence[str] = (), *, initial: str = "", strict: bool = False
    ) -> str:
        assert initial == ""
        raise error()

    monkeypatch.setattr(cli.ui, "entry", cancelled)
    invocation = cli.Invocation({}, {}, True)
    for result in (
        cli.pick(invocation, "Select", {"one": "First"}),
        cli.confirm(invocation, "Apply this change?"),
    ):
        assert isinstance(result, cli.Failure)
        assert result.code == cli.Code.INTERRUPTED
        assert result.status == 130


def test_noninteractive_selection_and_confirmation_never_read_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_entry(_label: str, _options: Sequence[str] = (), *, initial: str = "") -> str:
        assert initial == ""
        pytest.fail("A noninteractive operation must not read stdin")

    monkeypatch.setattr(cli.ui, "entry", forbidden_entry)
    invocation = cli.Invocation({}, {}, False)
    selected = cli.pick(invocation, "Select", {"one": "First"})
    assert isinstance(selected, cli.Failure)
    assert selected.code == cli.Code.USAGE
    approved = cli.confirm(invocation, "Apply this change?")
    assert isinstance(approved, cli.Failure)
    assert approved.code == cli.Code.DECLINED


@pytest.mark.parametrize("answer,accepted", [("y", True), ("Y", True), ("", False), ("yes", False)])
def test_confirmation_needs_an_explicit_y(
    monkeypatch: pytest.MonkeyPatch, answer: str, accepted: bool
) -> None:
    def answer_entry(_label: str, _options: Sequence[str] = (), *, initial: str = "") -> str:
        assert initial == ""
        return answer

    monkeypatch.setattr(cli.ui, "entry", answer_entry)
    result = cli.confirm(cli.Invocation({}, {}, True), "Apply this change?")
    if accepted:
        assert isinstance(result, cli.Approved)
    else:
        assert isinstance(result, cli.Failure)
        assert result.code == cli.Code.DECLINED


@pytest.fixture
def model_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for key in tuple(os.environ):
        monkeypatch.delenv(key)
    tools = tmp_path / "bin"
    tools.mkdir()
    monkeypatch.setenv("PATH", str(tools))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    return tmp_path


def fake_pi(home: Path, body: str) -> None:
    executable = home / "bin/pi"
    _ = executable.write_text(f"#!{sys.executable}\n" + body)
    executable.chmod(0o755)


def test_pi_receives_literal_stdin_and_isolated_mode_flags(
    model_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_pi(
        model_home,
        "import json, os, sys\nfrom pathlib import Path\n"
        "Path(os.environ['HOME'], 'request.json').write_text(json.dumps({'args': sys.argv[1:], 'input': sys.stdin.read()}))\n"
        "print('Useful answer')\n",
    )
    prompt = "literal $(touch forbidden)\n--model untrusted"
    result = cli.ask_pi(prompt, model="selected/model", timeout=2)
    assert isinstance(result, cli.Result)
    assert result.data == {"text": "Useful answer"}
    assert result.text == "Useful answer"
    request = json.loads((model_home / "request.json").read_text())
    assert request["input"] == prompt
    assert prompt not in request["args"]
    assert request["args"] == [
        "--print",
        "--no-session",
        "--no-tools",
        "--no-extensions",
        "--no-mcp",
        "--no-skills",
        "--no-context-files",
        "--no-prompt-templates",
        "--offline",
        "--model",
        "selected/model",
    ]
    assert not (model_home / "forbidden").exists()
    output = capsys.readouterr()
    assert output.out == ""
    assert "Useful answer" not in output.err


@pytest.mark.parametrize(
    "body", ["pass\n", "import sys\nprint('SECRET-TOKEN', file=sys.stderr)\nsys.exit(17)\n"]
)
def test_pi_failures_do_not_echo_provider_diagnostics(
    model_home: Path, body: str, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_pi(model_home, body)
    result = cli.ask_pi("Summarize the sample", timeout=2)
    assert isinstance(result, cli.Failure)
    assert result.code == cli.Code.UNAVAILABLE
    assert result.status == 69
    assert "SECRET-TOKEN" not in result.message
    output = capsys.readouterr()
    assert "SECRET-TOKEN" not in output.out + output.err


def test_pi_timeout_and_missing_executable_are_closed_failures(model_home: Path) -> None:
    missing = cli.ask_pi("Summarize the sample", timeout=1)
    assert isinstance(missing, cli.Failure)
    assert missing.code == cli.Code.UNAVAILABLE
    fake_pi(model_home, "import time\ntime.sleep(5)\n")
    timed_out = cli.ask_pi("Summarize the sample", timeout=0.05)
    assert isinstance(timed_out, cli.Failure)
    assert timed_out.code == cli.Code.UNAVAILABLE
    assert "timeout" in timed_out.message


@pytest.mark.parametrize("prompt,timeout", [("", 1), ("   ", 1), ("request", 0)])
def test_invalid_model_requests_do_not_start_pi(
    model_home: Path, prompt: str, timeout: int
) -> None:
    fake_pi(
        model_home,
        "import os\nfrom pathlib import Path\nPath(os.environ['HOME'], 'called').touch()\n",
    )
    result = cli.ask_pi(prompt, timeout=timeout)
    assert isinstance(result, cli.Failure)
    assert result.code == cli.Code.USAGE
    assert not (model_home / "called").exists()


def test_public_runner_rejects_before_domain_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "interface.json"
    _ = path.write_text(contract().model_dump_json(by_alias=True))
    monkeypatch.setenv("AYI_INTERFACE_PATH", str(path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(cli.sys, "argv", ["sample", "--json", "--name"])

    def forbidden_handler(_invocation: cli.Invocation) -> cli.Result:
        pytest.fail("Invalid input must not reach domain work")

    assert cli.run(forbidden_handler) == 64
    output = capsys.readouterr()
    assert output.out == ""
    assert json.loads(output.err)["error"]["code"] == "usage"


@pytest.mark.parametrize(
    "keys,expected",
    [
        (b"\x1b[B\x1b[B\r", "second-id"),
        (b"missing\r\x15\x1b[B\x1b[B\r", "second-id"),
        (b"\x03", "interrupted"),
    ],
)
def test_real_picker_restores_terminal_after_selection_or_cancel(
    tmp_path: Path, keys: bytes, expected: str
) -> None:
    master, slave = pty.openpty()
    _ = fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 20, 40, 0, 0))
    before = termios.tcgetattr(slave)
    program = (
        "import cli\n"
        "result = cli.pick(cli.Invocation({}, {}, True), 'Select record', "
        "{'first-id': 'Same label', 'second-id': 'Same label'})\n"
        "print('RESULT=' + (result if isinstance(result, str) else result.code.value), flush=True)\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", program],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin", "TERM": "xterm-256color"},
        cwd=Path(__file__).resolve().parents[1],
    )
    transcript = bytearray()
    sent = False
    deadline = time.monotonic() + 8
    try:
        while time.monotonic() < deadline:
            if select.select([master], [], [], 0.05)[0]:
                chunk = os.read(master, 65536)
                transcript.extend(chunk)
                if b"\x1b[6n" in chunk:
                    _ = os.write(master, b"\x1b[1;1R")
                if b"esc" in transcript and not sent:
                    _ = os.write(master, keys)
                    sent = True
            if process.poll() is not None:
                break
        assert process.wait(timeout=2) == 0, transcript.decode(errors="replace")
        while select.select([master], [], [], 0.05)[0]:
            transcript.extend(os.read(master, 65536))
        assert f"RESULT={expected}".encode() in transcript
        assert b"\x1b[?25h" in transcript
        assert b"?1049h" not in transcript
        after = termios.tcgetattr(slave)
        # macOS sets PENDIN when input arrives. It is not an application input mode.
        after[3] &= ~termios.PENDIN
        before[3] &= ~termios.PENDIN
        assert after == before
    finally:
        if process.poll() is None:
            process.kill()
            _ = process.wait(timeout=2)
        os.close(slave)
        os.close(master)


def test_review_unicode_summary_remains_valid_mise_toml() -> None:
    document = contract().model_dump(by_alias=True)
    document["summary"] = "Read a note 😀"
    interface = Interface.model_validate(document)
    source = cli.scaffold(interface, "def handle(invocation):\n    pass\n")
    metadata = next(
        line.removeprefix("#MISE ") for line in source.splitlines() if line.startswith("#MISE ")
    )
    assert tomllib.loads(metadata) == {"description": "Read a note 😀"}


def test_review_explicit_xdg_config_does_not_need_home(tmp_path: Path) -> None:
    write_settings(tmp_path, '[defaults]\nstyle = "long"\n')
    result = cli.resolve(
        contract(),
        ["--name", "Ada"],
        {"XDG_CONFIG_HOME": str(tmp_path / "config")},
        can_prompt=False,
    )
    assert isinstance(result, cli.Invocation)
    assert result.text("style") == "long"
    assert result.sources["style"] == "config"


def boolean_contract(*, default: bool) -> Interface:
    return Interface.model_validate(
        {
            "schemaVersion": 1,
            "runtime": "python",
            "name": "sample",
            "summary": "Show sample details",
            "options": [
                {
                    "names": ["--verbose", "-v"],
                    "description": "Show details",
                    "default": default,
                }
            ],
            "output": ["text", "json"],
        }
    )


@pytest.mark.parametrize("source", ["default", "config"])
def test_review_negative_boolean_alias_overrides_true(tmp_path: Path, source: str) -> None:
    interface = boolean_contract(default=source == "default")
    if source == "config":
        write_settings(tmp_path, "[defaults]\nverbose = true\n")
    baseline = cli.resolve(interface, [], environment(tmp_path), can_prompt=False)
    assert isinstance(baseline, cli.Invocation)
    assert baseline.flag("verbose") is True
    assert baseline.sources["verbose"] == source
    changed = cli.resolve(interface, ["--no-verbose"], environment(tmp_path), can_prompt=False)
    assert isinstance(changed, cli.Invocation)
    assert changed.flag("verbose") is False
    assert changed.sources["verbose"] == "argument"
    assert "--no-verbose" in cli.parser(interface).format_help()


def test_review_negative_boolean_alias_cannot_collide_with_declared_option() -> None:
    document = boolean_contract(default=True).model_dump(by_alias=True)
    document["options"] = [
        {"names": ["--verbose"], "description": "Show details"},
        {"names": ["--no-verbose"], "description": "Another operation"},
    ]
    with pytest.raises(ValidationError):
        _ = Interface.model_validate(document)


def test_review_negative_boolean_alias_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "interface"
    directory.mkdir()
    _ = (directory / "sample.json").write_text(
        boolean_contract(default=True).model_dump_json(by_alias=True)
    )
    monkeypatch.setattr(completion, "ROOT", tmp_path)
    candidates = completion.resolve(
        completion.Request(command="sample", args=["--no-v"], cursor=0, cwd=str(tmp_path))
    )
    assert [candidate.value for candidate in candidates] == ["--no-verbose"]


def test_review_positional_choices_appear_in_help(tmp_path: Path) -> None:
    document = boolean_contract(default=False).model_dump(by_alias=True)
    document["positionals"] = [
        {
            "name": "style",
            "required": True,
            "completion": {"kind": "choices", "values": ["compact", "detailed"]},
        }
    ]
    interface = Interface.model_validate(document)
    accepted = cli.resolve(interface, ["compact"], environment(tmp_path), can_prompt=False)
    assert isinstance(accepted, cli.Invocation)
    assert accepted.text("style") == "compact"
    rejected = cli.resolve(interface, ["other"], environment(tmp_path), can_prompt=False)
    assert isinstance(rejected, cli.Failure)
    assert rejected.code == cli.Code.USAGE
    help_text = cli.parser(interface).format_help()
    assert "compact" in help_text
    assert "detailed" in help_text


def test_optional_inputs_distinguish_missing_empty_text_and_zero(tmp_path: Path) -> None:
    document = boolean_contract(default=False).model_dump(by_alias=True)
    document["options"] = [
        {
            "names": ["--text"],
            "description": "Optional text",
            "value": {"name": "TEXT", "completion": {"kind": "none"}},
        },
        {
            "names": ["--count"],
            "description": "Optional count",
            "value": {"name": "COUNT", "type": "integer", "completion": {"kind": "none"}},
        },
    ]
    document["positionals"] = [{"name": "target", "completion": {"kind": "none"}}]
    interface = Interface.model_validate(document)
    absent = cli.resolve(interface, [], environment(tmp_path), can_prompt=False)
    assert isinstance(absent, cli.Invocation)
    assert absent.optional_text("text") is cli.Missing.VALUE
    assert absent.optional_integer("count") is cli.Missing.VALUE
    assert absent.optional_text("target") is cli.Missing.VALUE
    explicit = cli.resolve(
        interface, ["--text", "", "--count", "0", ""], environment(tmp_path), can_prompt=False
    )
    assert isinstance(explicit, cli.Invocation)
    assert explicit.optional_text("text") == ""
    assert explicit.optional_integer("count") == 0
    assert explicit.optional_text("target") == ""
    rejected = cli.resolve(interface, ["--count", ""], environment(tmp_path), can_prompt=False)
    assert isinstance(rejected, cli.Failure)
    assert rejected.code == cli.Code.USAGE


def test_closed_stdout_pipe_exits_successfully_without_traceback(tmp_path: Path) -> None:
    interface = tmp_path / "interface.json"
    _ = interface.write_text(boolean_contract(default=False).model_dump_json(by_alias=True))
    program = (
        "from cli import Invocation, Result, run\n"
        "def handle(invocation: Invocation) -> Result:\n"
        "    return Result({'complete': True}, 'x' * 4194304)\n"
        "raise SystemExit(run(handle))\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", program],
        cwd=Path(__file__).resolve().parents[1],
        env=environment(tmp_path) | {"AYI_INTERFACE_PATH": str(interface)},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        process.stdout.close()
        process.stdout = None
        _, stderr = process.communicate(timeout=5)
        assert process.returncode == 0, stderr
        assert stderr == ""
    finally:
        if process.poll() is None:
            process.kill()
            _ = process.communicate(timeout=2)
