from __future__ import annotations

import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main(arguments: list[str]) -> int:
    if arguments != ["init", "zsh"]:
        print("ayi: usage: ayi shell init zsh", file=sys.stderr)
        return 64
    print(f"typeset -g _ayi_root={shlex.quote(str(ROOT))}")
    print((ROOT / "shell/ayi.zsh").read_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
