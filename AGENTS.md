# Ayi source

`mise.toml` owns task discovery. `tasks/` contains ordinary executables.
`create.py` owns draft review, verification, and local Git registration.
The registry uses this checkout's Git index. Keep dotfiles and HOME Git indexes separate.
`workflows.py` owns GitHub data and local reports. Pi receives evidence with its tools disabled.
`install.py` owns the two installed links. Keep shell startup outside this installer.

From the repository root, run `uv run --locked python check.py`.

Keep prompts in `prompts/`. Keep generated task verification in `verification/`.
Keep generated command requirements in `requirements/`. Drafts and reports remain outside Git.

Tests use disposable HOME and XDG directories without host credentials.
Keep authentication, sessions, and machine settings outside the repository.
Do not install, publish, or change remotes without explicit authorization.
