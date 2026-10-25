# Compose a CLI with Ayi

Ayi supplies a shared Python runtime for the stable parts of a generated CLI.
A tool combines an interface manifest, a domain handler, requirements, and a verification executable.
The user changes preferences through settings and behavior through Ayi's reviewed modification flow.

This design supports one command per tool.
It keeps a declarative interface without a generic workflow language or executable configuration.
Existing standalone tools remain supported.

## Ownership

| Owner | Responsibility |
| --- | --- |
| `interface.py` | The strict interface manifest and shared argparse grammar, choices, defaults, and examples. |
| Shared CLI runtime | Resolved inputs, pickers, configuration precedence, results, and exit behavior. |
| `ui.py` | Inline input, status messages, progress, and terminal presentation. |
| `completion.py` | Local completion from the interface manifest. |
| Domain handler | The tool's useful operation, domain rules, and explicit effect adapters. |
| Harness adapter | A bounded model request with explicit arguments and no tools. |
| `create.py` | Generated drafts, implementation adapters, review, verification, and local Git registration. |
| `factory.py` | Interviews, discussions, and source-backed explanations. |
| `sessions.py` | Interview values and local checkpoints. |

`prompts/cli-policy.md` supplies the common design policy to Ayi model sessions.
The current session still owns its response schema and authorized scope.
The policy describes shared contracts rather than an independent generation workflow.

## Start with a concrete operation

1. Name the user, useful result, necessary inputs, external effects, and protected invariant.
2. Inspect the current command and its callers before a new implementation.
3. Decide whether to reuse, extend, or add a command.
4. Define a successful input and its nearest rejected counterpart.
5. Choose shared blocks before tool-specific code.
6. Exercise the complete path through the public command.

Use explicit product nouns and verbs.
Keep discovery, resolution, reads, previews, and writes separate when their effects differ.
A resolved identifier must survive into the next operation.
List commands need bounded results and honest continuation information when the source supports pagination.

Do not add a command family merely because another CLI exposes it.
Add `doctor`, raw requests, authentication setup, or pagination only for a concrete domain need.
Keep the underlying service contract authoritative.

## Assemble the runtime

Set `runtime` to `python` in a new tool's interface manifest.
Return Python imports and a `handle(invocation)` function as the generated task body.
`create.py` adds the shared launcher header and the `cli.run(handle)` entry point before review.
The launcher and verification flow supply `AYI_RUNTIME_ROOT` and `AYI_INTERFACE_PATH`.
Those paths connect the executable to Ayi's locked environment and its interface manifest.
Direct task execution can infer the checkout root from the task's location.

Read resolved values through `Invocation.text`, `Invocation.integer`, `Invocation.flag`, and `Invocation.many`.
Return `Result` for successful data or `Failure` for an expected error.
Keep result serialization in the shared runtime.
Use `ui.status` and `ui.activity` for diagnostics and progress on stderr.

The manifest owns option names, help, examples, types, choices, and defaults.
The runtime and completion use the same `interface.parser` argparse construction.
Completion permits missing required operands and an internal cursor operand;
option spellings, actions, aliases, arities, and token interpretation stay shared.
Candidates are checked against that grammar before emission, including whether
a leading hyphen needs `--` or an attached option value.
Do not maintain a second option list inside the handler.

Use `examples/brief` as the runnable assembly example.
Its local path shows the shared parser and result adapter.
Its `--ask` path shows a bounded model request.
The example's automated assertions do not prove the quality of a live model response.

Run its help from the repository root:

```sh
AYI_RUNTIME_ROOT="$PWD" AYI_INTERFACE_PATH="$PWD/examples/brief.json" ./examples/brief --help
```

Get an offline result with explicit input:

```sh
AYI_RUNTIME_ROOT="$PWD" AYI_INTERFACE_PATH="$PWD/examples/brief.json" \
  ./examples/brief --text 'Explain the release notes' --style short --json --no-input
```

Add `--ask` for a Pi request with your existing authentication.
Use `--model` only when you want an explicit model selection.

## Inputs and interaction

