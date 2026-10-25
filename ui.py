"""One inline terminal surface; generated Python tools may reuse the output helpers.

Results belong on stdout. These helpers write presentation and diagnostics to stderr.
No history is persisted and no alternate screen is used.
"""

from __future__ import annotations

import difflib
import os
import signal
import sys
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from types import FrameType

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, VSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.output import ColorDepth
from prompt_toolkit.output.defaults import create_output
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import TextArea
from rich.console import Console
from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

STYLE = Style.from_dict(
    {
        "accent": "ansicyan bold",
        "muted": "ansibrightblack",
        "selected": "bg:ansicyan ansiblack bold",
        "title": "bold",
    }
)
MONOCHROME_STYLE = Style.from_dict({"accent": "bold", "selected": "reverse bold", "title": "bold"})
_inline_active = False


def can_prompt() -> bool:
    """Input capability is independent of color and cursor rendering."""
    try:
        return (
            sys.stdin.isatty()
            and sys.stdout.isatty()
            and sys.stderr.isatty()
            and not os.environ.get("CI")
        )
    except (AttributeError, OSError, ValueError):
        return False


def interactive() -> bool:
    return can_prompt() and os.environ.get("TERM") != "dumb"


def _unicode() -> bool:
    try:
        _ = "┌─│└❯◆↑↓↵".encode(sys.stderr.encoding or "ascii")
    except UnicodeError:
        return False
    return True


def _color() -> bool:
    return interactive() and "NO_COLOR" not in os.environ


def console() -> Console:
    # During input, patch_stdout schedules complete lines above the application.
    # Rich remains a formatter, never a second live renderer (or an ANSI writer).
    return Console(
        stderr=True,
        force_terminal=interactive() and not _inline_active,
        no_color=not _color() or _inline_active,
    )


def status(message: str) -> None:
    console().print(
        f"  {'◆' if _unicode() else '*'}  {message}" if interactive() else message,
        style="cyan",
        markup=False,
        highlight=False,
    )


@contextmanager
def activity(message: str) -> Iterator[None]:
    """Stable, textual lifecycle events share the active input's output queue.

    Deliberately no independent Rich Live/spinner: activity may begin before an
    entry or on a worker thread and must never take ownership of its input row.
    """
    status(f"Pending: {message}")
    try:
        yield
    except BaseException:
        status(f"Failed: {message}")
        raise
    else:
        status(f"Completed: {message}")


@contextmanager
def _termination(app: Application[str]) -> Iterator[None]:
    """Let the application's normal finally blocks restore the TTY on SIGTERM."""
    previous = signal.getsignal(signal.SIGTERM)
    install = threading.current_thread() is threading.main_thread() and previous == signal.SIG_DFL

    def terminate(number: int, _frame: FrameType | None) -> None:
        error = SystemExit(128 + number)
        loop = app.loop
        if loop is None:
            raise error

        def exit_application() -> None:
            if not app.is_done:
                app.exit(exception=error)

        _ = loop.call_soon_threadsafe(exit_application)

    if install:
        _ = signal.signal(signal.SIGTERM, terminate)
    try:
        yield
    finally:
        if install:
            _ = signal.signal(signal.SIGTERM, previous)


def _clip(text: str, width: int) -> str:
    line = Text(text)
    line.truncate(max(1, width), overflow="ellipsis")
    return line.plain


def _wrap(text: str, width: int, limit: int) -> list[str]:
    if limit <= 0:
        return []
    lines = Text(text).wrap(console(), max(1, width))
    result = [line.plain for line in lines[:limit]]
    if len(lines) > limit:
        result[-1] = _clip(result[-1].rstrip() + " …", width)
    return result


