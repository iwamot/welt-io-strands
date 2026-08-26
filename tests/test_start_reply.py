import asyncio
import base64
from collections.abc import AsyncIterator

import pytest
from strands.agent import AgentResult
from strands.interrupt import Interrupt
from strands.telemetry import EventLoopMetrics

from welt_io_strands import renderable_events, start_reply

PNG_BYTES = b"\x89PNG\r\n\x1a\n"
PNG_BASE64 = base64.b64encode(PNG_BYTES).decode("ascii")


class ReplayAgent:
    """A Strands-shaped agent that replays scripted events, one list per call.

    Constructed input data, not a mock: it holds the event lists to stream
    and the prompts it was streamed on, and verifies nothing itself.
    """

    def __init__(self, *scripts: list) -> None:
        self.messages: list = []
        self.scripts = list(scripts)
        self.prompts: list[list] = []

    def stream_async(self, prompt: list) -> AsyncIterator[dict]:
        """Replay the next script."""
        self.prompts.append(prompt)
        return _replayed(self.scripts.pop(0))


async def _replayed(events: list) -> AsyncIterator[dict]:
    for event in events:
        yield event


def result_of(*interrupts: Interrupt) -> AgentResult:
    """Build the AgentResult that ends a stream."""
    return AgentResult(
        stop_reason="interrupt" if interrupts else "end_turn",
        message={"role": "assistant", "content": []},
        metrics=EventLoopMetrics(),
        state={},
        interrupts=list(interrupts) or None,
    )


def replies(agent: ReplayAgent, payload: dict) -> list[dict]:
    """Stream one reply and gather its events."""

    async def gather() -> list[dict]:
        stream = start_reply(agent, payload)
        return [event async for event in renderable_events(stream, agent=agent)]

    return asyncio.run(gather())


def test_a_turn_streams_the_renderable_events() -> None:
    agent = ReplayAgent([{"data": "hi"}])

    assert replies(agent, {"messages": []}) == [{"data": "hi"}]


def test_a_turn_runs_on_the_decoded_messages() -> None:
    agent = ReplayAgent([])
    payload = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"image": {"format": "png", "source": {"bytes": PNG_BASE64}}}
                ],
            }
        ]
    }

    replies(agent, payload)

    image = agent.prompts[0][0]["content"][0]["image"]
    assert image["source"]["bytes"] == PNG_BYTES


def test_a_resume_runs_on_the_decoded_answers() -> None:
    agent = ReplayAgent([{"data": "resumed"}])

    resumed = replies(agent, {"interrupt_responses": {"i-1": {"value": True}}})

    assert resumed == [{"data": "resumed"}]
    assert agent.prompts[0] == [
        {"interruptResponse": {"interruptId": "i-1", "response": True}}
    ]


def test_an_interrupted_stop_ends_the_reply_with_its_interrupts() -> None:
    agent = ReplayAgent(
        [{"result": result_of(Interrupt(id="i-1", name="approval", reason="Go?"))}]
    )

    assert replies(agent, {"messages": []}) == [
        {"interrupt": {"id": "i-1", "name": "approval", "reason": "Go?"}}
    ]


def test_a_payload_carrying_neither_envelope_is_welts_bug() -> None:
    agent = ReplayAgent([])

    with pytest.raises(KeyError, match="messages"):
        replies(agent, {})


def test_files_come_only_from_the_named_tools() -> None:
    file_block = {
        "image": {"format": "png", "source": {"bytes": PNG_BYTES}},
    }
    message = {
        "role": "user",
        "content": [
            {
                "toolResult": {
                    "toolUseId": "t-1",
                    "status": "success",
                    "content": [file_block],
                }
            }
        ],
    }
    agent = ReplayAgent([{"message": message}], [{"message": message}])
    agent.messages = [
        {
            "role": "assistant",
            "content": [{"toolUse": {"toolUseId": "t-1", "name": "picture_tool"}}],
        }
    ]

    async def gather(files_from: set[str] | None) -> list[dict]:
        stream = start_reply(agent, {"messages": []})
        return [
            event
            async for event in renderable_events(
                stream, agent=agent, files_from=files_from
            )
        ]

    listed = asyncio.run(gather({"picture_tool"}))
    unlisted = asyncio.run(gather(None))

    assert any("file" in event for event in listed)
    assert not any("file" in event for event in unlisted)
