"""Serve a Strands Harness agent through Welt's wire contract."""

import os
import tempfile
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent, tool
from strands.models import BedrockModel
from strands_harness import create_harness

from welt_io_strands import renderable_events, start_reply

app = BedrockAgentCoreApp()

# AgentCore Runtime's writable directory also holds files created by the tools.
os.chdir(tempfile.gettempdir())

model = BedrockModel(
    model_id=os.environ.get("MODEL_ID") or "global.anthropic.claude-sonnet-4-6",
    region_name=os.environ.get("BEDROCK_REGION") or None,
)


@tool
def current_time() -> str:
    """Get the current UTC date and time in ISO 8601 format."""
    return datetime.now(UTC).isoformat()


def new_agent() -> Agent:
    """Build a fresh turn using the conversation history supplied by Welt."""
    return create_harness(
        model=model,
        tools=[current_time],
        builtin_tools=["shell", "read", "write", "edit", "web_fetch"],
        session=False,
        memory=False,
        skills=False,
        background_tasks=False,
        interventions="ask",
        caching=False,
        callback_handler=None,
    )


# Approval answers must resume the Agent that raised the interrupt. The map
# lasts for this process; a recycled Runtime session loses pending approvals.
_interrupted: dict[str, Agent] = {}


def _resumed(answers: Mapping[str, object]) -> Agent:
    """Take the held Agent named by these interrupt answers."""
    held_id = next((i for i in answers if i in _interrupted), None)
    if held_id is None:
        raise RuntimeError("No interrupted agent to resume in this session.")
    agent = _interrupted[held_id]
    for interrupt_id in [i for i, held in _interrupted.items() if held is agent]:
        del _interrupted[interrupt_id]
    return agent


@app.entrypoint
async def invoke(payload: dict) -> AsyncIterator[dict]:
    """Stream a Harness reply or resume a pending tool approval."""
    agent = (
        _resumed(payload["interrupt_responses"])
        if "interrupt_responses" in payload
        else new_agent()
    )
    async for event in renderable_events(start_reply(agent, payload), agent=agent):
        interrupt = event.get("interrupt")
        if interrupt is not None:
            _interrupted[interrupt["id"]] = agent
        yield event


if __name__ == "__main__":
    app.run()