def entry(
    label: str, options: Sequence[str] = (), *, initial: str = "", strict: bool = False
) -> str:
    """Candidates and context always precede the final input row.

    Typing filters case-insensitively; navigation previews matches without
    replacing the query. Editing a preview starts a new filter. The custom row
    restores the query. Strict pickers accept only exact declared values.
    The transient region collapses into a stable scrollback record on completion.
    """
    if strict and not options:
        raise ValueError("A strict picker needs at least one choice")
    if not interactive():
        print(label, file=sys.stderr, flush=True)
        for index, option in enumerate(options, 1):
            print(f"  {index}  {option}", file=sys.stderr)
        if initial:
            print(
                f"Current answer: {initial}\nEnter keeps it. Type a replacement or + text to append.",
                file=sys.stderr,
            )
        while True:
            print("ayi > ", end="", file=sys.stderr, flush=True)
            answer = input().strip()
            if initial:
                answer = initial + answer[1:] if answer.startswith("+") else answer or initial
            if options and answer.isdecimal() and 1 <= int(answer) <= len(options):
                answer = options[int(answer) - 1]
            if not strict or answer in options:
                return answer
            print("Choose a listed value.", file=sys.stderr, flush=True)

    global _inline_active
    if _inline_active:
        raise RuntimeError("An inline interaction is already active")
    unicode = _unicode()
    selected: int | None = None
    query = initial
    query_cursor = len(initial)
    navigating = False
    validation = ""
    field = TextArea(
        height=1,
        text=initial,
        multiline=False,
        wrap_lines=False,
    )
    field.buffer.cursor_position = len(initial)

    def candidates() -> list[str | None]:
        matches: list[str | None] = [
            option for option in options if query.casefold() in option.casefold()
        ]
        if options and not strict:
            matches.append(None)
        return matches

    def edited(buffer: Buffer) -> None:
        nonlocal query, selected, validation
        if not navigating:
            query = buffer.text
            selected = None
            validation = ""

    field.buffer.on_text_changed += edited

    def context_rows() -> list[tuple[str, str]]:
        size = app.output.get_size()
        budget = max(0, size.rows - 1)
        if not budget:
            return []
        width = max(1, size.columns - 5)
        choices = candidates()
        # Budget physical rows, not logical strings: long context cannot crowd
        # out input. The selected candidate gets wrapping priority over others.
        candidate_rows = min(2, len(choices))
        if selected is not None and choices[selected] is not None:
            candidate_rows = max(candidate_rows, len(_wrap(choices[selected] or "", width - 2, 3)))
        heading_budget = min(4, max(0, budget - 1 - candidate_rows))
        heading = _wrap(f"ayi / {label}", width, heading_budget)
        rows: list[tuple[str, str]] = [
            ("class:title", ("  ┌─ " if unicode else "  +- ") + text)
            if index == 0
            else ("", ("  │  " if unicode else "  |  ") + text)
            for index, text in enumerate(heading)
        ]
        count = min(6, budget - len(rows) - 1)
        focus = selected if selected is not None else 0
        focused_lines = 1
        if selected is not None and choices[selected] is not None:
            focused_lines = len(_wrap(choices[selected] or "", width - 2, min(3, count)))
        start = max(0, focus - max(0, count - focused_lines))
        remaining = count
        for index in range(start, len(choices)):
            if remaining <= 0:
                break
            focused = index == selected
            candidate = choices[index]
            text = candidate if candidate is not None else "Write your own answer"
            if candidate is None and query:
                text = ("Custom: " if len(choices) > 1 else "No matches; custom: ") + query
            # Candidate values can contain newlines, but each preview row has a
            # known cell width. The returned value itself is never normalized.
            text = " ".join(text.splitlines())
            lines = (
                _wrap(text, width - 2, min(3, remaining)) if focused else [_clip(text, width - 2)]
            )
            for line_index, line in enumerate(lines):
                marker = ("❯" if unicode else ">") if focused and line_index == 0 else " "
                prefix = "  │  " if unicode else "  |  "
                rows.append(("class:selected" if focused else "", f"{prefix}{marker} {line}"))
                remaining -= 1
        if options and not choices and count > 0:
            rows.append(("class:muted", "  No matches"))
        hint = "enter submit · esc cancel"
        if options:
            hint = "↑↓ tab · enter · esc" if unicode else "up/down tab enter esc"
            if size.columns >= 60:
                hint = f"{len(choices) - int(not strict)} matches · {hint} · type to filter"
        rows.append(
            ("class:muted", ("  └─ " if unicode else "  +- ") + _clip(validation or hint, width))
        )
        return rows

    def context() -> list[tuple[str, str]]:
        rows = context_rows()
        return [
            (style, text + ("\n" if index < len(rows) - 1 else ""))
            for index, (style, text) in enumerate(rows)
        ]

    bindings = KeyBindings()

    def choose(direction: int) -> None:
        nonlocal selected, navigating, validation, query_cursor
        choices = candidates()
        if choices:
            if selected is None or choices[selected] is None:
                query_cursor = field.buffer.cursor_position
            selected = (
                (0 if direction > 0 else len(choices) - 1)
                if selected is None
                else (selected + direction) % len(choices)
            )
            value = choices[selected]
            navigating = True
            try:
                text = query if value is None else value
                field.buffer.document = Document(
                    text, cursor_position=query_cursor if value is None else len(text)
                )
            finally:
                navigating = False
            validation = ""

    def _next_choice(_event: object) -> None:
        choose(1)

    def _previous_choice(_event: object) -> None:
        choose(-1)

    def _accept(_event: object) -> None:
        nonlocal validation
        answer = field.text.strip()
        if strict and answer not in options:
            validation = "Choose a listed value; use tab or arrows."
        else:
            app.exit(result=answer)

    def _cancel(_event: object) -> None:
        app.exit(exception=EOFError())

    for key in ("down", "tab"):
        _ = bindings.add(key)(_next_choice)
    for key in ("up", "s-tab"):
        _ = bindings.add(key)(_previous_choice)
    _ = bindings.add("enter")(_accept)
    for key in ("escape", "c-c", "c-d", "<sigint>"):
        _ = bindings.add(key, eager=True)(_cancel)

    last_width: int | None = None

    def reflow(application: Application[str]) -> None:
        nonlocal last_width
        width = application.output.get_size().columns
        if width != last_width:
            # Window retains horizontal scroll even when a resize makes the
            # whole answer fit. Recompute its viewport, never the edit buffer.
            field.window.horizontal_scroll = 0
            last_width = width

    app: Application[str] = Application(
        layout=Layout(
            HSplit(
                [
                    Window(
                        FormattedTextControl(context),
                        height=lambda: len(context_rows()),
                        dont_extend_height=True,
                        wrap_lines=False,
                    ),
                    VSplit(
                        [
                            Window(
                                FormattedTextControl(
                                    [("class:accent", "  ayi ❯ " if unicode else "  ayi > ")]
                                ),
                                width=8,
                                height=1,
                            ),
                            field,
                        ],
                        height=1,
                    ),
                ]
            ),
            focused_element=field,
        ),
        key_bindings=bindings,
        style=STYLE if _color() else MONOCHROME_STYLE,
        color_depth=None if _color() else ColorDepth.DEPTH_1_BIT,
        output=create_output(stdout=sys.stderr),
        full_screen=False,
        erase_when_done=True,
        before_render=reflow,
    )
    _inline_active = True
    try:
        # Keep the shared AppSession: patch_stdout's flush thread uses it to
        # schedule output through the running application's redraw lifecycle.
        with patch_stdout(), _termination(app):
            answer = app.run()
    except (EOFError, KeyboardInterrupt, SystemExit):
        status(f"Cancelled: {label}")
        raise
    except Exception:
        status(f"Failed: {label}")
        raise
    finally:
        _inline_active = False
    # The live summary may be elided, never the stable question/answer record.
    status(f"Answered: {label}: {answer}")
    return answer