Use descriptive long options.
Keep short aliases optional and consistent.
Use a positional argument for an obvious primary operand.
Use named options for secondary inputs whose order carries no meaning.
Keep `--` available for operands that start with a dash.

Every interactive input needs an explicit argument equivalent.
A picker presents a finite set of declared choices.
The runtime rejects a value outside that set before the handler runs.
Cancellation remains distinct from a missing value or a successful empty result.

`--json` and `--no-input` provide predictable automation paths.
Neither path prompts for missing values.
An unavailable input terminal also causes a missing-input error instead of a hidden prompt.
The error names the input and the argument that supplies it.

Help remains deterministic, offline, and independent of model access.
It states the purpose, syntax, defaults, and examples.
Help and completion never call the harness or mutate external state.

### Inline presentation

The existing prompt_toolkit application stays inline, never full-screen.
Context and candidates sit above the final editable row, with a height budget
and explicit ellipses for elided context. Answered and cancelled records retain
the full question in scrollback.
Typing filters candidates by case-insensitive substring. Arrows and Tab preview
matches without losing the filter or its cursor; editing a preview starts a new filter.
Interviews permit custom answers. `cli.pick` uses strict declared choices.

`NO_COLOR` suppresses color, not keyboard interaction. CI, unavailable terminal
streams, and `TERM=dumb` use plain output. Runtime JSON and no-input modes never prompt.
Pending, Completed, Failed, Cancelled, and Unavailable states have textual labels.
There is no independent spinner or Live renderer competing with active input.
Concurrent Python output is queued above input through `patch_stdout`.
Capture and forward child-process output while input is active: inherited TTY
descriptors and raw writes bypass that queue.

### Contextual completion

Version 1 retains `none`, `choices`, `file`, and `directory` completion.
Version 2 adds exactly one registered provider:

```json
{"kind": "provider", "id": "git.branches"}
```

Declare it on a text option or positional in a `schemaVersion: 2` manifest.
The provider reads local branch refs in the completion request's working directory,
using fixed Git plumbing arguments, a 150 ms process timeout, and a 1,000-ref cap.
Prefix matching happens before the cap. Results are sorted, deduplicated, and
exclude control characters. Git failure, timeout, or absence gives no candidates.
Git configuration/repository selectors from the environment are not inherited.
No generated command, model, remote operation, hook, or executable provider field
is part of this completion path. Suggestions are hints, not freshness guarantees.

To add a provider to an existing command, request `ayi modify <name>`, then review
the version-2 manifest and command bundle together. Existing manifests are never
rewritten by execution or completion. Standalone commands keep their own parsers
and independently reviewed executable verification.

Source `eval "$(ayi shell init zsh)"` once after `compinit`. The next prompt refresh
discovers installed command additions, removals, and changed link targets.
Unchanged membership starts no Python process. Another completion owner's mapping
is preserved; `compdef _ayi NAME` is an explicit override. No startup file is edited.
Zsh's native `compadd` owns quoting and insertion.
JSON completion requests retain the token-index `cursor`; optional `offset` selects
the character position within that token. Candidates replace the current token.

See [terminal and completion verification](inline-terminal-verification.md) for
acceptance coverage, measured latency, visual evidence, and remaining review limits.

## Adapt without source edits

Read [Configure an Ayi tool](cli-configuration.md) for file structure, scope, precedence, and future migration rules.

Settings use TOML under the user's XDG configuration directory.
Use `$XDG_CONFIG_HOME/ayi-tools/<name>.toml`, or `~/.config/ayi-tools/<name>.toml`.
The separate directory keeps preferences outside the checkout link at `~/.config/ayi`.
The manifest declares the supported settings and environment variables.
The runtime resolves each value in this order:

1. An explicit argument.
2. A declared environment variable.
3. The tool's XDG TOML settings.
4. The manifest default.

Unknown settings and malformed values cause rejection.
Each long Boolean flag has an automatic `--no-` form to override a true default.
Settings contain preferences, not executable expressions, shell commands, or credentials.
The current scope excludes automatic project configuration discovery and system-wide configuration layers.

