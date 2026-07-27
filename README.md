# welt-io-strands

[![pypi](https://img.shields.io/pypi/v/welt-io-strands.svg)](https://pypi.org/project/welt-io-strands/)
[![python](https://img.shields.io/pypi/pyversions/welt-io-strands.svg)](https://pypi.org/project/welt-io-strands/)

The [Strands Agents](https://strandsagents.com/) (Python) adapter for [Welt](https://github.com/iwamot/welt)'s wire contract.

## Install

```bash
uv add welt-io-strands
```

## Usage

See [`examples/agent`](examples/agent) — the smallest complete agent built on this package (text streaming, tool use, image generation, file output, file input, and human-approval tools), which doubles as the example for [Welt's Quick Start](https://github.com/iwamot/welt#quick-start). The sections below explain the adapters it wires in.

## API

The wire between Welt and the agent is JSON, specified by [Welt's wire contract](https://github.com/iwamot/welt/blob/main/docs/wire.md) — plain Strands values do not fit it in either direction. Two functions adapt the inbound payload, three the outbound stream.

### Inbound

#### `decode_messages(messages)`

Returns a copy of Welt's Converse-shaped messages with the base64-encoded file bytes restored to the raw bytes Strands expects; the input is left untouched. Its in-place predecessor, `decode_file_blocks(messages)`, is deprecated.

#### `decode_interrupt_responses(responses)`

Turns Welt's resume payload — a mapping of interrupt id to the answer a human chose — into the `interruptResponse` items that `Agent.stream_async` resumes from.

#### Payloads that violate the contract

Both functions reject a payload the [wire contract](https://github.com/iwamot/welt/blob/main/docs/wire.md#malformed-payloads) does not describe — an unknown role, a block missing its bytes, base64 that was never valid — with a `TypeError` where a value is of the wrong type and a `ValueError` where it is the right type but unusable. Welt does not send those, so a raise means the caller is not Welt or Welt has a bug; either way, decoding what is left would hand the agent a conversation with a turn missing.

The format token is the exception: it is checked for presence, not against a list of known tokens, and travels on to Bedrock unchanged — the side that knows which ones it takes.

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

#### `file_event(name, data)`

Builds the same `file` event from a filename and raw bytes, for the files the host app attaches itself:

```python
yield file_event("report.csv", csv_bytes)
```

Tools have no use for it — they hand files to the agent as content blocks, and `files_from` decides which of those reach the thread.

#### `interrupt_reason(message, options=..., input=...)`

Builds the structured reason Welt renders as a message with the specified widgets — choice buttons (`options`), a free-text field (`input`), or both. The specs are [the wire's own shapes](https://github.com/iwamot/welt/blob/main/docs/wire.md#interrupt); omitted fields keep Welt's defaults, and a typo becomes an immediate `ValueError` instead of a silent fallback to Welt's default rendering:

```python
answer = tool_context.interrupt(
    "deploy-approval",
    reason=interrupt_reason(
        "Deploy to prod?",
        [
            {"value": "y", "label": "Deploy", "style": "primary"},
            {"value": "n", "label": "Cancel"},
        ],
        input={"label": "Or type your answer"},
    ),
)
```

## Working with interrupts

[Welt's Interrupts doc](https://github.com/iwamot/welt/blob/main/docs/interrupts.md) covers the Slack side: how each reason renders, who can answer, multiple questions, and expiry. On the Strands side:

- **Prefix your interrupt names** (`myapp-deploy-approval`). Hook-raised interrupts must be unique across the whole event, tool-raised ones within their tool — a prefix keeps both as the agent grows.
- **Strands' ready-made [`HumanInTheLoop`](https://strandsagents.com/docs/user-guide/concepts/agents/interventions/human-in-the-loop/) intervention works over Welt as-is.** Its string reasons render with Welt's default **Approve** / **Deny** buttons, whose `y` / `n` values its default evaluator understands. Do not pass `ask`: both of its inline modes block the agent waiting for input that Slack can never deliver — the default interrupt/resume mode is the one Welt drives.
- **Route stdio consent prompts through interrupts instead.** For strands-tools packages that gate themselves behind a stdio prompt, set `BYPASS_TOOL_CONSENT=true` and let `HumanInTheLoop` do the gating over Slack. The strands-tools `handoff_to_user` tool is likewise stdio-bound; a small interrupt-raising tool of your own is the replacement.
- **Code before `interrupt` runs again on resume.** Strands re-executes the interrupted tool from its start, so whatever precedes an interrupt and must not run twice — side effects, or work that must match what the human approved — has to be skipped on the second pass. Memoizing on `tool_context.tool_use["toolUseId"]`, the same id on both passes, is enough: the cache lives in the same process as the interrupt state it pairs with. The [example agent](examples/agent)'s `sample_draft_report` shows the pattern.

## Supported Versions

Welt releases first; welt-io-strands follows, mirroring the minor version. While both are 0.x, a welt-io-strands 0.Y release supports Welt v0.Y — other combinations may work, but come with no guarantee.

## License

MIT
