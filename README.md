# welt-io

[![pypi](https://img.shields.io/pypi/v/welt-io.svg)](https://pypi.org/project/welt-io/)
[![python](https://img.shields.io/pypi/pyversions/welt-io.svg)](https://pypi.org/project/welt-io/)

Agent-side adapters for [Welt](https://github.com/iwamot/welt)'s wire contract.

## Install

```bash
uv add welt-io
```

## Usage

```python
from collections.abc import AsyncIterator

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent
from welt_io import decode_file_blocks, renderable_events

app = BedrockAgentCoreApp()


@app.entrypoint
async def invoke(payload: dict) -> AsyncIterator[dict]:
    messages = payload["messages"]
    decode_file_blocks(messages)  # base64 file bytes -> raw bytes, in place
    agent = Agent()
    # Reduce the stream to the JSON-serializable events Welt renders
    async for event in renderable_events(agent.stream_async(messages)):
        yield event


if __name__ == "__main__":
    app.run()
```

## Adapters

The wire between Welt and your agent is JSON, and plain Strands values do not fit it in either direction.

### Inbound

`decode_file_blocks(messages)` restores the base64-encoded file bytes in Welt's Converse-shaped messages (built from Slack uploads) back to the raw bytes Strands expects, in place. Without uploads it is a no-op.

### Outbound

`renderable_events(events)` reduces raw `stream_async` events — not JSON-serializable as-is — to the events Welt renders: text chunks (`data`), tool-use indicators (`current_tool_use` / `tool_result`, slimmed so text tool output stays off the wire), and generated files (`file`, a filename plus base64 bytes for each image/document/video block a tool or the model produces, which Welt uploads to the Slack thread).

## License

MIT
