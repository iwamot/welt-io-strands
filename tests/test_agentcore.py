import asyncio
import base64
from collections.abc import AsyncIterator, Callable, Iterator

import pytest
from strands.agent import AgentResult
from strands.interrupt import Interrupt
from strands.telemetry import EventLoopMetrics

from welt_io_strands.agentcore import (
    _checked_data,
    _checked_name,
    _drained,
    send_file,
    welt_agent,
)

PNG_BYTES = b"\x89PNG\r\n\x1a\n"
PNG_BASE64 = base64.b64encode(PNG_BYTES).decode("ascii")


@pytest.fixture(autouse=True)
def empty_queue() -> Iterator[None]:
    """Start and leave every test with no files queued."""
    _drained()
    yield
    _drained()


def test_sent_file_becomes_a_file_wire_event() -> None:
    send_file("chart.png", PNG_BYTES)
    assert _drained() == [{"file": {"name": "chart.png", "bytes": PNG_BASE64}}]


def test_files_drain_in_the_order_they_were_sent() -> None:
    send_file("first.txt", b"1")
    send_file("second.txt", b"2")
    names = [event["file"]["name"] for event in _drained()]
    assert names == ["first.txt", "second.txt"]


def test_draining_empties_the_queue() -> None:
    send_file("chart.png", PNG_BYTES)
    _drained()
    assert _drained() == []


# The checks below go through the private helpers, which take `object`: a
# deliberately wrong value handed to the typed public function would be a
# type error in this file, and the helpers are where the checks live.


def test_a_name_that_is_not_a_string_is_refused() -> None:
    with pytest.raises(TypeError, match="name must be a str, not int"):
        _checked_name(1)


def test_an_empty_name_is_refused() -> None:
    with pytest.raises(ValueError, match="name must not be empty"):
        _checked_name("")


def test_data_that_is_not_bytes_is_refused() -> None:
    with pytest.raises(TypeError, match="data must be bytes, not str"):
        _checked_data("not bytes")


def test_empty_data_is_refused() -> None:
    with pytest.raises(ValueError, match="data must not be empty"):
        _checked_data(b"")


def test_a_refused_file_is_not_queued() -> None:
    with pytest.raises(ValueError):
        send_file("chart.png", b"")
    assert _drained() == []


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


def replies(
    entrypoint: Callable[[dict], AsyncIterator[dict]], payload: dict
) -> list[dict]:
    """Run the entrypoint on one payload and gather what it streams."""

    async def gather() -> list[dict]:
        return [event async for event in entrypoint(payload)]

    return asyncio.run(gather())


def test_a_turn_streams_the_renderable_events() -> None:
    agent = ReplayAgent([{"data": "hi"}])

    entrypoint = welt_agent(lambda: agent)

    assert replies(entrypoint, {"messages": []}) == [{"data": "hi"}]


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

    replies(welt_agent(lambda: agent), payload)

    image = agent.prompts[0][0]["content"][0]["image"]
    assert image["source"]["bytes"] == PNG_BYTES


def test_each_turn_runs_on_a_fresh_agent() -> None:
    agents = [ReplayAgent([{"data": "one"}]), ReplayAgent([{"data": "two"}])]
    made = iter(agents)

    entrypoint = welt_agent(lambda: next(made))
    replies(entrypoint, {"messages": []})
    replies(entrypoint, {"messages": []})

    assert [len(agent.prompts) for agent in agents] == [1, 1]


class SendingAgent:
    """A Strands-shaped agent whose stream queues a file mid-reply."""

    def __init__(self, *, after_last_event: bool = False) -> None:
        self.messages: list = []
        self.after_last_event = after_last_event

    def stream_async(self, prompt: list) -> AsyncIterator[dict]:
        """Stream two chunks, queueing a file the way a tool would."""
        return self._events()

    async def _events(self) -> AsyncIterator[dict]:
        yield {"data": "before"}
        if not self.after_last_event:
            send_file("chart.png", PNG_BYTES)
            yield {"data": "after"}
        else:
            send_file("chart.png", PNG_BYTES)


def test_a_file_a_tool_queued_rides_beside_the_reply() -> None:
    entrypoint = welt_agent(SendingAgent)

    assert replies(entrypoint, {"messages": []}) == [
        {"data": "before"},
        {"data": "after"},
        {"file": {"name": "chart.png", "bytes": PNG_BASE64}},
    ]


def test_a_file_queued_after_the_last_event_still_rides_the_reply() -> None:
    entrypoint = welt_agent(lambda: SendingAgent(after_last_event=True))

    assert replies(entrypoint, {"messages": []}) == [
        {"data": "before"},
        {"file": {"name": "chart.png", "bytes": PNG_BASE64}},
    ]


def test_a_failed_turns_leftover_files_stay_off_the_next_reply() -> None:
    send_file("stale.txt", b"left behind")

    entrypoint = welt_agent(lambda: ReplayAgent([{"data": "fresh"}]))

    assert replies(entrypoint, {"messages": []}) == [{"data": "fresh"}]


def test_resume_without_an_interrupted_run_is_refused() -> None:
    entrypoint = welt_agent(lambda: ReplayAgent([]))

    with pytest.raises(RuntimeError, match="No interrupted agent"):
        replies(entrypoint, {"interrupt_responses": {}})


def test_an_interrupted_run_resumes_on_the_agent_that_raised_it() -> None:
    agent = ReplayAgent(
        [{"result": result_of(Interrupt(id="i-1", name="approval", reason="Go?"))}],
        [{"data": "resumed"}],
    )
    turns_started = []

    def new_agent() -> ReplayAgent:
        turns_started.append(True)
        return agent

    entrypoint = welt_agent(new_agent)
    first = replies(entrypoint, {"messages": []})
    second = replies(entrypoint, {"interrupt_responses": {"i-1": {"value": True}}})

    assert first == [{"interrupt": {"id": "i-1", "name": "approval", "reason": "Go?"}}]
    assert second == [{"data": "resumed"}]
    # The resume ran on the stashed agent, not a fresh one.
    assert len(turns_started) == 1
    assert agent.prompts[1] == [
        {"interruptResponse": {"interruptId": "i-1", "response": True}}
    ]


def test_the_slot_empties_once_resumed() -> None:
    agent = ReplayAgent(
        [{"result": result_of(Interrupt(id="i-1", name="approval"))}],
        [{"data": "resumed"}],
    )

    entrypoint = welt_agent(lambda: agent)
    replies(entrypoint, {"messages": []})
    replies(entrypoint, {"interrupt_responses": {"i-1": {"value": True}}})

    with pytest.raises(RuntimeError, match="No interrupted agent"):
        replies(entrypoint, {"interrupt_responses": {"i-1": {"value": True}}})


def test_a_resume_that_interrupts_again_can_resume_again() -> None:
    agent = ReplayAgent(
        [{"result": result_of(Interrupt(id="i-1", name="first"))}],
        [{"result": result_of(Interrupt(id="i-2", name="second"))}],
        [{"data": "done"}],
    )

    entrypoint = welt_agent(lambda: agent)
    replies(entrypoint, {"messages": []})
    replies(entrypoint, {"interrupt_responses": {"i-1": {"value": True}}})
    third = replies(entrypoint, {"interrupt_responses": {"i-2": {"value": True}}})

    assert third == [{"data": "done"}]
