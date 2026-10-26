# Shared CLI components

Use `runtime: "python"` in `interface.json` for a new Python tool.
Use `output: ["text", "json"]` with that runtime.
Use `schemaVersion: 1` for existing static completion forms.
Use `schemaVersion: 2` when declaring a registered dynamic provider.
The shared runtime also supplies `--help`, `-h`, `--json`, and `--no-input`.
Do not declare these reserved options in the manifest.

Return Python imports and a `handle(invocation)` function as the `task` file content.
Do not add a shebang, launcher, parser, main guard, or `from __future__` import.
Ayi adds its launcher, mise description, and `cli.run(handle)` entry point before review.
Keep module imports free of domain effects.

```python
from cli import Code, Failure, Invocation, Result

def handle(invocation: Invocation) -> Result | Failure:
    count = invocation.integer("count")
    if count < 1:
        return Failure(Code.USAGE, "Count must be positive")
    name = invocation.text("name")
    return Result({"greeting": "Hello " + name}, "Hello " + name)
```

```json
{
  "schemaVersion": 1,
  "runtime": "python",
  "name": "greet",
  "summary": "Greet a person",
  "options": [
    {
      "names": ["--name", "-n"],
      "description": "Person to greet",
      "required": true,
      "prompt": "Person to greet",
      "value": {"name": "NAME", "completion": {"kind": "none"}}
    },
    {
      "names": ["--count"],
      "description": "Greeting count",
      "default": "1",
      "env": "GREET_COUNT",
      "value": {"name": "COUNT", "type": "integer", "completion": {"kind": "none"}}
    }
  ],
  "examples": ["greet --name Ada"],
  "output": ["text", "json"]
}
```

An option key is its first long name without `--`, or its first short name without `-`.
Keep hyphens in keys when you call the input methods.
`Invocation.text`, `integer`, and `flag` return resolved values for those input kinds.
`Invocation.many` returns a tuple for a repeated value.
A repeated flag returns an integer count through `integer`.
`Invocation.sources` maps each key to `argument`, `environment`, `config`, `default`, or `prompt`.
Use a default or mark an input required before you call `text` or `integer`.
For optional values, use `optional_text` or `optional_integer` and handle `Missing.VALUE` explicitly.
An absent optional value differs from an explicit empty string.

Value types are `text` and `integer`. The default type is `text`.
An option can declare `required`, `default`, `env`, and `prompt`.
Encode value defaults as strings. Encode flag defaults as Booleans.
Each long Boolean flag also accepts an automatic `--no-` form that sets it to false.
Do not declare that derived spelling as another option.
Repeated options accept argument values only.
Do not declare defaults, environment variables, prompts, or required status for repeated options.
Prompted choices use the same values as completion.
The runtime rejects an answer outside those choices.

Completion kinds are `none`, `choices`, `file`, and `directory` in either version.
Version 2 additionally supports `{"kind": "provider", "id": "git.branches"}`.
This provider suggests local branch names from the caller's repository.
It is a bounded, read-only hint, not validation that a branch exists at execution time.
Do not use it for integer inputs. It does not supply interactive picker choices.
No other provider IDs, executable fields, imports, URLs, or shell commands are supported.
To extend a version-1 command with this provider, propose `schemaVersion: 2`
and the provider declaration together through `ayi modify`.
Review and verify the complete changed bundle before registration.
Ordinary execution never upgrades manifests automatically.

Settings use `$XDG_CONFIG_HOME/ayi-tools/<name>.toml` or `~/.config/ayi-tools/<name>.toml`.
The file accepts only `[defaults]` and declared, nonrepeated option keys.
Arguments override declared environment variables, settings, and manifest defaults, in that order.
Unknown settings and invalid configured values fail before the domain handler.
Settings contain data only. They cannot select Python imports, shell commands, or executable expressions.

Use `cli.pick(invocation, label, {stable_id: display_label})` for domain choices.
It returns the selected ID or `Failure`.
Use `cli.confirm(invocation, question)` for a reviewed domain action.
It returns `Approved` or `Failure`.
Neither helper prompts in JSON mode, under `--no-input`, or outside an interactive terminal.
Supply an explicit argument for automation.
An approval value does not prove resource identity, freshness, or single use.
The domain owner must preserve those facts when the action needs them.

Use `ui.status` and `ui.activity` for real progress on stderr.
Use `Result(data, text)` for JSON-safe data and its human-readable form.
Use `Failure(Code.USAGE|CONFIG|DECLINED|UNAVAILABLE|IO|INTERRUPTED, message)` for expected failures.
Keep provider diagnostics and credentials out of failure messages.
The runtime emits one success value on stdout or one error value on stderr.
Both JSON envelopes include `schemaVersion: 1` and `ok`.
Success includes `data`. Failure includes `error.code` and `error.message`.

Use `cli.ask_pi(prompt, model=None, timeout=120)` for an explicit text request to Pi.
It returns `Result` or `Failure` and uses Pi's existing authentication.
It sends the prompt on stdin and disables tools, extensions, context files, and saved sessions.
It does not interpret or run the returned text.
Keep domain decisions deterministic unless the user requests model interpretation.
Other harnesses and domain effects need explicit adapters with their own finite failures and tests.

Verification receives the task path and interface path as its first two arguments.
Ayi supplies `AYI_RUNTIME_ROOT` and `AYI_INTERFACE_PATH` in the disposable environment.
Invoke the supplied executable path from the verifier.
Assert the domain result and a rejected input, plus settings and JSON behavior when used.
Do not call a live model or remote service during verification.
The runtime uses Ayi's project lock. Standalone verifiers still need their own script locks.

## Terminal input lifetime

Prefer the shared input helpers. Use a custom `PromptSession` only when the requested interaction needs it.
The locked prompt-toolkit API's `create_input()` returns an `Input` with `close()`, not a context manager.
Never write `with create_input(...)`. Use `contextlib.closing` or an explicit `try/finally` that calls `close()`.
For example, run this input block inside the domain handler after the shared invocation allows interaction:

```python
import sys
from contextlib import closing

from prompt_toolkit import PromptSession
from prompt_toolkit.history import DummyHistory
from prompt_toolkit.input.defaults import create_input
from prompt_toolkit.output.defaults import create_output

with closing(create_input(stdin=sys.stdin)) as terminal_input:
    session = PromptSession[str](
        input=terminal_input,
        output=create_output(stdout=sys.stderr),
        history=DummyHistory(),
    )
    question = session.prompt("You > ")
```

`PromptSession` restores input modes around each prompt; the enclosing block closes the input resource.
Keep EOF and Ctrl-C distinct. Let the operation's cancellation owner handle them without swallowing other errors.
If the user requires an alternate screen, restore it in `finally`, including when an adapter fails.
Do not open an alternate screen or persist input history by default.

For custom terminal interaction, verification must exercise the executable through a real PTY, not just redirected help.
Cover successful input, EOF, Ctrl-C, adapter failure when present, and the noninteractive fallback.
Capture terminal modes before spawning the child and compare them after exit using `termios.tcgetattr(master_fd)`.
Keep the PTY master open until those checks finish. On macOS, the slave descriptor can be revoked when its controlling session exits.
Do not skip terminal restoration checks or treat an ioctl failure as successful restoration.
