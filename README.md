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

## Shared CLI components

New Python tools use one shared runtime for arguments, help, choices, settings, JSON results, and bounded Pi requests.
The interface manifest supplies the public contract. A small domain handler supplies the operation.
Ayi assembles the launcher before review and supplies the same design policy to every model session.
Existing standalone tools keep their contracts.

Users change supported defaults in `$XDG_CONFIG_HOME/ayi-tools/<name>.toml`, or `~/.config/ayi-tools/<name>.toml`.
The file accepts a `[defaults]` table with declared option keys.
Explicit arguments override declared environment variables, user settings, and manifest defaults.
Unknown or malformed settings fail before domain work.
Use `ayi modify <name>` to change behavior through a reviewed draft without a source edit.

Read [Compose a CLI with Ayi](docs/cli-design.md) for the chosen conventions, source decisions, component contracts, and executable example.
Read [Configure an Ayi tool](docs/cli-configuration.md) for the configuration structure and rules for future extensions.

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

### Update and version

```sh
ayi --version
ayi -V
ayi update
```

Both version flags print the version from `pyproject.toml`.
Archive installations also show the source commit.
Version output uses the installed Python runtime without a network request or dependency sync.

`ayi update` downloads the latest `main` commit for an archive installation.
It preserves generated tools and local Git history.
Dirty registries and local changes that conflict with new source cause rejection before replacement.
The command records source changes in a local commit, then refreshes dependencies, links, and mise trust.
The source commit uses the installer identity with hooks and signing disabled.
If setup fails after that commit, the command reports the failure. Run `ayi update` again to finish setup.
Developer checkouts use Git for source updates.

For an older archive installation without `ayi update`, use the current bootstrap:

```sh
curl -fsSL https://raw.githubusercontent.com/amichne/ayi/main/install.sh | sh -s -- update
```

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

Use arrow keys or Tab to copy a choice into the input field. Type to append or edit its text.
Choices remain visible and never filter as you type. Select **Write your own answer** for an empty field.
You can also type a custom answer directly. Each answer receives a checkpoint before its submission review.
Choose **Submit answer** to send it to the design model, or **Edit answer** to change it first.

Candidates and context precede the input row. The interface uses no alternate screen.
With `NO_COLOR`, `TERM=dumb`, CI, or redirected streams, numbered choices accept a number or custom text.
During plain-text edits, Enter keeps the current answer. A `+ text` response appends text.
Esc, Ctrl-C, and EOF exit the flow. The last checkpoint remains available for resume.
The interface uses a single-line editor. Sessions preserve accepted answers and pending answers outside Git.

Use these controls at a question, answer review, design review, or draft review:

- `/clarify <direction>` changes the direction of future questions and the proposed approach.
- `/show-me` or `/unclear` requests examples and explanation without changing the current answer.
- `/review` displays all submitted questions, answers, directions, and the proposed approach when available.
- `/edit N` returns to answer N. Later answers leave this branch but remain in earlier checkpoints.
- `/settings` selects the design model, implementation harness, and implementation model.
- `/pause` saves the flow and exits. `/fork` saves a separate flow and prints its resume command.

After all answers, Ayi displays the complete design for review. Choose **Implement** to start generation.
After generation, choose **Review draft** to examine its file paths and request verification.
Verification and registration still need separate approvals. Source and generation logs stay in files.
Progress and diagnostics go to stderr. Command results stay on stdout.

### Sessions and models

```sh
ayi sessions
ayi review <session-id>
ayi resume <session-id>
ayi fork <session-id>
ayi fork <session-id> --checkpoint 4
ayi new my-command "Summarize logs" --design-model <pi-model> --implementation-harness codex --implementation-model <codex-model>
```

`ayi review` lists checkpoint numbers without a model call. `resume` continues the latest checkpoint.
`fork` creates a separate session from the latest or selected checkpoint. It preserves the parent.
A fork from a generated draft returns to design review and needs its own generation.
A completed session needs an earlier checkpoint for a fork.
Sessions belong to one registry. Resume rejects changes to the original command source or permissions.
Only one process can resume a session at a time.

Sessions, checkpoints, drafts, and generation logs use `${XDG_STATE_HOME:-$HOME/.local/state}/ayi/sessions/`.
Session directories use mode `0700`. Checkpoints and generation logs use mode `0600`.
An interrupted model call leaves the previous checkpoint intact. Resume can retry that call.
A saved generated draft resumes without another implementation call.

Design uses Pi. Implementation supports Pi and Codex, with a separate model choice for each phase.
Omitted model flags use the harness default. Model IDs remain subject to the selected harness and account.
Use `/settings` before **Implement** to change the choices. Model selection does not imply available authentication.
Changes to settings after generation return to design review and need another explicit implementation invocation.

### Completion

After installing Ayi, explicitly source its integration **after `compinit`** in your Zsh configuration:

```zsh
autoload -Uz compinit
compinit
eval "$(ayi shell init zsh)"
```

The installer never edits startup files. The integration registers Ayi and its owned installed
tool links, and a prompt hook refreshes registrations when registry/link membership changes.
There is no Python process on unchanged prompts, no daemon, and no per-tool shell script.
Tab completes option names, declared choices, files, and directories. File names with spaces
are shell-quoted. Completion never invokes Pi or runs a tool. It uses the already initialized
locked runtime without uv synchronization or bytecode writes; for a source checkout, first run
`ayi install plan` (or `uv sync --locked`). Bash, Fish, and custom dynamic providers are not
supported yet; unknown provider kinds are rejected rather than executed.
uv precompiles dependency bytecode during explicit runtime initialization, not while completing.

