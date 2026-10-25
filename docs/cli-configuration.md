# Configure an Ayi tool

Ayi uses one TOML file per tool for persistent preferences.
The file supplies defaults for declared options. It does not define the tool's behavior or credentials.
Use `ayi modify <name>` for a change to the operation, interface, or model instructions.

## File structure

Use `$XDG_CONFIG_HOME/ayi-tools/<name>.toml`.
If `XDG_CONFIG_HOME` is unset or empty, use `~/.config/ayi-tools/<name>.toml`.
The configuration root must be absolute.
The separate `ayi-tools` directory keeps preferences outside Ayi's checkout link at `~/.config/ayi`.

The file has one flat `[defaults]` table:

```toml
# ~/.config/ayi-tools/brief.toml
[defaults]
style = "detailed"
model = "provider/model"
ask = false
```

Replace the model name with a model that your Pi setup supports.
The model name selects a provider model. It is not a credential.
The example keeps the model request off until `--ask` appears in the invocation.

Use the first long option name without `--` as the key.
Keep hyphens in keys. For an option with only short names, use its first name without `-`.
Use TOML strings for text, integers for numeric preferences, and Booleans for flags.
The current parser also accepts a quoted integer for an integer option.

Only declared, nonrepeated options accept persistent defaults.
Positionals and repeated options belong in the invocation.
Do not add profiles, nested command tables, aliases, includes, shell fragments, or executable expressions.
Unknown tables, unknown keys, and invalid values cause rejection before the domain handler runs.
A malformed file remains an error even when arguments override its values.

## Scope and precedence

The tool uses the same user file from every working directory.
It does not search project directories, parent directories, `.env` files, or system configuration.
No cross-tool preferences file exists.

Values resolve in this order:

1. Explicit arguments.
2. Environment variables declared in the interface manifest.
3. The user file's `[defaults]` table.
4. Defaults in the interface manifest.

For example, `brief --style short` overrides `style = "detailed"` in the file.
`BRIEF_STYLE=short` also overrides that file value because the example manifest declares `BRIEF_STYLE`.
Use the automatic `--no-<flag>` form to override a true Boolean default.
An interactive prompt resolves a remaining missing value after those layers.
JSON mode and `--no-input` reject missing required inputs instead of a prompt.

Prefer `<TOOL>_<SETTING>` for environment names.
Use uppercase letters and underscores. Convert hyphens in the tool name to underscores.
Declare each supported variable in the manifest.
Do not read arbitrary variables as implicit settings.

## Separate preferences from private state

Keep preferences under XDG config, session checkpoints under XDG state, and disposable caches under XDG cache.
Keep durable domain data under XDG data when a tool needs local storage.
Do not store these values beside generated source or inside the Git registry.

Keep credentials with Pi or the service adapter's established credential owner.
Do not store tokens in tool settings, manifests, requirements, prompts, argv, or diagnostic output.
Use provider-standard credential variables only when that adapter documents them.
Do not copy credentials into a tool-specific environment variable.

Respect `NO_COLOR` and `TERM=dumb` for presentation.
These settings do not change result data or prevent plain prompts in a usable terminal.

## Rules for future extensions

The current file has no version field. A `schemaVersion` key causes rejection.
Before an incompatible format change, add an explicit version and a migration operation.
Reject unsupported versions. Preserve the original file during migration.
Do not rewrite preferences during ordinary reads, help, or completion.

Add a project layer only for a concrete domain need and an explicit path or project opt-in.
Add a shared file only for preferences that several tools actually share.
Keep domain settings in their tool's file.
If Ayi adds a settings editor, use atomic replacement and preserve unrelated supported keys.

`Invocation.sources` exposes value provenance to domain handlers.
The runtime does not currently provide a configuration inspection command or `doctor`.
If a tool needs inspection, keep that operation offline and read-only.
Show each value's source and omit sensitive values.
Keep authentication and network diagnostics in an explicit operation.

## Source decisions

The Unix CLI skill informs the [XDG hierarchy](https://github.com/pproenca/dot-skills/blob/cf93c57cac89d6fc3e4194686000411567f5caf3/skills/.experimental/unix-cli/references/config-follow-xdg.md),
[precedence](https://github.com/pproenca/dot-skills/blob/cf93c57cac89d6fc3e4194686000411567f5caf3/skills/.experimental/unix-cli/references/config-precedence-order.md),
and [environment names](https://github.com/pproenca/dot-skills/blob/cf93c57cac89d6fc3e4194686000411567f5caf3/skills/.experimental/unix-cli/references/config-env-naming.md).
Its examples include more configuration layers and use illustrative YAML.
Ayi chooses TOML and one user layer to keep precedence explicit and independent of the working directory.

The skill also informs [secret handling](https://github.com/pproenca/dot-skills/blob/cf93c57cac89d6fc3e4194686000411567f5caf3/skills/.experimental/unix-cli/references/config-never-store-secrets.md)
and [standard environment behavior](https://github.com/pproenca/dot-skills/blob/cf93c57cac89d6fc3e4194686000411567f5caf3/skills/.experimental/unix-cli/references/config-respect-standard-vars.md).
Strict keys, the flat table, migration rules, and inspection rules are Ayi design decisions.
