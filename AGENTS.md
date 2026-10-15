# Ayi source

`mise.toml` owns task discovery. `tasks/` contains ordinary executables.
`create.py` owns draft review, verification, and local Git registration.
The registry uses this checkout's Git index. Keep dotfiles and HOME Git indexes separate.
`workflows.py` owns GitHub data and local reports. Pi receives evidence with its tools disabled.
`install.py` owns the two installed links. Keep shell startup outside this installer.
`bootstrap.py` owns archive installation and the private Git registry.
Its seed commands ignore ambient Git repository and index selectors.
`install.sh` is generated from `bootstrap.py`. Regenerate it with
`uv run --locked python -c 'from pathlib import Path; from bootstrap import launcher; Path("install.sh").write_text(launcher(Path("bootstrap.py").read_text()))'`.
The launcher uses uv-managed Python and the standard library.
Source downloads use `/usr/bin/curl` on macOS and `curl` on other systems.
uv uses system certificates for Python and dependency downloads.
Keep TLS authentication enabled.
Installed runtime dependencies stay under the downloaded project's `uv.lock`.

From the repository root, run `uv run --locked python check.py`.

Keep prompts in `prompts/`. Keep generated task verification in `verification/`.
Keep generated command requirements in `requirements/`. Drafts and reports remain outside Git.

Tests use disposable HOME and XDG directories without host credentials.
Keep authentication, sessions, and machine settings outside the repository.
Do not install, publish, or change remotes without explicit authorization.