`interface/<name>.json` uses schema version 1 and declares `name`, `summary`, `positionals`,
`options`, optional invocation `examples`, and output capabilities (`text`, `json`, `jsonl`,
or `native`). Each value has an explicit completion kind: `none`, `choices` (with `values`),
`file`, or `directory`. Option aliases must be unique. Only the final positional may repeat.
Legacy commands without a manifest still run; no interface is guessed from their help.

The read-only JSON API is `ayi complete`, receiving one request on stdin:

```json
{"command":"large-files","args":["--sort","s"],"cursor":1,"cwd":"/tmp"}
```

`cursor` is the zero-based active **token** index, not a byte offset. Supply only the active
token's prefix before the text cursor. An index equal to `args` length means a trailing empty
token. Results are an array of `{value, description, kind}` candidates; `kind` is `value`,
`file`, or `directory`. `--` ends option completion and `--option=value` is supported.
No candidates is a successful empty array. Invalid manifests/requests fail on stderr, never
as candidate text. Options use separate values or long-option equals syntax, not short clusters.

Pi uses its existing authentication. Design and explanation calls disable tools.
Pi implementation enables only its read, write, and edit tools. These tools provide no filesystem sandbox.
Codex implementation uses `exec` with a read-only sandbox and ignores user configuration.
It returns a structured bundle of file contents. Ayi writes that bundle into the draft directory.
Both adapters keep their stdout and stderr in `generation.log`.

Every new or changed draft must contain a valid `interface.json`. Legacy commands receive one during modification.

For standalone Python scripts, Ayi creates adjacent locks with `uv lock --script` before review.
This step resolves dependencies without script execution. Dependency resolution can contact package indexes.
Ayi examines existing locks with `uv lock --check --script`. A stale lock causes rejection without an automatic refresh.

The review includes paths to `task.lock` and `verify.lock` when those scripts use uv.
Verification uses the same lock bytes in its disposable directory.
Registration commits each lock beside its script as `tasks/<name>.lock` or `verification/<name>.lock`.
Modification protects both locks from concurrent changes. A replacement without uv removes the old lock in the same commit.

The caller displays requirements and paths to the task, verification, and interface files.
Examine these files before you approve verification. Ayi does not print their complete source automatically.
The verification receives a disposable `HOME` and XDG directories without inherited credentials.
Its first argument is the absolute task path and its second is the reviewed interface path.
The generation prompt requires tests of every declared option/alias and choice, invalid inputs,
output modes, and examples using disposable fixtures. These behavior-specific assertions are
generated code: review them; schema validation alone cannot prove a separately implemented parser.
Bash tasks also pass Bash syntax and ShellCheck checks.
Changed task, verifier, manifest, or lockfile inputs cause rejection. Independently of generated assertions,
Ayi checks `--help` and `-h` for nonempty stdout, success, and every declared option spelling,
and requires a nonzero exit for an unknown option before allowing registration.

After verification passes, the caller requests approval to commit, register, and install
the command at `~/.local/bin/<name>`.
The local commit uses the Ayi checkout and contains only the command's files:

```text
tasks/<name>
verification/<name>
requirements/<name>.md
interface/<name>.json
```

Unrelated staged files stay staged. By default, `new` cannot overwrite existing commands;
deleted paths in the Git index or current commit also prevent new registration.
`modify` requires the command's files to be clean and tracked (missing requirements,
verification, or interface files may be added). It rechecks the original bytes, permissions, and Git
status before replacement. A changed command causes rejection.

`--force` on `new` allows rebuilding a clean registered tool. On `new` or `modify`, it also
allows replacing an existing file or symlink at that name in `~/.local/bin`.
It requires an explicit name and never replaces directories or other special files,
discards dirty registry edits, or skips approval and verification.
A failed commit restores the original registry files and displaced executable/link.
A successful replacement discards the displaced executable/link.
Use `--` before prompt text when it contains a standalone `--force` that should be literal.

`ayi list` shows descriptions and invocation examples from manifests, falling back to legacy
mise descriptions. It does not execute tools or invoke Pi. Mise still owns task discovery;
the installed command is immediately callable
when `~/.local/bin` is on PATH.
No push occurs. Normal Git hooks and local Git identity still govern the commit.

`explain` sends source and any saved requirements, interface, and verification to Pi, and prints
usage, dependencies, effects, and limitations without running the command. It explains
Ayi registry commands, not arbitrary executables from PATH. Explanation is model analysis,
not a claim that the tool has been tested.

Drafts remain in their session directory after rejection or cancellation.
Resume the session to review the draft, or use `/clarify` to revise the design before another generation.

Generated code runs with your operating-system permissions after approval.
A disposable home separates normal configuration. It does not provide a filesystem or network sandbox.
Interview and explanation calls disable tools, MCP, extensions, skills, and project context.
Pi generation also disables these resources except its explicit file tools.
Codex receives an instruction to return file contents without tools or code execution. Its sandbox enforces read-only filesystem access.

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
Tests use real mise, isolated homes, and bounded substitutes for Pi and Codex.
Certificate tests use a local HTTPS server and disposable certificates from OpenSSL.
They prove local control flow and data handling, not model judgment.
Live model runs need existing authentication and remain separate from automated tests.

## Source

The initial source comes from `amichne/dotfiles` at [this commit](https://github.com/amichne/dotfiles/commit/7eb9d7e0f51c930f4a3b7a65a7839cb76f0c409d).
Ayi owns the command registry, prompts, installer, and tests.
Personal shell and Pi settings remain in dotfiles.
