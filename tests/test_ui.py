from __future__ import annotations

import errno
import fcntl
import json
import os
import pty
import re
import select
import signal
import struct
import subprocess
import sys
import termios
import textwrap
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import pyte
import pytest


class Terminal:
    """A real child TTY, with its actual output interpreted as terminal state."""

    def __init__(self, source: str, width: int, height: int, env: dict[str, str]) -> None:
        self.master, self.slave = pty.openpty()
        self.original_modes = termios.tcgetattr(self.slave)
        self.screen = pyte.HistoryScreen(width, height, history=2000)
        self.stream = pyte.ByteStream(self.screen)
        self.output = b""
        _ = fcntl.ioctl(self.slave, termios.TIOCSWINSZ, struct.pack("HHHH", height, width, 0, 0))
        self.process = subprocess.Popen(
            [sys.executable, "-c", source],
            stdin=self.slave,
            stdout=self.slave,
            stderr=self.slave,
            env={
                "PATH": os.environ["PATH"],
                "HOME": "/nonexistent",
                "XDG_CONFIG_HOME": "/nonexistent",
                "XDG_STATE_HOME": "/nonexistent",
                "TERM": "xterm-256color",
                **env,
            },
        )

    @property
    def display(self) -> str:
        return "\n".join(self.screen.display)

    @property
    def scrollback(self) -> str:
        return (
            "\n".join(
                "".join(line[x].data for x in sorted(line)) for line in self.screen.history.top
            )
            + "\n"
            + self.display
        )

    def read(self, timeout: float = 0.05) -> None:
        if select.select([self.master], [], [], timeout)[0]:
            chunk = os.read(self.master, 65536)
            self.output += chunk
            self.stream.feed(chunk)
            if b"\x1b[6n" in chunk:
                _ = os.write(
                    self.master,
                    f"\x1b[{self.screen.cursor.y + 1};{self.screen.cursor.x + 1}R".encode(),
                )

    def wait_for(self, predicate: Callable[[], bool]) -> None:
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            self.read()
            if predicate():
                return
            if self.process.poll() is not None:
                break
        pytest.fail(f"Terminal did not reach expected state:\n{self.display}\n{self.output!r}")

    def send(self, keys: bytes) -> None:
        _ = os.write(self.master, keys)

    def resize(self, width: int, height: int) -> None:
        # pyte clips the top on shrink but, unlike a terminal emulator, does
        # not retain those clipped rows in its scrollback automatically.
        for row in range(max(0, self.screen.lines - height)):
            self.screen.history.top.append(self.screen.buffer[row])
        self.screen.resize(lines=height, columns=width)
        # Model the real terminal's cursor being clamped when shrinking.
        self.screen.cursor.y = min(self.screen.cursor.y, height - 1)
        self.screen.cursor.x = min(self.screen.cursor.x, width - 1)
        _ = fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack("HHHH", height, width, 0, 0))
        self.process.send_signal(signal.SIGWINCH)

    def assert_input_last(self, text: str = "") -> None:
        # PTY reads can split a redraw before its cursor restoration arrives.
        self.wait_for(
            lambda: "ayi ❯ " in self.screen.display[self.screen.cursor.y]
            and text in self.screen.display[self.screen.cursor.y]
            and all(not line.strip() for line in self.screen.display[self.screen.cursor.y + 1 :])
            and not self.screen.cursor.hidden
        )
        row = self.screen.cursor.y
        assert "ayi ❯ " in self.screen.display[row], self.display
        assert all(not line.strip() for line in self.screen.display[row + 1 :]), self.display
        assert not self.screen.cursor.hidden

    def capture(self, name: str) -> None:
        directory = os.environ.get("AYI_UI_ARTIFACTS")
        if directory:
            path = Path(directory)
            path.mkdir(parents=True, exist_ok=True)
            _ = (path / f"{name}.ansi").write_bytes(self.output)
            _ = (path / f"{name}.txt").write_text(self.display + "\n")
            _ = (path / f"{name}.json").write_text(
                json.dumps(
                    {
                        "width": self.screen.columns,
                        "height": self.screen.lines,
                        "cursor": {"x": self.screen.cursor.x, "y": self.screen.cursor.y},
                        "rows": self.screen.display,
                    },
                    indent=2,
                    ensure_ascii=False,
                )
                + "\n"
            )

    def finish(self, expected: str, code: int = 0) -> None:
        self.wait_for(lambda: self.process.poll() is not None)
        # Keep the slave open: macOS can discard unread output on its last close.
        while select.select([self.master], [], [], 0.05)[0]:
            self.read(0)
        assert self.process.returncode == code, self.output.decode(errors="replace")
        assert expected.encode() in self.output, self.output.decode(errors="replace")
        restored = termios.tcgetattr(self.slave)
        # macOS sets this kernel bookkeeping bit on return to canonical mode;
        # it is not a leaked raw-mode setting and clears on the next input.
        restored[3] &= ~termios.PENDIN
        self.original_modes[3] &= ~termios.PENDIN
        assert restored == self.original_modes
        assert not self.screen.cursor.hidden
        assert not re.search(rb"\x1b\[\?(?:47|1047|1049)h|\x1b\[[23]J|\x1bc", self.output)
        assert "HISTORY_KEEP" in self.scrollback
        assert "Write your own answer" not in self.display

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.kill()
            _ = self.process.wait(timeout=2)
        os.close(self.slave)
        os.close(self.master)