def approved(question: str) -> bool:
    try:
        return entry(f"{question} [y/N]").lower() == "y"
    except EOFError:
        return False


def review(label: str, content: bytes, previous: bytes | None = None) -> None:
    output = console()
    if interactive():
        output.rule(
            Text(f"◆ {label}", style="bold cyan"),
            align="left",
            style="bright_black",
            characters="─",
        )
    else:
        output.print(f"\n{label}:", style="bold", markup=False)
    source = content.decode()
    if previous is not None:
        source = (
            "".join(
                difflib.unified_diff(
                    previous.decode().splitlines(True),
                    source.splitlines(True),
                    fromfile="installed",
                    tofile="proposed",
                )
            )
            or "(unchanged)\n"
        )
    if interactive():
        language = (
            "diff"
            if previous is not None
            else (
                "bash" if source.startswith("#!") and "bash" in source.splitlines()[0] else "text"
            )
        )
        output.print(Syntax(source, language, word_wrap=True, background_color="default"))
    else:
        output.print(source, markup=False, highlight=False, soft_wrap=True)


def explain(text: str) -> None:
    output = Console(
        force_terminal=interactive() and not _inline_active, no_color=not _color() or _inline_active
    )
    if interactive():
        output.print(Markdown(text))
    else:
        output.print(text, markup=False, highlight=False, soft_wrap=True)


def command_list(rows: Sequence[tuple[str, str, str]]) -> None:
    output = Console(
        force_terminal=interactive() and not _inline_active, no_color=not _color() or _inline_active
    )
    if not rows:
        output.print(
            'No Ayi commands registered. Create one with: ayi new <name> "<request>"', markup=False
        )
    elif interactive():
        table = Table(box=None, padding=(0, 2))
        table.add_column("Command", style="cyan", overflow="fold")
        table.add_column("Description", overflow="fold")
        table.add_column("Example", overflow="fold")
        for name, summary, invocation in rows:
            table.add_row(Text(name), Text(summary), Text(invocation))
        output.print(table)
    else:
        for row in rows:
            print("\t".join(row))
