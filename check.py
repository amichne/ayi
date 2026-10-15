from __future__ import annotations

import subprocess
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent
    for arguments in (
        ["ruff", "format", "--check", "."],
        ["ruff", "check", "."],
        ["pyright", "--project", "pyproject.toml"],
        ["pytest", "-q"],
    ):
        result = subprocess.run(["uv", "run", "--locked", *arguments], cwd=root, check=False)
        if result.returncode:
            return result.returncode
    shell_source = ["bin/ai", "tasks/ci", "tasks/review"]
    for arguments in (
        ["shellcheck", "-S", "warning", *shell_source],
        *(["bash", "-n", name] for name in shell_source),
    ):
        result = subprocess.run(arguments, cwd=root, check=False)
        if result.returncode:
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
