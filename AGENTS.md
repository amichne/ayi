# Ayi source

`mise.toml` owns task discovery. `tasks/` contains ordinary executables.
`create.py` owns draft review, verification, and local Git registration.

It creates uv script locks before review and preserves them through verification and registration.
Keep each generated uv script beside its `.lock` file under `tasks/` or `verification/`.

The registry uses this checkout's Git index. Keep dotfiles and HOME Git indexes separate.
`factory.py` owns the model-driven tool interview and read-only explanations.
`sessions.py` owns interview values and local checkpoints under XDG state.
Keep each submitted answer, pending answer, and proposed approach in a checkpoint.
A fork preserves its parent and needs a separate generated draft.
`create.py` owns Pi and Codex implementation adapters. Keep generation output in local logs.
`install.py` owns installed link discovery and ownership. `bin/ayi-tool` dispatches direct tool invocations.
Keep shell startup outside the installer. Force never bypasses review, verification, or dirty-registry protection.
`bootstrap.py` owns archive installation, updates, version output, and the private Git registry.
`pyproject.toml` owns the version. Archive version output also names the source commit.
Updates preserve generated tools and local commits. Dirty registries and source conflicts cause rejection.
Its Git commands ignore ambient Git repository and index selectors.
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
