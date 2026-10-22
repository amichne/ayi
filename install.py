from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

ROOT = Path(__file__).resolve().parent


class LinkState(StrEnum):
    ABSENT = "absent"
    OWNED = "owned"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class Link:
    source: Path
    destination: Path
    state: LinkState


def valid_name(name: str) -> bool:
    return (
        name not in ("ayi", "ayi-tool") and re.fullmatch(r"[a-z][a-z0-9-]{0,47}", name) is not None
    )


def link_for(source: Path, destination: Path) -> Link:
    if destination.is_symlink() and destination.resolve() == source:
        state = LinkState.OWNED
    elif destination.exists() or destination.is_symlink():
        state = LinkState.CONFLICT
    else:
        state = LinkState.ABSENT
    return Link(source, destination, state)


def command_link(name: str, home: Path | None = None) -> Link:
    if not valid_name(name):
        raise ValueError(f"invalid command name: {name}")
    return link_for(ROOT / "bin/ayi-tool", (home or Path.home()) / ".local/bin" / name)


def links_for(home: Path, config: Path) -> tuple[Link, ...]:
    if not config.is_absolute():
        raise ValueError("XDG_CONFIG_HOME must be absolute")
    result: list[Link] = []
    for source, destination in (
        (ROOT / "bin/ayi", home / ".local/bin/ayi"),
        (ROOT, config / "ayi"),
    ):
        result.append(link_for(source, destination))
    for requirements in sorted((ROOT / "requirements").glob("*.md")):
        name = requirements.stem
        if valid_name(name) and (ROOT / "tasks" / name).is_file():
            result.append(command_link(name, home))
    return tuple(result)


def links() -> tuple[Link, ...]:
    home = Path.home()
    return links_for(home, Path(os.environ.get("XDG_CONFIG_HOME", str(home / ".config"))))


def main(arguments: list[str]) -> int:
    if arguments not in (["plan"], ["apply"], ["uninstall"]):
        print("ayi: usage: ayi install <plan|apply|uninstall>", file=sys.stderr)
        return 64
    targets = links()
    for target in targets:
        print(f"{target.state.value}: {target.destination} -> {target.source}")
    print(f"mise trust: {ROOT / 'mise.toml'}")
    if arguments == ["plan"]:
        return 0
    if any(target.state == LinkState.CONFLICT for target in targets):
        print("ayi: installation conflict. Existing targets stay unchanged.", file=sys.stderr)
        return 65
    if arguments == ["uninstall"]:
        for target in targets:
            if target.state == LinkState.OWNED:
                target.destination.unlink()
        return 0
    # Explicit apply authorizes native mise trust for this reviewed registry.
    trusted = subprocess.run(["mise", "trust", str(ROOT / "mise.toml")], check=False)
    if trusted.returncode:
        return trusted.returncode
    created: list[Path] = []
    try:
        for target in targets:
            if target.state == LinkState.ABSENT:
                target.destination.parent.mkdir(parents=True, exist_ok=True)
                target.destination.symlink_to(target.source)
                created.append(target.destination)
    except (OSError, KeyboardInterrupt):
        for path in created:
            path.unlink()
        raise
    print("Installed. Invoke ayi from a shell whose PATH includes ~/.local/bin.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except ValueError as error:
        print(f"ayi: {error}", file=sys.stderr)
        raise SystemExit(65) from error
    except OSError as error:
        print(f"ayi: installation failed: {error.strerror}", file=sys.stderr)
        raise SystemExit(74) from error
    except KeyboardInterrupt:
        raise SystemExit(130) from None
