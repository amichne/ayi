# Ayi

Ayi is a shell tooling factory. Describe a tool, answer task-specific menus, review it, and keep it in Git.
Pi creates the questions and choices at runtime. Ayi installs each tool in `~/.local/bin`.

```sh
ayi new large-files "Find large files and show their sizes"
large-files --help
ayi modify large-files "Add a minimum size flag"
large-files --min-size 100M
ayi explain large-files
ayi                          # enter a new request interactively
```

## Install

Install mise, uv 0.12.9 or later, curl, Pi, Git, and ShellCheck through your approved package provider.
Inline input and presentation use locked prompt_toolkit and Rich dependencies; fzf is not required.
Authenticate Pi through its existing commands.
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
./bin/ayi install plan
```

After source review, install the links:

```sh
./bin/ayi install apply
```

This command trusts the reviewed registry through `mise trust`.
It creates `~/.local/bin/ayi` and `${XDG_CONFIG_HOME:-$HOME/.config}/ayi` as links to this checkout.
It also restores `~/.local/bin/<name>` launchers for saved tools with requirements and task files.
It preserves existing targets when their ownership differs.
If a file operation fails or you interrupt installation, the installer removes links that it created.
It does not edit `.zshrc`, `.bashrc`, or the global mise configuration.

The shell's `PATH` must include `~/.local/bin` to invoke Ayi and its tools by name.
Add that directory to your existing shell configuration if it is absent.

## Discover and run

```sh
ayi list
large-files --help
large-files --min-size 100M
```

Tools run directly, without an `ayi run` prefix. Arguments, the caller's working directory,
and exit status are preserved. The launcher does not invoke Pi, uv, or mise to run a tool;
each tool still needs its declared runtime dependencies.
A standalone `--help` or `-h` prints the tool's native help followed by:

```text
Created with ayi. Manage this tool with: ayi modify large-files
```

The factory has no bundled CI or PR-review commands. The old `create`, `run`, and `inspect`
actions are no longer supported. Use `new`, the installed tool itself, and `explain` instead.

## Create, modify, and explain commands

```sh
ayi new my-command "Summarize the supplied log file"
ayi modify my-command "Accept stdin as well as file arguments"
ayi explain my-command
ayi new --force my-command "Replace it with a different log summarizer"
```

`new <name> <prompt>` keeps the supplied name and asks for the preferred invocation,
then resolves consequential requirements as needed. Names use 1–48 lowercase letters,
digits, or hyphens, starting with a letter; `ayi` and `ayi-tool` are reserved.
Run `ayi` or `ayi new` to enter the request and choose a model-suggested name instead.
A single quoted, multiword prompt (`ayi new "Find large files"`) also starts the naming interview.
Each question and its choices come from Pi, using the request and
all previous answers, rather than a fixed questionnaire. `modify` supplies the existing
source and requirements and asks only about unresolved changes; the name stays fixed.

Type to filter the inline choices, use arrow keys or Tab and Enter to select, or choose
**Write a different answer** for free text. Esc or Ctrl-C cancels without registration.
Candidates and context stay above the final input row, without an alternate screen.
Completed steps remain in scrollback. Menus wrap at narrow widths. With `NO_COLOR`,
`TERM=dumb`, CI, or redirected input, plain numbered choices accept a number or a custom answer.
EOF or an empty answer cancels.
Run `ayi`, `ayi new [name]`, or `ayi modify <name>` without a prompt to enter text with ordinary
cursor editing. No multiline editor or persistent prompt history is provided.
Review shows proposed source or a diff against installed source, then requests explicit
verification and registration approvals. Diagnostics and progress go to stderr; command results
stay on stdout. Animations run only while work is pending on an interactive terminal.

Pi uses its existing authentication and model settings. Interview and explanation calls
have tools disabled. After the interview, Pi writes a draft and exits automatically;
there is no Pi session to quit. Draft generation enables only read/write/edit file tools,
not command execution. These tools are not a filesystem sandbox.

The caller prints the requirements, task source, and verification source.
Examine all three before you approve verification.
The verification receives a disposable `HOME` and XDG directories without inherited credentials.
Bash tasks also pass Bash syntax and ShellCheck checks.
Changed verification inputs cause rejection. Ayi also runs `--help` and requires nonempty
stdout and a successful exit before allowing registration.

After verification passes, the caller requests approval to commit, register, and install
the command at `~/.local/bin/<name>`.
The local commit uses the Ayi checkout and contains only the command's files:

```text
tasks/<name>
verification/<name>
requirements/<name>.md
```

Unrelated staged files stay staged. By default, `new` cannot overwrite existing commands;
deleted paths in the Git index or current commit also prevent new registration.
`modify` requires the command's files to be clean and tracked (missing requirements or
verification files may be added). It rechecks the original bytes, permissions, and Git
status before replacement. A changed command causes rejection.

`--force` on `new` allows rebuilding a clean registered tool. On `new` or `modify`, it also
allows replacing an existing file or symlink at that name in `~/.local/bin`.
It requires an explicit name and never replaces directories or other special files,
discards dirty registry edits, or skips approval and verification.
A failed commit restores the original registry files and displaced executable/link.
A successful replacement discards the displaced executable/link.
Use `--` before prompt text when it contains a standalone `--force` that should be literal.

Mise lists the task file in later sessions; the installed command is immediately callable
when `~/.local/bin` is on PATH.
No push occurs. Normal Git hooks and local Git identity still govern the commit.

`explain` sends source and any saved requirements and verification to Pi, and prints
usage, dependencies, effects, and limitations without running the command. It explains
Ayi registry commands, not arbitrary executables from PATH. Explanation is model analysis,
not a claim that the tool has been tested.

Drafts remain under the ignored `drafts/` directory after rejection or cancellation.
To refine an existing draft, use Pi in that directory.

Generated code runs with your operating-system permissions after approval.
A disposable home separates normal configuration. It does not provide a filesystem or network sandbox.
Interview and explanation calls disable tools, MCP, extensions, skills, and project context.
Generation also disables these resources except its explicit file tools.

## Status

| Result | Exit status |
| --- | --- |
| Successful operation | `0` |
| Declined verification or registration | `1` |
| Invalid action or command name | `64` |
| Invalid draft, installation conflict, or dirty registry files | `65` |
| Missing dependency or unavailable model response/tool | `69` |
| Failed local file operation | `74` |
| Command changed during modification | `75` |
| Interrupted operation or cancelled interview | `130` |

Native task, verification, and subprocess failures preserve their exit status,
except that failed or empty help is treated as an invalid draft (`65`).

## Recover and remove

Keep the checkout and generated commits in Git to recover the same definitions on another machine.
On a fresh clone, run `./bin/ayi install apply` to restore Ayi and generated tool launchers.
Pi authentication remains a machine-local prerequisite for factory operations.

To remove the installed links, use:

```sh
./bin/ayi install uninstall
```

The command removes owned Ayi and generated tool links while preserving source files,
drafts, and mise trust records.
To remove one generated command, remove its owned `~/.local/bin/<name>` link,
then review and commit the removal of its three source files.

## Acceptance checks

```sh
uv run --locked python check.py
```

The command runs Ruff, strict Pyright, pytest, Bash syntax, and ShellCheck.
Tests use real mise, isolated homes, and bounded substitutes for Pi.
Certificate tests use a local HTTPS server and disposable certificates from OpenSSL.
They prove local control flow and data handling, not model judgment.
Live Pi smoke runs need existing authentication and remain separate from automated tests.

## Source

The initial source comes from `amichne/dotfiles` at [this commit](https://github.com/amichne/dotfiles/commit/7eb9d7e0f51c930f4a3b7a65a7839cb76f0c409d).
Ayi owns the command registry, prompts, installer, and tests.
Personal shell and Pi settings remain in dotfiles.