@contextmanager
def terminal(
    expression: str,
    *,
    width: int = 80,
    height: int = 24,
    env: dict[str, str] | None = None,
    before: str = "",
) -> Iterator[Terminal]:
    source = (
        "import ui, sys, threading, time\n"
        "print('HISTORY_KEEP', flush=True)\n"
        + before
        + "try: print('ANSWER=' + "
        + expression
        + ", flush=True)\n"
        "except (EOFError, KeyboardInterrupt): print('ANSWER=CANCELLED', flush=True)\n"
    )
    child = Terminal(source, width, height, env or {})
    try:
        yield child
    finally:
        child.close()


@pytest.mark.parametrize(
    "width,keys,expected",
    [
        (80, b"\x1b[B\x1b[B\r", "Home"),
        (80, b"\x1b[B and children\r", "Current directory and children"),
        (80, b"unmatched\x1b[B\r", "unmatched"),
        (28, b"\x1b[Aown response\r", "own response"),
        (28, b"\x1b[A1\r", "1"),
        (28, b"custom\r", "custom"),
        (40, b"\x03", "CANCELLED"),
        (40, b"\x1b", "CANCELLED"),
        (40, b"\x04", "CANCELLED"),
        (80, b"HOME\x1b[B\r", "Home"),
        (80, b"ho\t\x1b[Z\r", "ho"),
        (80, b"abc\x1b[D\x7fZ\x1b[3~\r", "aZ"),
        (80, b"end\x1b[Hstart \x1b[F!\r", "start end!"),
    ],
)
def test_inline_terminal_selection_resize_and_cancel(
    width: int, keys: bytes, expected: str
) -> None:
    with terminal(
        "ui.entry('Where should the search begin?', ['Current directory', 'Home', 'Choose a path'])",
        width=width,
        height=20,
        before="threading.Timer(0.3, lambda: print('operation update', flush=True)).start()\n",
    ) as child:
        child.wait_for(lambda: "operation update" in child.display)
        child.resize(width, 16)
        child.assert_input_last()
        child.send(keys)
        child.finish(f"ANSWER={expected}")


