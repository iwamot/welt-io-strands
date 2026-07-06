# welt-io

[![pypi](https://img.shields.io/pypi/v/welt-io.svg)](https://pypi.org/project/welt-io/)
[![python](https://img.shields.io/pypi/pyversions/welt-io.svg)](https://pypi.org/project/welt-io/)

Agent-side adapters for [Welt](https://github.com/iwamot/welt)'s wire contract.

The wire between Welt and your agent is JSON, and plain Strands values do not fit it in either direction:

- **Inbound**, JSON cannot carry raw bytes, so Welt base64-encodes the `bytes` slot of the Converse image/document/video blocks it builds from Slack uploads. `decode_file_blocks` restores them before Strands (Bedrock Converse) sees the messages; without uploads it is a no-op.
- **Outbound**, raw `stream_async` events carry values that are not JSON-serializable (the Agent itself, UUIDs, traces), which the AgentCore Runtime SDK would degrade to a plain string on the SSE wire. `renderable_events` reduces the stream to the events Welt renders: text chunks (`data`), tool-use starts (`current_tool_use`), and tool completions (`tool_result`, slimmed to the toolUseId and status so tool output stays off the wire).

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

## License

MIT
