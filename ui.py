"""One inline terminal surface; generated Python tools may reuse the output helpers.

Results belong on stdout. These helpers write presentation and diagnostics to stderr.
No history is persisted and no alternate screen is used.
"""

from __future__ import annotations

import difflib
import os
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager

from prompt_toolkit.application import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
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


def interactive() -> bool:
    try:
        _ = "┌─│└❯◆↑↓↵".encode(sys.stderr.encoding or "ascii")
    except UnicodeError:
        return False
    return (
        sys.stdin.isatty()
        and sys.stdout.isatty()
        and sys.stderr.isatty()
        and os.environ.get("TERM") != "dumb"
        and "NO_COLOR" not in os.environ
        and not os.environ.get("CI")
    )


def console() -> Console:
    return Console(stderr=True, force_terminal=interactive(), no_color=not interactive())


def status(message: str) -> None:
    console().print(
        f"  ◆  {message}" if interactive() else message, style="cyan", markup=False, highlight=False
    )


@contextmanager
def activity(message: str) -> Iterator[None]:
    output = console()
    if interactive():
        with output.status(f"  {message}", spinner="dots"):
            yield
    else:
        status(message)
        yield


def entry(label: str, options: Sequence[str] = ()) -> str:
    """Candidates and context always precede the final input row.

    Up/down and Tab select candidates; Enter accepts the selection or free text.
    The transient region collapses into a stable scrollback record on completion.
    """
    if not interactive():
        print(label, file=sys.stderr, flush=True)
        for index, option in enumerate(options, 1):
            print(f"  {index}  {option}", file=sys.stderr)
        print("ayi > ", end="", file=sys.stderr, flush=True)
        answer = input().strip()
        if options and answer.isdecimal() and 1 <= int(answer) <= len(options):
            answer = options[int(answer) - 1]
        return answer

    selected: int | None = None
    field = TextArea(
        height=1, prompt=[("class:accent", "  ayi ❯ ")], multiline=False, wrap_lines=False
    )

    def matches() -> list[str]:
        return [option for option in options if field.text.casefold() in option.casefold()]

    def context() -> list[tuple[str, str]]:
        candidates = matches()
        heading, _, question = label.partition("\n")
        rows: list[tuple[str, str]] = [
            ("class:accent", "  ┌─ "),
            ("class:title", f"ayi / {heading}\n"),
        ]
        if question:
            for line in Text(question).wrap(console(), max(1, app.output.get_size().columns - 5)):
                rows.extend([("class:muted", "  │  "), ("", f"{line.plain}\n")])
        # Reserve the final row for input, even in small terminals.
        count = max(0, min(6, app.output.get_size().rows - 4))
        start = max(0, (selected or 0) - count + 1)
        for index in range(start, min(len(candidates), start + count)):
            focused = index == selected
            rows.extend(
                [
                    ("class:accent" if focused else "class:muted", "  │  "),
                    (
                        "class:selected" if focused else "",
                        f" {'❯' if focused else ' '} {candidates[index]} ",
                    ),
                    ("", "\n"),
                ]
            )
        help_text = "↵ submit · esc cancel"
        if options:
            help_text = (
                "↑↓ tab · ↵ · esc"
                if app.output.get_size().columns < 45
                else "↑↓ navigate · tab next · ↵ accept · esc cancel"
            )
        rows.extend([("class:muted", "  └─ "), ("class:muted", help_text)])
        return rows

    bindings = KeyBindings()

    def _next_choice(_event: object) -> None:
        nonlocal selected
        candidates = matches()
        if candidates:
            selected = 0 if selected is None else (selected + 1) % len(candidates)

    def _previous_choice(_event: object) -> None:
        nonlocal selected
        candidates = matches()
        if candidates:
            selected = len(candidates) - 1 if selected is None else (selected - 1) % len(candidates)

    def _accept(_event: object) -> None:
        candidates = matches()
        answer = (
            candidates[selected]
            if selected is not None and selected < len(candidates)
            else field.text
        )
        if options and answer.isdecimal() and 1 <= int(answer) <= len(options):
            answer = options[int(answer) - 1]
        app.exit(result=answer.strip())

    def _cancel(_event: object) -> None:
        app.exit(exception=EOFError())

    for key in ("down", "tab"):
        _ = bindings.add(key)(_next_choice)
    for key in ("up", "s-tab"):
        _ = bindings.add(key)(_previous_choice)
    _ = bindings.add("enter")(_accept)
    for key in ("escape", "c-c", "c-d"):
        _ = bindings.add(key, eager=True)(_cancel)

    def changed(_buffer: object) -> None:
        nonlocal selected
        selected = None

    field.buffer.on_text_changed += changed
    app: Application[str] = Application(
        layout=Layout(
            HSplit(
                [
                    Window(FormattedTextControl(context), dont_extend_height=True, wrap_lines=True),
                    field,
                ]
            ),
            focused_element=field,
        ),
        key_bindings=bindings,
        style=STYLE,
        output=create_output(stdout=sys.stderr),
        full_screen=False,
        erase_when_done=True,
    )
    with patch_stdout():
        answer = app.run()
    status(f"{label}: {answer}")
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
    output = Console(force_terminal=interactive(), no_color=not interactive())
    if interactive():
        output.print(Markdown(text))
    else:
        output.print(text, markup=False, highlight=False, soft_wrap=True)


def command_list(rows: Sequence[tuple[str, str, str]]) -> None:
    output = Console(force_terminal=interactive(), no_color=not interactive())
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
