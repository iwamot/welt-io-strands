"""The AgentCore Runtime entrypoint for a Strands agent Welt drives.

`welt_agent` builds the entrypoint that `BedrockAgentCoreApp` serves, so
an agent connects to Welt without rewriting the wiring every deployable
needs: telling a conversation turn from the answers that resume an
interrupted run, decoding each envelope, keeping the interrupted Agent
until its answers arrive, and reducing the stream to the events Welt
renders. The example agent of this repository once wrote this wiring out
by hand; this module is the same wiring as a function.

The interrupted Agent waits inside the returned entrypoint, under the
runtime's own lifecycle: AgentCore Runtime serves each session from its
own microVM, so one slot is enough, and the slot lives and dies with that
microVM — resuming after it was recycled (idle timeout, 8 hours at most)
raises an error the AgentCore Runtime SDK reports as an `error` event,
which Welt renders as its resume-failure notice. The slot is resume-only:
a normal turn always builds a fresh Agent, because the Slack thread is
the source of truth for conversation history and the messages Welt sends
already carry it whole.

`send_file` hands the Slack thread a file without handing it to the
model: a tool queues the file, and the entrypoint puts it on the wire
beside the events of the reply being streamed. The model never sees what
was sent, so a tool whose file matters to the conversation says what it
holds in its result — or hands it to the model as a content block and is
named in `files_from` instead.
"""

from collections.abc import AsyncIterator, Callable, Collection
from typing import Protocol

from welt_io_strands import (
    _file_event,
    decode_interrupt_responses,
    decode_messages,
    renderable_events,
)

__all__ = ["send_file", "welt_agent"]


class _StreamingAgent(Protocol):
    """What the entrypoint drives: the Agent's streaming face.

    Importing the SDK to name the Agent would say what two members
    already say. This names them instead, and an Agent satisfies it.
    """

    messages: list

    def stream_async(self, prompt: list) -> AsyncIterator[dict]:
        """Stream the agent's reply to a prompt."""
        ...


# The files queued by `send_file`, on their way to the Slack thread. One
# queue for the process, like the interrupt slot is one per entrypoint:
# AgentCore Runtime serves each session from its own microVM, so no other
# reply's files can interleave with the running one's.
_pending_files: list[dict] = []


def send_file(name: str, data: bytes) -> None:
    """
    Queue one file for the Slack thread, beside the reply being streamed.

    The file rides the wire between the events of the running reply, and
    never reaches the model. A tool that wants the model to know what the
    file holds says so in its result string — or returns the file as an
    image/document/video content block and is named in `files_from`, which
    puts it in front of the model and on the thread both.

    A file queued by a turn that failed before draining does not ride a
    later reply: the entrypoint starts every turn with the queue empty.

    Args:
        name (str): The upload filename, extension included.
        data (bytes): The raw file bytes.

    Raises:
        TypeError: If the name or the data is of the wrong type.
        ValueError: If either is empty. Slack refuses a zero-byte upload,
            and the whole reply fails with it, so an empty file is refused
            here, where the tool that queued it is still on the stack.
    """
    _pending_files.append(_file_event(_checked_name(name), _checked_data(data)))


def _checked_name(name: object) -> str:
    """
    Check an upload filename.

    Args:
        name (object): The name the caller passed.

    Returns:
        str: The name.

    Raises:
        TypeError: If it is not a string.
        ValueError: If it is empty.
    """
    if not isinstance(name, str):
        raise TypeError(f"name must be a str, not {type(name).__name__}")
    if not name:
        raise ValueError("name must not be empty")
    return name


def _checked_data(data: object) -> bytes:
    """
    Check a file's bytes.

    Args:
        data (object): The data the caller passed.

    Returns:
        bytes: The data.

    Raises:
        TypeError: If it is not bytes.
        ValueError: If it is empty.
    """
    if not isinstance(data, bytes):
        raise TypeError(f"data must be bytes, not {type(data).__name__}")
    if not data:
        raise ValueError("data must not be empty; Slack refuses an empty upload")
    return data


def _drained() -> list[dict]:
    """
    Take every queued file event off the queue, in order.

    Returns:
        list[dict]: The `file` events queued since the last drain.
    """
    events = _pending_files[:]
    _pending_files.clear()
    return events


def welt_agent(
    new_agent: Callable[[], _StreamingAgent],
    *,
    files_from: Collection[str] | None = None,
) -> Callable[[dict], AsyncIterator[dict]]:
    """
    Build the AgentCore Runtime entrypoint for an agent Welt drives.

    The returned function is what `BedrockAgentCoreApp` takes::

        app = BedrockAgentCoreApp()
        app.entrypoint(welt_agent(new_agent, files_from={"generate_image"}))

    It reads which envelope Welt sent — Converse-shaped `messages` for a
    conversation turn, `interrupt_responses` for the answers that resume
    an interrupted run — drives the agent, and yields the events Welt
    renders, the files tools queued with `send_file` among them.

    Args:
        new_agent (Callable[[], Agent]): Builds the Agent of one
            conversation turn. Called on every turn, so each turn runs on
            the messages Welt sends and nothing carried over — except an
            interrupted run, which resumes on the Agent that raised the
            interrupt.
        files_from (Collection[str] | None): The names of the tools whose
            image/document/video results become `file` events, as
            `renderable_events` takes it. None takes files from none of
            them.

    Returns:
        Callable[[dict], AsyncIterator[dict]]: The entrypoint. It raises
            `RuntimeError` when asked to resume a run its microVM no
            longer holds — the session was recycled while the buttons
            waited — which the AgentCore Runtime SDK reports as an
            `error` event and Welt renders as its resume-failure notice.
    """
    interrupted_agent: _StreamingAgent | None = None

    async def entrypoint(payload: dict) -> AsyncIterator[dict]:
        """
        Stream a reply to the conversation or approval answers Welt sent.

        Args:
            payload (dict): The invocation payload, carrying one of the
                two envelopes. What Welt sends is taken as correct, so a
                payload carrying neither is Welt's bug, and the KeyError
                it raises is reported as an `error` event by the SDK.

        Yields:
            dict: The renderable subset of the agent's stream, and the
                `file` events tools queued with `send_file`.

        Raises:
            RuntimeError: If there is no interrupted run to resume.
        """
        nonlocal interrupted_agent
        # A failed turn's leftovers stay off this reply.
        _pending_files.clear()

        if "interrupt_responses" in payload:
            agent = interrupted_agent
            interrupted_agent = None
            if agent is None:  # The microVM was recycled while the buttons waited.
                raise RuntimeError("No interrupted agent to resume in this session.")
            stream = agent.stream_async(
                decode_interrupt_responses(payload["interrupt_responses"])
            )
        else:
            agent = new_agent()
            stream = agent.stream_async(decode_messages(payload["messages"]))

        interrupted = False
        async for event in renderable_events(
            stream, agent=agent, files_from=files_from
        ):
            if "interrupt" in event:
                interrupted = True
            yield event
            for file_event in _drained():
                yield file_event
        # Files a tool queued after its result's events had already
        # drained — the stream's tail — still belong to this reply.
        for file_event in _drained():
            yield file_event

        if interrupted:
            # Re-stashed on every interrupted stop, so a resume that
            # interrupts again keeps working.
            interrupted_agent = agent

    return entrypoint
