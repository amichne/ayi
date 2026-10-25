from __future__ import annotations

import fcntl
import os
import pty
import select
import signal
import struct
import subprocess
import sys
import termios
import time
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "width,keys,expected",
    [
        (80, b"\x1b[B\x1b[B\r", "Home"),
        (80, b"\x1b[B and children\r", "Current directory and children"),
        (80, b"unmatched\x1b[B\r", "Current directory"),
        (28, b"\x1b[Aown response\r", "own response"),
        (28, b"\x1b[A1\r", "1"),
        (28, b"custom\r", "custom"),
        (40, b"\x03", "CANCELLED"),
    ],
)
def test_inline_terminal_selection_resize_and_cancel(
    width: int, keys: bytes, expected: str
) -> None:
    master, slave = pty.openpty()
    _ = fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 20, width, 0, 0))
    env = {"PATH": os.environ["PATH"], "TERM": "xterm-256color", "HOME": "/nonexistent"}
    source = (
        "import ui, threading\n"
        "threading.Timer(0.1, lambda: print('model update', flush=True)).start()\n"
        "try: print('ANSWER=' + ui.entry('Where should the search begin?', ['Current directory', 'Home', 'Choose a path']))\n"
        "except EOFError: print('ANSWER=CANCELLED')\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", source], stdin=slave, stdout=slave, stderr=slave, env=env
    )
    os.close(slave)
    output = b""
    sent = False
    deadline = time.monotonic() + 8
    try:
        while time.monotonic() < deadline:
            if select.select([master], [], [], 0.1)[0]:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                output += chunk
                if b"\x1b[6n" in chunk:
                    _ = os.write(master, b"\x1b[1;1R")
                if b"esc" in output and b"model update" in output and not sent:
                    _ = fcntl.ioctl(
                        master, termios.TIOCSWINSZ, struct.pack("HHHH", 16, width, 0, 0)
                    )
                    process.send_signal(signal.SIGWINCH)
                    _ = os.write(master, keys)
                    sent = True
            if process.poll() is not None:
                break
        assert process.wait(timeout=2) == 0, output.decode(errors="replace")
        assert f"ANSWER={expected}".encode() in output
        assert b"?1049h" not in output and b"?1047h" not in output
        assert b"\x1b[?25h" in output  # Cursor restored on success and cancellation.
    finally:
        if process.poll() is None:
            process.terminate()
            _ = process.wait(timeout=2)
        os.close(master)


@pytest.mark.parametrize(
    "mode", [{"NO_COLOR": "1"}, {"TERM": "dumb"}, {"CI": "true"}, {"PYTHONIOENCODING": "ascii"}, {}]
)
def test_plain_input_has_no_terminal_controls(mode: dict[str, str], tmp_path: Path) -> None:
    master, slave = pty.openpty()
    # Each capability is tested with TTY input/error, not already-degraded pipes.
    # The empty mode specifically tests redirected stdout with no capability overrides.
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import ui; print('CAN_PROMPT=' + str(ui.can_prompt())); print('ANSWER=' + ui.entry('Choose', ['First', 'Second']))",
        ],
        env={"HOME": str(tmp_path), "TERM": "xterm-256color", **mode},
        stdin=slave,
        stderr=slave,
        stdout=slave if mode else subprocess.PIPE,
    )
    try:
        _ = os.write(master, b"2\n")
        assert process.wait(timeout=4) == 0
        output = b""
        # Keep the slave open until drained: macOS discards unread data on its last close.
        while select.select([master], [], [], 0.05)[0]:
            output += os.read(master, 65536)
        if process.stdout is not None:
            output += process.stdout.read()
        assert b"ANSWER=Second" in output
        assert f"CAN_PROMPT={bool(mode) and 'CI' not in mode}".encode() in output
        assert b"Choose" in output
        assert b"\x1b" not in output
    finally:
        if process.poll() is None:
            process.kill()
            _ = process.wait(timeout=2)
        if process.stdout is not None:
            process.stdout.close()
        os.close(slave)
        os.close(master)