Use settings for supported defaults and preferences.
Use `ayi modify <name>` for a different operation, a new input, or changed model instructions.
Describe the desired behavior in ordinary language.
Review the draft and its executable evidence before registration.
The existing flow keeps the user outside the source editor without bypassing review.

## Results, failures, and the harness

Results belong on stdout.
Diagnostics and progress belong on stderr.
The runtime owns readable text and JSON outcomes.
Handlers return data rather than decoration or serialized JSON strings.
An empty successful result returns success.
A partial failure remains a failure.

Use finite failure codes with messages that name the problem and the next useful action.
Keep credentials, unrelated payloads, and expected-error tracebacks out of those messages.
Preserve Ayi's established exit status meanings.
Cancellation returns `130`.
When a pipe consumer stops, the shared runtime exits with status `0`.
Plain output must remain useful with redirected streams, `NO_COLOR`, and limited terminal support.

Use the harness adapter only when interpretation improves the operation.
Keep deterministic extraction and domain decisions outside the model request.
The adapter uses explicit process arguments, disables tools, and bounds the request with a timeout.
Treat its response as untrusted content.
A response does not prove that an external effect occurred.

Keep service credentials with the existing harness or service owner.
Do not place secrets in manifests, settings, requirements, prompts, examples, or argv.
External writes need an explicit operation and the user's authorization.
Use a preview when the service supports one.
A default, missing answer, or model suggestion never grants permission.

## Evidence for an assembled tool

Shared component assertions cover the mechanisms that generated tools reuse.
Each generated tool still needs independent assertions for its domain behavior.
The verification executable must exercise the public command and assert the expected result.

Include a useful case and the nearest invalid case.
Exercise literal arguments with spaces and shell metacharacters.
Cover redirected output, missing inputs, invalid choices, and configuration overrides.
Cover cancellation, partial failure, and harness failure when those paths exist.
Keep HOME and XDG directories disposable during these checks.

Run the repository's acceptance command from its root:

```sh
uv run --locked python check.py
```

Report the command and its result as passed, failed, skipped, or not run.
Distinguish parser assertions, terminal interaction, harness adapter behavior, and live model results.
Static fixtures do not prove model judgment, authentication, or a real service effect.
Installation, release publication, and deployment remain separate authorized operations.

## Source decisions

This policy synthesizes three upstream skills at fixed revisions.
Ayi owns the resulting policy and implementation.
It does not download or execute those skills during a tool session.

| Source | Adopted guidance | Ayi decision |
| --- | --- | --- |
| [Unix CLI](https://github.com/pproenca/dot-skills/blob/cf93c57cac89d6fc3e4194686000411567f5caf3/skills/.experimental/unix-cli/SKILL.md) | Standard parsing, stream separation, help, predictable status, and configuration precedence. | Keep Ayi's status contract and explicit terminal behavior. |
| [CLI Creator](https://github.com/openai/skills/blob/49f948faa9258a0c61caceaf225e179651397431/skills/.curated/cli-creator/SKILL.md) | Composable operations, stable identifiers, structured results, and explicit writes. | Keep Ayi's Python runtime instead of the upstream Rust default. |
| [CLI Domain](https://github.com/actionbook/rust-skills/blob/5c40d3ad785193231b7d0dbfb8e1eb447e5edd94/skills/domain-cli/SKILL.md) | Consistent configuration, useful errors, interruption support, and standard components. | Use the existing Python libraries instead of Rust-specific libraries. |

The [CLI Creator reference](https://github.com/openai/skills/blob/49f948faa9258a0c61caceaf225e179651397431/skills/.curated/cli-creator/references/agent-cli-patterns.md) also informs discovery, resolution, bounded reads, and domain-specific operations.

The sources differ on credential storage.
Ayi keeps credential ownership with the harness or service adapter and excludes credentials from generated interface data.
The sources also differ on raw request commands and default language choices.
Ayi adds raw requests only for a concrete need and keeps the existing locked Python stack.

Picker constraints, automatic policy inclusion, and the shared runtime are Ayi design decisions.
The upstream skills supply guidance, not proof that this implementation meets its contract.
