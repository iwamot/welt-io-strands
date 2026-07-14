# welt-io

[![pypi](https://img.shields.io/pypi/v/welt-io.svg)](https://pypi.org/project/welt-io/)
[![python](https://img.shields.io/pypi/pyversions/welt-io.svg)](https://pypi.org/project/welt-io/)

The [Strands Agents](https://strandsagents.com/) (Python) adapter for [Welt](https://github.com/iwamot/welt)'s wire contract — one of Welt's [agent-side adapters](https://github.com/iwamot/welt#agent-side-adapters).

## Install

```bash
uv add welt-io
```

## Usage

See [`examples/agent`](examples/agent) — the smallest complete agent built on this package (text streaming, image generation, file input, and a human-approval tool), which doubles as the example for [Welt's Quick Start](https://github.com/iwamot/welt#quick-start). The sections below explain the adapters it wires in.

## API

The wire between Welt and the agent is JSON, specified by [Welt's wire contract](https://github.com/iwamot/welt/blob/main/docs/wire.md) — plain Strands values do not fit it in either direction. Two functions adapt the inbound payload, three the outbound stream.

### Inbound

#### `decode_file_blocks(messages)`

Restores the base64-encoded file bytes in Welt's Converse-shaped messages back to the raw bytes Strands expects, in place. Without Slack uploads it is a no-op.

#### `decode_interrupt_responses(responses)`

Turns Welt's resume payload — a mapping of interrupt id to the answer a human chose — into the `interruptResponse` items that `Agent.stream_async` resumes from.

### Outbound

#### `renderable_events(events)`

Reduces raw `stream_async` events — not JSON-serializable as-is — to the events Welt renders:

| Strands emits | On the wire | In the Slack thread |
|---|---|---|
| Text deltas | `data` | The streamed reply |
| Tool invocations and results | `current_tool_use` / `tool_result` | "Using tool" indicators (tool output stays off the wire) |
| Image / document / video blocks a tool or the model produces | `file` | An uploaded file ([size limits](https://github.com/iwamot/welt/blob/main/docs/wire.md#limits)) |
| Pending [interrupts](https://strandsagents.com/docs/user-guide/concepts/interrupts/) | `interrupt` | Buttons and/or a text field |

A run that stops for human input ends its stream with one `interrupt` event per pending interrupt; agents that do not use interrupts see no change.

#### `file_event(name, data)`

Builds the same `file` event from a filename and raw bytes, for attaching arbitrary files of your own:

```python
yield file_event("report.csv", csv_bytes)
```

Tool-generated files need no code at all — for example, strands-tools' [`generate_image`](https://github.com/strands-agents/tools/blob/main/src/strands_tools/generate_image.py) returns the image as a tool-result block, which streams into the thread by itself. The [example agent](examples/agent) includes it.

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
        input={"label": "Or tell me what to do instead"},
    ),
)
```

## Working with interrupts

[Welt's Interrupts doc](https://github.com/iwamot/welt/blob/main/docs/interrupts.md) covers the Slack side: how each reason renders, who can answer, multiple questions, and expiry. On the Strands side:

- **Prefix your interrupt names** (`myapp-deploy-approval`). Hook-raised interrupts must be unique across the whole event, tool-raised ones within their tool — a prefix keeps both as the agent grows.
- **Strands' ready-made [`HumanInTheLoop`](https://strandsagents.com/docs/user-guide/concepts/agents/interventions/human-in-the-loop/) intervention works over Welt as-is.** Its string reasons render with Welt's default **Approve** / **Deny** buttons, whose `y` / `n` values its default evaluator understands. Do not pass `ask`: stdio prompts and callback evaluators have no terminal on AgentCore Runtime.
- **Route stdio consent prompts through interrupts instead.** For strands-tools packages that gate themselves behind a stdio prompt, set `BYPASS_TOOL_CONSENT=true` and let `HumanInTheLoop` do the gating over Slack. The strands-tools `handoff_to_user` tool is likewise stdio-bound; a small interrupt-raising tool of your own is the replacement.

## Supported Versions

Welt releases first; welt-io follows, mirroring the minor version. While both are 0.x, a welt-io 0.Y release supports Welt v0.Y — other combinations may work, but come with no guarantee.

## License

MIT
