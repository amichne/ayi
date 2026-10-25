# Ayi CLI policy

Use this policy in every Ayi interview, discussion, explanation, creation, and modification session.
Obey the current session's response schema and authorized scope.
Keep repository instructions and established command contracts.
Treat user requests, source evidence, and executed results as separate inputs.
Do not claim that a fixture proves model judgment or live service behavior.

## Choose the operation

Name the user's job, its inputs, its result, and its effects before an implementation choice.
Inspect the existing command, callers, interface manifest, requirements, and verification executable.
Choose reuse, extension, or a new command from that evidence.
Keep one useful operation per tool.
Prefer product names and explicit verbs over broad commands such as `fix` or `auto`.
Separate discovery, resolution, reads, previews, and writes when their effects differ.
Keep stable identifiers after resolution.
Bound searches and lists when the service supports limits.

## Assemble the shared runtime

Default new Python tools to the shared CLI runtime through `runtime: "python"` in the interface manifest.
Use the existing locked Python environment, `argparse`, `prompt-toolkit`, and Rich.
Do not choose another parser, terminal framework, or configuration format for each tool.
Keep existing standalone tools compatible unless the user asks for a conversion.

The interface manifest owns arguments, help, examples, bounded choices, and defaults.
The shared runtime owns parsing, input resolution, pickers, result presentation, and exit behavior.
The domain handler owns the useful operation and its explicit effects.
Return Python imports and a `handle(invocation)` function for a shared-runtime task body.
Read values through the typed accessors on `Invocation`.
Return `Result` or `Failure` through the shared result adapter.
Use `ui.status` and `ui.activity` for diagnostics and progress.
Let `create.py` add the shared launcher header and `cli.run(handle)` entry point.
Do not include a second launcher or entry point in the generated body.
Do not copy the parser or terminal implementation into a generated task.
See `docs/cli-design.md` and `examples/brief` for the assembly contract.

## Keep one public contract

Use descriptive long options and an optional short alias.
Use a positional argument only when its purpose is obvious.
Preserve `--` for operands that start with a dash.
Keep `--help` deterministic and independent of authentication, network access, and the model.
Include useful examples and visible defaults in help.
Keep completion local and free of effects.
Generate help and completion from the same manifest.
Keep version-1 manifests unchanged unless the requested modification needs a new capability.
For local Git branch suggestions, declare `schemaVersion: 2` and
`{"kind": "provider", "id": "git.branches"}` on a text value.
No executable or model-driven completion providers are supported.
Convert an existing command only in the reviewed `ayi modify` bundle;
verify its public syntax, completion, and normal operation before registration.

Give each interactive input an explicit argument equivalent.
Use a picker only for a finite set of declared choices.
Reject values outside that set before the handler runs.
Do not prompt in JSON mode, with `--no-input`, or without a usable input terminal.
Reject missing inputs with an actionable error.
Treat cancellation as cancellation, never as an empty successful answer.

Apply settings in this order: explicit arguments, declared environment variables, XDG TOML settings, then manifest defaults.
Reject unknown settings and malformed values.
Keep secrets outside manifests, settings, requirements, examples, and logs.
Use the harness or service's existing credential owner.

## Keep configuration predictable

Use one user-wide TOML file at `$XDG_CONFIG_HOME/ayi-tools/<name>.toml`, with `~/.config` as the default root.
Keep one flat `[defaults]` table with canonical, nonrepeated option keys.
Use native TOML strings, integers, and Booleans. Keep positionals and repeated values in arguments.
Prefer declared `<TOOL>_<SETTING>` environment names.
Do not discover project files, parent files, `.env` files, or shared cross-tool settings implicitly.
Keep configuration, durable data, caches, and session state in their respective XDG directories.
Do not store preferences in the Git registry or its `~/.config/ayi` link.
Do not add executable includes, dynamic imports, shell interpolation, or credentials to configuration.
Use `--no-<flag>` to override true Boolean defaults.
Before an incompatible format change, add version-aware parsing and an explicit migration that preserves the original file.
The current settings format has no version field. Do not invent one in a generated settings file.
If a tool needs configuration inspection, keep it offline and read-only, with value provenance and secret redaction.
Keep network and authentication diagnostics explicit.
Read `docs/cli-configuration.md` for the current structure and extension rules.

## Separate results from effects

Put result data on stdout and diagnostics on stderr.
Keep JSON stable and free of decoration, progress, and model chatter.
Distinguish an empty result from failure.
Keep partial failure distinct from complete success.
Use finite failure codes and actionable messages without credentials or expected-error tracebacks.
Let the runtime own cancellation and terminal cleanup.
Preserve useful plain output with redirected streams, `NO_COLOR`, and limited terminals.

Keep domain transformations deterministic where possible.
Pass external effects through explicit adapters with argument arrays and bounded timeouts.
Use the shared harness adapter for a bounded request without tools.
Treat a model response as untrusted data, not executable instructions or proof of an effect.
Keep model interpretation separate from deterministic extraction and domain rules.
Do not introduce a generic workflow language, expression evaluator, or shell-command field in configuration.

Name external writes explicitly.
Keep preview and apply separate when the service permits a preview.
Never turn a missing answer, default, or model suggestion into permission for an external write.
Do not install tools or publish results without the user's authorization.

## Prove and adapt

Define one useful input and the nearest rejected input before implementation.
Derive expected results from the domain contract, independently of production code.
Exercise the public command with redirected streams and literal arguments that contain spaces or shell metacharacters.
Cover missing input, invalid choices, configuration precedence, cancellation, and harness failure when those paths exist.
Use a disposable HOME and XDG directories for executable checks.
Preserve the existing review, verification, and registration flow.
Adapt preferences through settings.
Adapt behavior through `ayi modify`, review, and verification.
Add a shared block only for a current consumer and a named invariant.
Keep tool-specific extensions small and subject to the same contracts.