@pytest.mark.parametrize("width", [28, 40, 80, 120])
@pytest.mark.parametrize("height", [6, 12, 24])
def test_input_survives_long_context_and_filtered_navigation(width: int, height: int) -> None:
    label = (
        "new / search\n"
        + "Which directories should the command search? " * 8
        + "Include hidden directories in the final search."
    )
    options = [f"Project {i}: café 界 and children" for i in range(12)]
    with terminal(f"ui.entry({label!r}, {options!r})", width=width, height=height) as child:
        child.assert_input_last()
        child.capture(f"initial-{width}x{height}")
        child.send(b"PROJECT 11")
        child.wait_for(lambda: "Project 0:" not in child.display)
        child.assert_input_last("PROJECT 11")
        child.capture(f"filtered-{width}x{height}")
        child.send(b"\x1b[B")
        child.wait_for(lambda: "❯ Project 11:" in child.display)
        child.assert_input_last()
        child.capture(f"selected-{width}x{height}")
        child.send(b"\r")
        child.finish(f"ANSWER={options[-1]}")
        assert "".join(label.split()) in "".join(child.scrollback.split())


def test_filter_navigation_keeps_query_and_resize_keeps_edit_cursor() -> None:
    with terminal("ui.entry('Search roots', ['Alpha one', 'Beta', 'Alpha two'])") as child:
        child.assert_input_last()
        child.send(b"ALP\x1b[D")
        child.wait_for(lambda: "Beta" not in child.display)
        child.send(b"\x1b[B\x1b[B")
        child.assert_input_last("Alpha two")
        child.send(b"\x1b[B")  # Custom response restores the original filter, not an empty buffer.
        child.assert_input_last("ALP")
        child.read(0.1)
        child.resize(28, 6)
        child.assert_input_last("ALP")
        child.send(b"X\r")
        child.finish("ANSWER=ALXP")


def test_navigation_scrolls_long_list_and_reflows_selected_candidate() -> None:
    options = [f"Root {i}: search the project and its children" for i in range(15)]
    with terminal(f"ui.entry('Choose search roots', {options!r})", width=28, height=6) as child:
        child.assert_input_last()
        child.send(b"\x1b[B" * 15)
        child.wait_for(lambda: "❯ Root 14:" in child.display)
        child.assert_input_last()
        child.capture("scrolled-28x6")
        child.resize(120, 24)
        child.wait_for(lambda: "❯ Root 14: search the project and its children" in child.display)
        child.assert_input_last(options[-1])
        child.resize(40, 12)
        child.wait_for(lambda: "↑↓ tab" in child.display)
        child.assert_input_last()
        child.send(b"\r")
        child.finish(f"ANSWER={options[-1]}")


def test_ascii_encoding_uses_native_inline_editing_with_ascii_chrome() -> None:
    with terminal(
        "ui.entry('Choose', ['First', 'Second'])", env={"PYTHONIOENCODING": "ascii"}
    ) as child:
        child.wait_for(lambda: "ayi > " in child.display)
        child.send(b"sec\t\r")
        child.finish("ANSWER=Second")
        assert b"\xe2\x94" not in child.output


def test_no_color_keeps_inline_filtering_without_color() -> None:
    with terminal("ui.entry('Choose', ['First', 'Second'])", env={"NO_COLOR": "1"}) as child:
        child.assert_input_last()
        child.send(b"sec")
        child.wait_for(lambda: "First" not in child.display)
        child.assert_input_last("sec")
        child.capture("monochrome-filter")
        child.send(b"\t\r")
        child.finish("ANSWER=Second")
        # Monochrome emphasis (bold/reverse) is fine; chromatic SGR is not.
        for sgr in re.findall(rb"\x1b\[([\d;]*)m", child.output):
            assert all(
                int(code or b"0") in {0, 1, 3, 4, 7, 22, 23, 24, 27} for code in sgr.split(b";")
            )


@pytest.mark.parametrize("options", [[], ["First", "Second"]])
def test_empty_and_unmatched_candidates_allow_custom_answers(options: list[str]) -> None:
    with terminal(f"ui.entry('Choose', {options!r})", width=40, height=6) as child:
        child.assert_input_last()
        child.send(b"custom")
        child.assert_input_last("custom")
        child.capture("unmatched" if options else "no-candidates")
        child.send(b"\x1b[B\r")
        child.finish("ANSWER=custom")


