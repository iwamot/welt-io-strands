# welt-io-strands

[![pypi](https://img.shields.io/pypi/v/welt-io-strands.svg)](https://pypi.org/project/welt-io-strands/)
[![python](https://img.shields.io/pypi/pyversions/welt-io-strands.svg)](https://pypi.org/project/welt-io-strands/)
[![strands-agents](https://img.shields.io/badge/dynamic/regex?url=https%3A%2F%2Fpypi.org%2Fpypi%2Fwelt-io-strands%2Fjson&search=strands-agents%28%3E%3D%5B%5Cd.%5D%2B%29&replace=%241&label=strands-agents)](https://pypi.org/project/strands-agents/)

The [Strands Agents](https://strandsagents.com/) (Python) adapter for [Welt](https://github.com/iwamot/welt)'s wire contract.

## Install

```bash
uv add welt-io-strands
```

## Usage

See [`examples/agent`](examples/agent) — the smallest complete agent built on this package (text streaming, tool use, image generation, file output, file input, and human-approval tools), which doubles as the example for [Welt's Quick Start](https://github.com/iwamot/welt#quick-start). The sections below explain the adapters it wires in.

## Supported Versions

### Welt

While both are 0.x, a welt-io-strands 0.Y release supports Welt v0.Y. From 1.0 on, a release supports any Welt release that shares its major version, and the minor versions move independently. Support is best effort either way, and other combinations come with no guarantee.

### Strands Agents

The badge at the top states the range this release installs against. Every push and pull request runs the suite at both ends of it: the declared floor, and the newest release CI has picked up. That is best effort rather than a guarantee — the floor is where the suite was last seen to pass, so a later release may raise it, and no ceiling is declared at all.

The badge follows the current release. For the range an older release declared, read that release's own metadata on PyPI.

Something misbehaving inside that range is worth an [issue](https://github.com/iwamot/welt-io-strands/issues).

## API

The wire between Welt and the agent is JSON, specified by [Welt's wire contract](https://github.com/iwamot/welt/blob/main/docs/wire.md) — plain Strands values do not fit it in either direction. Two functions adapt the inbound payload, two the outbound stream.

### Inbound

#### `decode_messages(messages)`

Returns a copy of Welt's Converse-shaped messages with the base64-encoded file bytes restored to the raw bytes Strands expects; everything else — the format token included — is carried over untouched, and the input is left alone.

#### `decode_interrupt_responses(responses)`

Turns Welt's resume payload — a mapping of interrupt id to the answer a human chose and the widget it came from — into the `interruptResponse` items that `Agent.stream_async` resumes from. The answer travels on as the value it was given; the widget it came from is Welt's vocabulary, and a tool that reads its own option values already knows which of them it declared.

#### What arrives is taken as correct

Welt builds the payload and checks its own output against the wire contract before releasing it, so these two functions do no field validation of their own. A payload that departs from the contract is a bug on the sending side rather than an input to guard against, and it surfaces as an ordinary error from whatever touches it first — a `KeyError`, a `TypeError`, or `binascii.Error` from bytes that are not base64.

The one thing `decode_messages` refuses outright is a content block of a kind Welt never sends. A `messages` turn carries only `text`, `image`, `document`, and `video` blocks; a `toolUse` or `toolResult` block is not a malformed one of those but a forged conversation turn, and rebuilt into history it would let a caller that is not Welt put words the model treats as its own past tool calls and their results into the run. It raises `ValueError`. This is a trust-boundary check, not the field validation the contract otherwise saves you from.

### Outbound

#### `renderable_events(events, agent=..., files_from=...)`

Reduces raw `stream_async` events — not JSON-serializable as-is — to the events Welt renders:

| Strands emits | On the wire | In the Slack thread |
|---|---|---|
| Text deltas | `data` | The streamed reply |
| Tool invocations and results | `current_tool_use` / `tool_result` | "Using tool" indicators (tool output stays off the wire) |
| Image / document / video blocks the model produces, or a tool named in `files_from` returns | `file` | An uploaded file ([size limits](https://github.com/iwamot/welt/blob/main/docs/wire.md#limits)) |
| Pending [interrupts](https://strandsagents.com/docs/user-guide/concepts/interrupts/) | `interrupt` | Buttons and/or a text field |

A run that stops for human input ends its stream with one `interrupt` event per pending interrupt; agents that do not use interrupts see no change.

A tool hands files to the model for either of two reasons — to have it read them, or to give them to the human — and only the agent knows which is which, so name the tools whose files belong in the thread:

```python
async for event in renderable_events(
    stream, agent=agent, files_from={"generate_image"}
):
```

A tool left out keeps its files to the model: strands-tools' [`file_read`](https://github.com/strands-agents/tools/blob/main/src/strands_tools/file_read.py) reading a PDF does not drop it into the thread as a side effect. A tool named there needs no code of its own — strands-tools' [`generate_image`](https://github.com/strands-agents/tools/blob/main/src/strands_tools/generate_image.py) returns the image as a tool-result block, and naming it is all it takes; a tool of your own returns image, document, or video blocks the same way. The `agent` is what makes the names resolvable: its messages hold the tool behind each result, the only place that survives a resume, where the stream carries the result alone.

Uploaded names come from the block — a document's own `name` plus its format, the block's kind for the rest (`image.png`). That name is the model's handle on the document as much as a filename, and Converse rejects a request whose messages carry two documents under one name, so a tool that returns documents has to keep their names apart across the run: strands-tools' `file_read` appends a short uuid to each.

Each event carries only what Welt reads. A `current_tool_use` is cut down to the name and id behind the indicator, so the tool's arguments — which Strands re-sends in full on every input delta — stay off the wire, and an event with nothing to render (a text chunk the model left empty, a file with no bytes) is not sent at all.

#### `interrupt_reason(message, options=..., approve=..., reject=..., input=...)`

Builds the structured reason Welt renders as a message with the specified widgets — the approve and reject buttons Welt words and values itself (`approve`, `reject`), choice buttons of your own (`options`), a free-text field (`input`), or any combination. `approve` and `reject` answer with `True` and `False`, so a question whose decision is approval asks for them by name instead of inventing values; `{}` takes Welt's wording, and a `label` or `style` overrides it. An option's `value` is any JSON value, and the pressed button answers with it as it was declared. With no widget at all the message renders as itself and Welt's default buttons answer it. The specs are [the wire's own shapes](https://github.com/iwamot/welt/blob/main/docs/wire.md#interrupt), typed as `DecisionSpec`, `OptionSpec`, and `InputSpec`, and omitted fields keep Welt's defaults:

```python
answer = tool_context.interrupt(
    "deploy-approval",
    reason=interrupt_reason(
        "Deploy to prod?",
        approve={"label": "Deploy"},
        reject={"label": "Cancel"},
        input={"label": "Or type your answer"},
    ),
)
```

Building the reason through this helper is what makes a typo an error. `ToolContext.interrupt` takes its `reason` as `Any`, so a dict literal handed to it directly is checked by nothing, and Welt's reaction to a reason it cannot match is its default buttons — no error, no log, just widgets you did not ask for. The typed parameters catch a misspelled key before the run; the checks inside catch it in runs where no type checker was involved. What they check is the shape, not the size: how many buttons one Slack block holds, and how long a button value may be, are Welt's to enforce.

## Working with interrupts

[Welt's Interrupts doc](https://github.com/iwamot/welt/blob/main/docs/interrupts.md) covers the Slack side: how each reason renders, who can answer, multiple questions, and expiry. On the Strands side:

- **Prefix your interrupt names** (`myapp-deploy-approval`). Hook-raised interrupts must be unique across the whole event, tool-raised ones within their tool — a prefix keeps both as the agent grows.
- **Gate your own tool with `interrupt()`, and everything else with [steering](https://strandsagents.com/docs/user-guide/concepts/agents/steering/).** A tool you wrote can ask for itself; a tool you did not — from strands-tools, or an MCP server — is gated from outside by a `SteeringHandler` returning `Interrupt`, which also puts the decision for every tool in one place. A handler cannot declare buttons, so its questions get Welt's default buttons and it reads the boolean they answer with. The [example agent](examples/agent) gates `generate_image` this way.
- **Strands' ready-made [`HumanInTheLoop`](https://strandsagents.com/docs/user-guide/concepts/agents/interventions/human-in-the-loop/) intervention works over Welt as-is.** Its string reasons render with Welt's default buttons, and its default evaluator reads the `true` they answer with as approval. Do not pass `ask`: both of its inline modes block the agent waiting for input that Slack can never deliver — the default interrupt/resume mode is the one Welt drives.
- **Route stdio consent prompts through interrupts instead.** For strands-tools packages that gate themselves behind a stdio prompt, set `BYPASS_TOOL_CONSENT=true` and let `HumanInTheLoop` do the gating over Slack. The strands-tools `handoff_to_user` tool is likewise stdio-bound; a small interrupt-raising tool of your own is the replacement.
- **Code before `interrupt` runs again on resume.** Strands re-executes the interrupted tool from its start, so whatever precedes an interrupt and must not run twice — side effects, or work that must match what the human approved — has to be skipped on the second pass. Memoizing on `tool_context.tool_use["toolUseId"]`, the same id on both passes, is enough: the cache lives in the same process as the interrupt state it pairs with. The [example agent](examples/agent)'s `sample_draft_report` shows the pattern.

## License

MIT
