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

The wire between Welt and the agent is JSON, and its contract is defined by [Welt's docs](https://github.com/iwamot/welt#features) — plain Strands values do not fit it in either direction. Each function below adapts one piece:

### Inbound

`decode_file_blocks(messages)` restores the base64-encoded file bytes in Welt's Converse-shaped messages (built from Slack uploads) back to the raw bytes Strands expects, in place. Without uploads it is a no-op.

`decode_interrupt_responses(responses)` turns Welt's resume payload — a plain mapping of interrupt id to the answer a human chose — into the `interruptResponse` items that `Agent.stream_async` resumes from.

### Outbound

`renderable_events(events)` reduces raw `stream_async` events — not JSON-serializable as-is — to the events Welt renders: text chunks (`data`), tool-use indicators (`current_tool_use` / `tool_result`, slimmed so text tool output stays off the wire), and generated files (`file`, a filename plus base64 bytes for each image/document/video block a tool or the model produces, which Welt uploads to the Slack thread — see [Welt's Files doc](https://github.com/iwamot/welt/blob/main/docs/files.md) for size limits and rendering). A stream that stops for human input ([interrupts](https://strandsagents.com/docs/user-guide/concepts/interrupts/)) ends with one `interrupt` event per pending interrupt, which Welt renders as buttons in the Slack thread; agents that do not use interrupts see no change.

`file_event(name, data)` builds the same `file` event from a filename and raw bytes, for attaching arbitrary files of your own:

```python
yield file_event("report.csv", csv_bytes)
```

Tool-generated files need no code at all — for example, strands-tools' [`generate_image`](https://github.com/strands-agents/tools/blob/main/src/strands_tools/generate_image.py) returns the image as a tool-result block, which streams into the thread by itself. The [example agent](examples/agent) includes it.

`interrupt_reason(message, options=..., input=...)` builds the reason shape Welt renders as a message with the specified widgets, both specs being the wire's own shapes: buttons (`options`, one dict per button — a required `value`, an optional `label`, an optional `style` of `"primary"` or `"danger"`), a free-text field whose submitted text becomes the response (`input` — an optional `label` and `multiline`), or both — whichever answer comes first settles the question. Omitted fields keep Welt's defaults; building the shape through this helper turns a typo into an immediate `ValueError` instead of a silent fallback to Welt's default rendering:

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

[Welt's Interrupts doc](https://github.com/iwamot/welt/blob/main/docs/interrupts.md) covers the whole round trip: the reason contract, who can press, multiple interrupts, and expiry. Prefix your interrupt names (`myapp-deploy-approval`): hook-raised interrupts must be unique across the whole event, tool-raised ones within their tool, and a prefix keeps both as the agent grows.

Strands' ready-made [`HumanInTheLoop`](https://strandsagents.com/docs/user-guide/concepts/agents/interventions/human-in-the-loop/) intervention works over Welt as-is — its string reasons render with Welt's default **Approve** / **Deny** buttons, whose `y` / `n` values its default evaluator understands. Do not pass `ask`: stdio prompts and callback evaluators have no terminal on AgentCore Runtime. For strands-tools packages that gate themselves behind a stdio consent prompt, set `BYPASS_TOOL_CONSENT=true` and let `HumanInTheLoop` do the gating over Slack instead; the strands-tools `handoff_to_user` tool is likewise stdio-bound, and a small interrupt-raising tool of your own is the replacement.

## Supported Versions

Welt releases first; welt-io follows, mirroring the minor version. While both are 0.x, a welt-io 0.Y release supports Welt v0.Y — other combinations may work, but come with no guarantee.

## License

MIT