def test_strict_picker_rejects_custom_and_accepts_declared_value() -> None:
    with terminal("ui.entry('Choose', ['First', 'Second'], strict=True)") as child:
        child.assert_input_last()
        child.send(b"other\r")
        child.wait_for(lambda: "Choose a listed value" in child.display)
        child.assert_input_last("other")
        child.send(b"\x15sec\t\r")
        child.finish("ANSWER=Second")


@pytest.mark.parametrize("width,height", [(28, 6), (40, 12), (80, 24), (120, 24)])
def test_concurrent_output_status_and_activity_keep_input(width: int, height: int) -> None:
    before = (
        "def updates():\n"
        " time.sleep(0.8)\n"
        " with ui.activity('Verification'):\n"
        "  print('worker stdout', flush=True)\n"
        "  print('worker stderr', file=sys.stderr, flush=True)\n"
        "  import subprocess\n"
        "  result = subprocess.run([sys.executable, '-c', 'print(\"subprocess output\")'], capture_output=True, text=True, check=True)\n"
        "  print(result.stdout, end='', flush=True)\n"
        "  ui.status('Unavailable: remote action; use a local path')\n"
        " time.sleep(0.4)\n"
        " print('OUTPUT_DONE', flush=True)\n"
        "threading.Thread(target=updates).start()\n"
    )
    with terminal(
        "ui.entry('New command request')", width=width, height=height, before=before
    ) as child:
        child.assert_input_last()
        child.send(b"draft\x1b[D\x1b[D")
        child.wait_for(lambda: b"OUTPUT_DONE" in child.output)
        child.assert_input_last("draft")
        child.capture(f"concurrent-{width}x{height}")
        child.send(b"X\r")
        child.finish("ANSWER=draXft")
        for message in (
            b"worker stdout",
            b"worker stderr",
            b"subprocess output",
            b"Verification",
            b"Unavailable",
        ):
            assert message in child.output


@pytest.mark.parametrize("fail", [False, True])
def test_activity_surrounding_entry_finishes_without_a_live_renderer(fail: bool) -> None:
    before = (
        "def operation():\n"
        " try:\n"
        "  with ui.activity('Generate local command'):\n"
        "   answer = ui.entry('Command name', initial='search')\n"
        + ("   raise ValueError('verification rejected')\n" if fail else "   return answer\n")
        + " except ValueError:\n"
        "  ui.status('Recovery: inspect the draft before retrying')\n"
        "  return 'REJECTED'\n"
    )
    with terminal("operation()", before=before) as child:
        child.assert_input_last("search")
        child.capture("pending-generation")
        child.send(b"\r")
        child.finish("ANSWER=REJECTED" if fail else "ANSWER=search")
        assert (b"Failed:" if fail else b"Completed:") in child.output
        child.capture("verification-failure" if fail else "verification-success")


@pytest.mark.parametrize("termination", [signal.SIGINT, signal.SIGTERM])
def test_control_signal_restores_terminal(termination: signal.Signals) -> None:
    with terminal("ui.entry('Interrupt this question')", width=40, height=6) as child:
        child.assert_input_last()
        child.send(b"uncommitted")
        child.assert_input_last("uncommitted")
        child.process.send_signal(termination)
        if termination == signal.SIGINT:
            child.finish("ANSWER=CANCELLED")
        else:
            child.finish("", 128 + termination)
        child.capture(f"cleanup-{termination.name}")


