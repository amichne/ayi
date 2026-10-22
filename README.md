# Ayi

Ayi is a shell tooling factory. Describe a tool, answer task-specific menus, review it, and keep it in Git.
Pi creates the questions and choices at runtime. Mise discovers and runs the resulting commands.
Bundled commands also review PRs locally and diagnose CI failures using GitHub CLI.

```sh
ayi new "Find large files and show their sizes"
ayi modify large-files "Add a minimum size flag"
ayi explain large-files
ayi run large-files --min-size 100M
ayi                          # enter a new request interactively
```

## Install

Install mise, uv 0.12.9 or later, curl, Pi, GitHub CLI, Git, and ShellCheck through your approved package provider.
Install fzf 0.60 or later for interactive fuzzy menus; without it, Ayi uses numbered text choices.
Authenticate Pi and `gh` through their existing commands.
The Python project uses Python 3.12 and locked dependencies. Shell startup does not install dependencies.

Install Ayi without a repository clone:

```sh
curl -fsSL https://raw.githubusercontent.com/amichne/ayi/main/install.sh | sh
```

The installer downloads a GitHub source archive for an exact commit.
It stores the source in `${XDG_DATA_HOME:-$HOME/.local/share}/ayi/source`.
It creates a local Git registry so that command creation can commit new tasks.
The first commit uses the Ayi installer identity, with signing and hooks disabled for that commit.
Later command commits use your normal Git identity and hooks.
The installer then uses the existing link installer and `mise trust`.

The launcher uses uv-managed Python and the standard library.
Source downloads use `/usr/bin/curl` on macOS and `curl` on other systems.
Apple's curl uses Keychain trust. uv uses system certificates for Python and dependency downloads.
Both clients reject certificates from untrusted issuers.
The operating system must trust your organization's certificate issuer.
See [uv TLS certificates](https://docs.astral.sh/uv/concepts/authentication/certificates/) for system certificate settings.
The installed runtime uses the downloaded `uv.lock` and disables source builds.
Existing source directories and conflicting links cause rejection.
Reruns reuse an owned registry and preserve generated commands and commits.
They restore missing links without a source upgrade.
The installer keeps authentication and shell startup outside its scope.

For a developer checkout, clone the repository into its own directory:

```sh
git clone https://github.com/amichne/ayi.git
cd ayi
```

Examine the source and installation plan:

```sh
./bin/ayi --help
./bin/ayi inspect ci
./bin/ayi install plan
```

If mise rejects the registry before inspection, read `mise.toml` and the task files directly.
After source review, install the links:

```sh
./bin/ayi install apply
```

This command trusts the reviewed registry through `mise trust`.
It creates `~/.local/bin/ayi` and `${XDG_CONFIG_HOME:-$HOME/.config}/ayi` as links to this checkout.
It preserves existing targets when their ownership differs.
If a file operation fails or you interrupt installation, the installer removes links that it created.
It does not edit `.zshrc`, `.bashrc`, or the global mise configuration.

The shell's `PATH` must include `~/.local/bin`.
If that path is absent, use `./bin/ayi` directly or add `~/.local/bin` to `PATH`.

## Discover and run

```sh
ayi list
ayi inspect ci
ayi run ci 1025
ayi run review 1025
ayi run ci https://github.com/OWNER/REPO/pull/1025
ayi run review 1025 --repo OWNER/REPO
```

Numeric PR identifiers use the caller's Git repository.
Both tasks also accept `--repo HOST/OWNER/REPO` for GitHub Enterprise.
Arguments reach the task unchanged. The dispatcher rejects unknown names and path traversal.

`ayi inspect` prints native mise information, the invocation, and the complete task source.
The workflow tasks delegate to `workflows.py`. Examine that file and `prompts/` for their shared implementation.

Tasks also run as ordinary executables:

```sh
./tasks/ci 1025
./tasks/review 1025 --repo OWNER/REPO
mise -C ~/.config/ayi tasks ls
```

## Create, modify, and explain commands

```sh
ayi new "Summarize failing CI checks for a PR"
ayi modify my-command "Accept stdin as well as file arguments"
ayi explain my-command
```

`new` first asks for the command name and preferred invocation, then resolves consequential
requirements as needed. Each question and its choices come from Pi, using the request and
all previous answers, rather than a fixed questionnaire. `modify` supplies the existing
source and requirements and asks only about unresolved changes; the name stays fixed.

Type to filter the fzf choices, use arrow keys and Enter to select, or choose
**Write a different answer** for free text. Esc or Ctrl-C cancels without registration.
Menus wrap at narrow widths and honor `NO_COLOR`. If fzf is absent or input/output is
redirected, numbered choices accept a number or a custom answer. EOF or an empty answer cancels.
Run `ayi`, `ayi new`, or `ayi modify <name>` without a prompt to enter text with ordinary
readline cursor editing. No multiline editor or persistent prompt history is provided.

Pi uses its existing authentication and model settings. Interview and explanation calls
have tools disabled. After the interview, Pi writes a draft and exits automatically;
there is no Pi session to quit. Draft generation enables only read/write/edit file tools,
not command execution. These tools are not a filesystem sandbox.

The caller prints the requirements, task source, and verification source.
Examine all three before you approve verification.
The verification receives a disposable `HOME` and XDG directories without inherited credentials.
Bash tasks also pass Bash syntax and ShellCheck checks.
Changed verification inputs cause rejection.

After verification passes, the caller requests approval to commit and register the command.
The local commit uses the Ayi checkout and contains only the command's files:

```text
tasks/<name>
verification/<name>
requirements/<name>.md
```

Unrelated staged files stay staged. `new` cannot overwrite existing commands;
deleted paths in the Git index or current commit also prevent new registration.
`modify` requires the command's files to be clean and tracked (missing requirements or
verification files may be added). It rechecks the original bytes, permissions, and Git
status before replacement. A changed command causes rejection. A failed commit restores
the original files and removes newly added files.
Mise discovers the task file in later sessions.
No push occurs. Normal Git hooks and local Git identity still govern the commit.

