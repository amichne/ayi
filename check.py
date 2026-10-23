from __future__ import annotations

import subprocess
from pathlib import Path

from bootstrap import launcher


def main() -> int:
    root = Path(__file__).resolve().parent
    if (root / "install.sh").read_text() != launcher((root / "bootstrap.py").read_text()):
        print("install.sh differs from bootstrap.py. Regenerate the launcher.")
        return 1
    for arguments in (
        ["ruff", "format", "--check", "."],
        ["ruff", "check", "."],
        ["pyright", "--project", "pyproject.toml"],
        ["pytest", "-q"],
    ):
        result = subprocess.run(["uv", "run", "--locked", *arguments], cwd=root, check=False)
        if result.returncode:
            return result.returncode
    shell_source = ["bin/ayi", "bin/ayi-tool", "install.sh"]
    for arguments in (
        ["shellcheck", "-S", "warning", *shell_source],
        *(["bash", "-n", name] for name in shell_source),
        ["zsh", "-n", "shell/ayi.zsh"],
    ):
        result = subprocess.run(arguments, cwd=root, check=False)
        if result.returncode:
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