@pytest.mark.parametrize(
    "mode,redirect",
    [({"TERM": "dumb"}, ""), ({"CI": "true"}, ""), ({}, "stdout"), ({}, "stderr"), ({}, "stdin")],
)
def test_plain_input_has_no_terminal_controls(
    mode: dict[str, str], redirect: str, tmp_path: Path
) -> None:
    master, slave = pty.openpty()
    # Capability overrides use real TTY streams; each redirect is isolated.
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import ui; print('CAN_PROMPT=' + str(ui.can_prompt())); print('ANSWER=' + ui.entry('Choose', ['First', 'Second']))",
        ],
        env={"HOME": str(tmp_path), "TERM": "xterm-256color", **mode},
        stdin=subprocess.PIPE if redirect == "stdin" else slave,
        stderr=subprocess.PIPE if redirect == "stderr" else slave,
        stdout=subprocess.PIPE if redirect == "stdout" else slave,
    )
    try:
        if process.stdin is not None:
            _ = process.stdin.write(b"2\n")
            process.stdin.close()
        else:
            _ = os.write(master, b"2\n")
        assert process.wait(timeout=4) == 0
        output = b""
        # Keep the slave open until drained: macOS discards unread data on its last close.
        while select.select([master], [], [], 0.05)[0]:
            output += os.read(master, 65536)
        if process.stdout is not None:
            output += process.stdout.read()
        if process.stderr is not None:
            output += process.stderr.read()
        assert b"ANSWER=Second" in output
        assert f"CAN_PROMPT={not redirect and 'CI' not in mode}".encode() in output
        assert b"Choose" in output
        assert b"\x1b" not in output
    finally:
        if process.poll() is None:
            process.kill()
            _ = process.wait(timeout=2)
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()
        if process.stdin is not None:
            process.stdin.close()
        os.close(slave)
        os.close(master)


@pytest.mark.parametrize(
    "width,keys,expected",
    [
        (80, b"answer with spaces\r", "answer with spaces"),
        (28, b"narrow answer\r", "narrow answer"),
        (80, b"\x04", "EOF"),
        (80, b"\x03", "INTERRUPTED"),
    ],
)
def test_generation_input_recipe_restores_terminal(
    width: int, keys: bytes, expected: str, tmp_path: Path
) -> None:
    guide = Path(__file__).resolve().parents[1] / "prompts/cli-components.md"
    examples = re.findall(r"```python\n(.*?)\n```", guide.read_text(), re.DOTALL)
    recipe = next(example for example in examples if "create_input" in example)
    source = (
        "try:\n"
        + textwrap.indent(recipe, "    ")
        + "\nexcept EOFError:\n    print('OUTCOME=EOF')\n"
        "except KeyboardInterrupt:\n    print('OUTCOME=INTERRUPTED')\n"
        "else:\n    print('OUTCOME=' + question)\n"
    )
    master, slave = pty.openpty()
    _ = fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 20, width, 0, 0))
    original = termios.tcgetattr(master)

    def controlling_terminal() -> None:
        os.setsid()
        _ = fcntl.ioctl(1, termios.TIOCSCTTY, 0)

    process = subprocess.Popen(
        [sys.executable, "-c", source],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env={"HOME": str(tmp_path), "TERM": "xterm-256color", "PROMPT_TOOLKIT_NO_CPR": "1"},
        preexec_fn=controlling_terminal,
    )
    output = b""
    sent = False
    deadline = time.monotonic() + 8
    try:
        while time.monotonic() < deadline:
            if select.select([master], [], [], 0.1)[0]:
                try:
                    chunk = os.read(master, 65536)
                except OSError as error:
                    if error.errno != errno.EIO or process.poll() is None:
                        raise
                    break
                if not chunk:
                    break
                output += chunk
                if b"You >" in output and not sent:
                    _ = os.write(master, keys)
                    sent = True
            elif process.poll() is not None:
                break
        assert process.wait(timeout=2) == 0, output.decode(errors="replace")
        assert f"OUTCOME={expected}".encode() in output
        assert termios.tcgetattr(master) == original
        assert b"?1049h" not in output
    finally:
        if process.poll() is None:
            process.terminate()
            _ = process.wait(timeout=2)
        os.close(slave)
        os.close(master)
