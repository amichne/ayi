# Ayi

Ayi supplies the `ai` command to create reusable commands, review a PR locally, or diagnose CI failures.
Mise discovers and runs tasks. Pi refines commands and analyzes evidence. GitHub CLI supplies PR data.

## Install

Install mise, uv 0.12.9 or later, Pi, GitHub CLI, Git, and ShellCheck through your approved package provider.
Authenticate Pi and `gh` through their existing commands.
The Python project uses Python 3.12 and locked dependencies. Shell startup does not install dependencies.

Clone the repository into its own directory:

```sh
git clone https://github.com/amichne/ayi.git
cd ayi
```

Examine the source and installation plan:

```sh
./bin/ai --help
./bin/ai inspect ci
./bin/ai install plan
```

If mise rejects the registry before inspection, read `mise.toml` and the task files directly.
After source review, install the links:

```sh
./bin/ai install apply
```

This command trusts the reviewed registry through `mise trust`.
It creates `~/.local/bin/ai` and `${XDG_CONFIG_HOME:-$HOME/.config}/ai` as links to this checkout.
It preserves existing targets when their ownership differs.
If a file operation fails or you interrupt installation, the installer removes links that it created.
It does not edit `.zshrc`, `.bashrc`, or the global mise configuration.

The shell's `PATH` must include `~/.local/bin`.
If that path is absent, use `./bin/ai` directly or add `~/.local/bin` to `PATH`.

## Discover and run

```sh
ai list
ai inspect ci
ai run ci 1025
ai run review 1025
ai run ci https://github.com/OWNER/REPO/pull/1025
ai run review 1025 --repo OWNER/REPO
```

Numeric PR identifiers use the caller's Git repository.
Both tasks also accept `--repo HOST/OWNER/REPO` for GitHub Enterprise.
Arguments reach the task unchanged. The dispatcher rejects unknown names and path traversal.

`ai inspect` prints native mise information, the invocation, and the complete task source.
The workflow tasks delegate to `workflows.py`. Examine that file and `prompts/` for their shared implementation.

Tasks also run as ordinary executables:

```sh
./tasks/ci 1025
./tasks/review 1025 --repo OWNER/REPO
mise -C ~/.config/ai tasks ls
```

## Create a command

```sh
ai create "Summarize failing CI checks for a PR"
```

Pi starts an interactive session with its existing authentication and model settings.
The creation prompt asks Pi to resolve missing requirements before implementation.
Pi can read and write draft files. It cannot execute commands through the enabled tools.

When Pi finishes the draft, exit Pi with `/quit`.
The caller prints the requirements, task source, and verification source.
Examine all three before you approve verification.
The verification receives a disposable `HOME` and XDG directories without inherited credentials.
Bash tasks also pass Bash syntax and ShellCheck checks.
Changed verification inputs cause rejection.

After verification passes, the caller requests approval to commit and register the command.
The local commit uses the Ayi checkout and contains only these new files:

```text
tasks/<name>
verification/<name>
requirements/<name>.md
```

Unrelated staged files stay staged. Existing commands cannot be overwritten.
Deleted paths in the Git index or current commit also prevent registration.
A failed commit removes the new files from the registry.
Mise discovers the task file in later sessions.
No push occurs. Normal Git hooks and local Git identity still govern the commit.

Drafts remain under the ignored `drafts/` directory after rejection or cancellation.
To refine an existing draft, use Pi in that directory.
To register a revised draft, start `ai create` again and ask Pi to reuse its files.

Generated code runs with your operating-system permissions after approval.
A disposable home separates normal configuration. It does not provide a filesystem or network sandbox.
Read-only review and CI use Pi with tools, MCP, extensions, skills, and project context disabled.
Creation also disables these resources except its explicit file tools.

## Reports and status

Reports and evidence stay in `${XDG_STATE_HOME:-$HOME/.local/state}/ai/reports/`.
Each invocation uses a separate directory with restricted permissions.
Reports name the PR URL and head commit. Review also saves the diff.
CI saves available failure logs and sends bounded excerpts to Pi.

The collector reexamines the PR after separate requests. A changed head or base commit causes rejection before Pi analysis.
Actions logs enter the report only when the run head matches the PR head.
The selected job must also belong to that run.
External checks without Actions job links remain visible with an explicit log limitation.

CI keeps pending, successful, failed, skipped, neutral, canceled, and unavailable records distinct.
No check records means unavailable. It never means success.
An empty Pi response means unavailable. The report keeps the native evidence.

| Result | Exit status |
| --- | --- |
| Successful CI or complete local review | `0` |
| Failed CI | `1` |
| Pending CI without a failed check | `8` |
| Invalid command or PR value | `64` |
| Invalid draft, installation conflict, or oversized diff | `65` |
| Unavailable GitHub data, Pi diagnosis, or conclusive CI result | `69` |
| Failed local file operation | `74` |
| PR changed during collection | `75` |
| Interrupted operation | `130` |

Native task failures preserve their exit status. Argument-parser usage errors return `2`.
A failed check takes precedence when other checks remain pending.
Pi output is analysis. The collected GitHub data remains authoritative.
Review findings separate defects supported by the supplied evidence from uncertain concerns.
The workflows do not run repository tests, publish comments, rerun jobs, or repair code.

## Recover and remove

Keep the checkout and generated commits in Git to recover the same definitions on another machine.
On a fresh clone, run `./bin/ai install apply`.
Pi authentication and `gh` authentication remain machine-local prerequisites.

To remove the installed links, use:

```sh
./bin/ai install uninstall
```

The command preserves source files, reports, drafts, and mise trust records.
To remove a generated command, review and commit the removal of its three source files.

## Acceptance checks

```sh
uv run --locked python check.py
```

The command runs Ruff, strict Pyright, pytest, Bash syntax, and ShellCheck.
Tests use real mise, isolated homes, and bounded substitutes for Pi and GitHub CLI.
They prove local control flow and data handling. They do not prove model judgment or GitHub availability.
Live Pi and GitHub smoke runs need existing authentication and remain separate from automated tests.

## Source

The initial source comes from `amichne/dotfiles` at commit `7eb9d7e0f51c930f4a3b7a65a7839cb76f0c409d`.
Ayi owns the command registry, prompts, installer, and tests.
Personal shell and Pi settings remain in dotfiles.
