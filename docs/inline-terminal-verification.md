# Inline terminal and completion verification

This report covers the local implementation based on
[`915279f3`](https://github.com/amichne/ayi/commit/915279f3fd50753ce847498ad832d6b16740fdcf).
It separates executable checks, reference-machine measurements, and visual review.
Automated tests use disposable homes without host credentials or a live model.
Local installation and PR publication are separate user-authorized delivery steps.
No shell startup-file edit is needed.

## Decisions

- Keep prompt_toolkit inline rendering and Rich formatting. A single output
  lifecycle queues Python stream output above the input. Replace the independent
  spinner with textual operation events rather than introduce another renderer.
- Move the existing argparse construction to `interface.py`. Execution and
  incomplete-line parsing share spellings, actions, and arities. Keep the
  `cli.parser` import compatible. Projected candidates also pass through that
  grammar: hyphenated operands require a valid context, but negative numbers
  remain available when argparse accepts them. Standalone tasks keep their native parsers.
- Version provider declarations explicitly: version 1 rejects providers;
  version 2 permits only `git.branches`. Conversion uses the existing reviewed
  modification flow, without an automatic migration or provider plugin system.
- Keep Git effects in `completion.py`: one registered provider does not need
  a separate module. Fixed local plumbing, timeout, result cap, and finite
  unavailable outcomes bound its authority. Completion never runs a task or model.
- Keep native Zsh insertion and prompt-time registration. Preserve other
  completion owners and prior registrations when the backend is unavailable.
- Keep `create.py` and `factory.py` transaction ownership unchanged. Existing
  validation consumes the extended schema; tests extend its reviewed bundles,
  failure injection, modification, and recovery coverage to version 2.

## Executed checks

`uv run --locked python check.py` passed on October 25, 2026: **300 tests passed**,
Ruff formatting/lint, strict Pyright, ShellCheck, Bash syntax, and Zsh syntax.
The focused completion/runtime selection passed 87 tests after the final
candidate-syntax regression was added. UI-specific evidence records 16 new
assertions failing on baseline and passing after the UI changes, plus all 45 UI cases.

## Acceptance coverage

All test paths below are relative to `tests/`. “Automated” means the named
observable behavior is checked, not that every possible terminal or command is proven.

| Criterion | Evidence and scope |
| --- | --- |
| AC-01 | Automated: `test_ui.py` rejects alternate-screen entry, whole-screen clearing, and terminal reset; checks retained history and collapsed candidate regions. |
| AC-02 | Automated: real PTYs and pyte grids cover widths 28/40/80/120 × heights 6/12/24, with the cursor on the final rendered input row. |
| AC-03 | Automated: filtering, query/cursor preservation, native editing, both navigation directions, custom/strict answers, cancellation, and shrink/grow resizing. |
| AC-04 | Automated: monochrome SGR assertions, ASCII encoding, CI/dumb modes, stream redirection, and runtime no-input/JSON contracts in `test_ui.py`, `test_cli.py`, and `test_cli_blocks.py`. |
| AC-05 | Automated: concurrent stdout/stderr, forwarded subprocess output, status/activity, SIGINT/default SIGTERM, cursor visibility, and restored termios. Raw inherited TTY writes remain outside the queue. |
| AC-06 | Automated: `test_completion.py` compares independent candidate expectations with the shared parser, including aliases, attached values, negative flags, repeated operands, invalid prefixes, and token offsets. |
| AC-07 | Automated: real interactive Zsh Tab insertion and an argument sink verify spaces, quotes, dollars, parentheses, globs, Unicode, hidden files, and leading hyphens after `--`; command-substitution-looking names stay literal. |
| AC-08 | Automated: isolated Git repositories, nested cwd, remote-ref exclusion, prefix matches beyond the 1,000-result cap, controlled argv/environment traces, and no task/model execution. |
| AC-09 | Automated: schema and draft registration reject unknown versions/kinds/IDs, unversioned providers, and executable fields. |
| AC-10 | Automated: real Zsh next-prompt registration, removal, retargeting, and reinstallation; repeated initialization has one hook and preserves another completion owner. |
| AC-11 | Automated: Git exit/timeout/encoding/missing-binary failures; a broken manifest leaves the actual Zsh buffer and following commands usable without task execution. Backend failure preserves prior mappings. |
| AC-12 | Automated: shared executable parser/help/config/JSON/errors/status contracts; both Pi and Codex substitutes create reviewed version-1/version-2 tools. `cli.pick` rejects a custom answer and permits correction in a real PTY. |
| AC-13 | Automated: `test_create.py`, `test_factory.py`, and `test_bootstrap.py` cover review/verification rejection, script locks, mutated inputs, registry write/interruption/staging/commit/link failures, update preservation, and unrelated Git indexes/staged work. |
| AC-14 | Automated: version-1 shared and standalone fixtures remain valid; both versions register and execute; modification explicitly upgrades to version 2; shared tools and locked standalone tasks survive fresh clones. |
| AC-15 | Reference measurement passes the stated budgets without concurrent Ayi tests; results and the separate contended run are below. Unchanged refresh starts exactly zero backends. |
| AC-16 | Reviewable PTY captures and agent inspection completed. **Explicit human approval and Terminal/iTerm2-specific review remain outstanding.** |

The verification executable still owns domain assertions for a standalone task.
Ayi independently probes its help, declared option documentation, unknown-option
rejection, and immutable reviewed inputs. This is not universal equivalence
between an arbitrary standalone parser and its manifest.

## Latency measurement

Reference environment: Apple M1 Max, macOS 26.5.2 arm64, Python 3.12.14,
Apple Git 2.50.1, Zsh 5.9. The baseline and changed checkouts use identical
100-choice and 300-local-branch fixtures, one registered command, and
fixture-installed launcher links.
Baseline has no dynamic provider, so its Git result is not applicable.

The harness times a real Zsh completion widget through `_main_complete`, `_ayi`,
the installed launcher, Python startup, serialization, and native `compadd`.
It asserts 100 native matches on every observation and 46 backend invocations
per completion phase.
Each operation has one first-call observation, five warmups, then 40 observations.
The monotonic clock is `perf_counter_ns`; p95 uses nearest rank. Refresh includes
the shell/PTY round trip, making it a conservative upper bound.
“First” is process-cold, not a flushed operating-system filesystem cache.

| Operation | Baseline first / warm p95 | Changed first / warm p95 | Warm p95 budget |
| --- | --- | --- | --- |
| Static completion | 119.33 / 127.47 ms | 123.28 / 130.06 ms | ≤ 200 ms |
| Local Git branches | Not supported | 161.33 / 362.54 ms | ≤ 400 ms |
| Unchanged prompt refresh | 0.40 / 0.50 ms | 5.32 / 4.86 ms | ≤ 10 ms |
| Unchanged refresh backend launches | 0 | 0 | Exactly 0 |

These measurements establish budget compliance in this run, **not a performance
improvement**. An earlier run before the final candidate filter, alongside the
test suite, measured changed p95 220.20 ms static, 403.86 ms Git, and 4.31 ms refresh;
its raw samples are retained, not treated as final-revision budget evidence.
One stricter benchmark attempt aborted on a candidate-count mismatch before
diagnostic context was recorded. A subsequent diagnostic run checked every
observation and passed. The earlier anomaly's cause remains unconfirmed; no
samples from that aborted run are included in the final numbers.
Latency is not a shared-CI threshold. CI tests enforce deterministic provider
bounds and local-only invocation instead. Fixed Git ref plumbing has no network
operation; no packet-level network profiling was performed.

Local review artifacts live under the excluded `.amp/in/artifacts/` directory:

- `completion-latency.json` and `completion-latency-contended.json`: raw samples.
- `measure_completion.py`: executable reproduction recipe; run with
  `uv run --locked python .amp/in/artifacts/measure_completion.py`.
- `check.txt`: the full repository check transcript.
- `verification-manifest.json`: source and evidence hashes for the local worktree.
- `README.md`, `review/manifest.json`, and `visual-contact-sheet.png`: visual
  provenance, inspection notes, and before/after presentation reference.
- `after/` and `review/`: individual ANSI captures, terminal grids, SVGs, and PNGs.

## Visual and live-behavior limits

The 59 captured states come from real PTYs. Images reconstruct captured cells
using pyte/Rich; they are not macOS Terminal or iTerm2 screenshots.
They include interviews, filtering, long questions/candidates, a narrow terminal,
monochrome, operation events, verification success/failure, source/diff review,
discovery, and actual `examples/brief --help` output.
The input stays below the active context; unused terminal rows can remain below
an inline application. Completed interaction records preserve normal scrollback.

Generation and verification presentation captures exercise the UI lifecycle,
not model judgment. Pi/Codex substitutes verify orchestration and registration,
not authenticated live-model behavior. No live model was invoked.
Concurrent children must have output captured and forwarded through Python
streams. Raw descriptors and cached pre-interaction stream objects bypass
`patch_stdout`. SIGKILL cannot restore terminal modes; custom/ignored SIGTERM
handlers are preserved rather than replaced.