`explain` sends source and any saved requirements and verification to Pi, and prints
usage, dependencies, effects, and limitations without running the command. It explains
Ayi registry commands, not arbitrary executables from PATH. Explanation is model analysis,
not a claim that the tool has been tested.

Drafts remain under the ignored `drafts/` directory after rejection or cancellation.
To refine an existing draft, use Pi in that directory.
The legacy `ayi create "<description>"` retains the interactive Pi workflow: exit with
`/quit` to review and register. It can also be used to ask Pi to reuse a saved draft.

Generated code runs with your operating-system permissions after approval.
A disposable home separates normal configuration. It does not provide a filesystem or network sandbox.
Read-only review and CI use Pi with tools, MCP, extensions, skills, and project context disabled.
Creation also disables these resources except its explicit file tools.

## Reports and status

Reports and evidence stay in `${XDG_STATE_HOME:-$HOME/.local/state}/ayi/reports/`.
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
| PR changed during collection or command changed during modification | `75` |
| Interrupted operation or cancelled interview | `130` |

Native task failures preserve their exit status. Argument-parser usage errors return `2`.
A failed check takes precedence when other checks remain pending.
Pi output is analysis. The collected GitHub data remains authoritative.
Review findings separate defects supported by the supplied evidence from uncertain concerns.
The workflows do not run repository tests, publish comments, rerun jobs, or repair code.

## Recover and remove

Keep the checkout and generated commits in Git to recover the same definitions on another machine.
On a fresh clone, run `./bin/ayi install apply`.
Pi authentication and `gh` authentication remain machine-local prerequisites.

To remove the installed links, use:

```sh
./bin/ayi install uninstall
```

The command preserves source files, reports, drafts, and mise trust records.
To remove a generated command, review and commit the removal of its three source files.

## Acceptance checks

```sh
uv run --locked python check.py
```

The command runs Ruff, strict Pyright, pytest, Bash syntax, and ShellCheck.
Tests use real mise, isolated homes, and bounded substitutes for Pi and GitHub CLI.
Certificate tests use a local HTTPS server and disposable certificates from OpenSSL.
They prove local control flow and data handling. They do not prove model judgment or GitHub availability.
Live Pi and GitHub smoke runs need existing authentication and remain separate from automated tests.

## Source

The initial source comes from `amichne/dotfiles` at commit `7eb9d7e0f51c930f4a3b7a65a7839cb76f0c409d`.
Ayi owns the command registry, prompts, installer, and tests.
Personal shell and Pi settings remain in dotfiles.
