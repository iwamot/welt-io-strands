"""Adapters for the two directions of Welt's wire contract.

The wire between Welt and the agent is JSON, and plain Strands values do not
fit it in either direction:

- Inbound, JSON cannot carry raw bytes, so Welt base64-encodes the `bytes`
  slot of the Converse image/document/video blocks it builds from Slack
  uploads. `decode_messages` restores them before Strands (Bedrock
  Converse) sees the messages. Welt resumes an interrupted run with a
  plain mapping of interrupt id to the chosen answer;
  `decode_interrupt_responses` turns it into Strands' resume input.
- Outbound, raw `stream_async` events carry values that are not
  JSON-serializable (the Agent itself, UUIDs, traces, raw file bytes), which
  the AgentCore Runtime SDK would degrade to a plain string on the SSE wire.
  `renderable_events` reduces the stream to the events Welt renders, with
  the files of the tools the agent names base64-encoded — the inbound
  encoding in reverse. `interrupt_reason` builds the reason shape Welt
  renders as a message with buttons, a free-text field, or both when a tool
  interrupts for human input.

What Welt sends is taken as correct. Welt builds the payload and checks its
own output against the wire contract before releasing it, so a payload that
departs from the contract is a bug on the sending side, not an input to
validate against runtime errors — a malformed one surfaces as an ordinary
error from whatever touches it first. The one thing `decode_messages`
does refuse is a content block of a kind Welt never sends: a `toolUse` or
`toolResult` is not a shape error but a forged conversation turn, and
loaded as history it would let whoever reached the runtime put words the
model treats as its own past actions into the run. What this adapter
checks beyond that is the values its own caller passes to
`interrupt_reason`, since Welt renders a reason it cannot match as its
default buttons, silently.

The reply stream is read as what Strands documents it to be: `stream_async`
yields plain event dicts, so the keys are read as keys, and the AgentResult
ending the stream as the object it is. Only what Welt reads goes on the
wire — an event carrying more than that costs bandwidth for something the
renderer discards.
"""

import base64
import copy
import logging
from collections.abc import AsyncIterator, Collection, Sequence
from typing import Literal, NotRequired, Protocol, TypedDict

try:
    from ._version import __version__
except ImportError:
    __version__ = "0.0.0+unknown"

__all__ = [
    "InputSpec",
    "OptionSpec",
    "decode_interrupt_responses",
    "decode_messages",
    "interrupt_reason",
    "renderable_events",
]

logger = logging.getLogger(__name__)


def decode_messages(messages: list) -> list:
    """
    Decode Welt's messages payload into the messages Strands consumes.

    Strands (Bedrock Converse) consumes Welt's Converse-shaped messages
    as they are, except that the image/document/video bytes arrive
    base64-encoded — JSON cannot carry raw bytes — and Strands expects
    them raw.

    Args:
        messages (list): The `messages` value of Welt's payload.

    Returns:
        list: A decoded copy of the messages; the input is left untouched.

    Raises:
        binascii.Error: If a file block's bytes are not valid base64.
    """
    decoded = copy.deepcopy(messages)
    _decode_sources(decoded)
    return decoded


# The content block kinds Welt sends. A block of any other kind — a toolUse or
# toolResult in particular — is a forged conversation turn, not something Welt
# builds, and loaded as history it would let a caller put words the model
# treats as its own past actions into the run. It is refused, not passed on.
_ALLOWED_BLOCKS = frozenset({"text", "image", "document", "video"})


def _decode_sources(messages: list) -> None:
    """
    Restore the raw bytes of every file block, in place.

    Args:
        messages (list): The Converse-shaped messages from Welt's payload.

    Returns:
        None

    Raises:
        binascii.Error: If a block's bytes are not valid base64.
        ValueError: If a block is of a kind Welt does not send.
    """
    for message in messages:
        for block in message["content"]:
            if not _ALLOWED_BLOCKS.issuperset(block):
                raise ValueError(f"unexpected content block: {sorted(block)}")
            for kind in ("image", "document", "video"):
                if kind in block:
                    source = block[kind]["source"]
                    # validate=True: the default discards what is not base64
                    # and returns bytes that were never encoded, where this
                    # refuses them.
                    source["bytes"] = base64.b64decode(source["bytes"], validate=True)


def decode_interrupt_responses(responses: dict) -> list:
    """
    Decode Welt's interrupt answers into Strands' resume input.

    Welt resumes an interrupted run with a payload mapping each interrupt
    id to the answer a human chose in the thread. Strands resumes from a
    list of `interruptResponse` content items; the returned list feeds
    `Agent.stream_async` directly.

    Args:
        responses (dict): The `interrupt_responses` value of Welt's
            payload.

    Returns:
        list: One `interruptResponse` item per answered interrupt.
    """
    return [
        {"interruptResponse": {"interruptId": interrupt_id, "response": response}}
        for interrupt_id, response in responses.items()
    ]


class OptionSpec(TypedDict):
    """One button of a structured interrupt reason."""

    value: object
    label: NotRequired[str]
    style: NotRequired[Literal["primary", "danger"]]


class InputSpec(TypedDict):
    """The free-text field of a structured interrupt reason."""

    label: NotRequired[str]
    multiline: NotRequired[bool]


_OPTION_KEYS = frozenset({"value", "label", "style"})
_INPUT_KEYS = frozenset({"label", "multiline"})
_STYLES = frozenset({"primary", "danger"})


def interrupt_reason(
    message: str,
    options: Sequence[OptionSpec] | None = None,
    *,
    input: InputSpec | None = None,
) -> dict:
    """
    Build an interrupt reason that Welt renders as the specified widgets.

    Welt renders this shape as `message` followed by one button per option
    (`options`), a free-text field whose submitted text becomes the
    interrupt's response (`input`), or both — whichever answer comes
    first, a pressed button or the submitted text, settles the question.
    With neither, the message renders as itself and Welt's default
    Approve / Deny buttons answer it.

    Building the reason through this helper is what makes a typo an error.
    `ToolContext.interrupt` takes its `reason` as `Any`, so a dict literal
    handed to it directly is checked by nothing, and Welt's reaction to a
    reason it cannot match is its default Approve / Deny buttons — no
    error, no log, just widgets the author did not ask for. The typed
    parameters here catch a misspelled key before the run, and the checks
    below catch it in runs where no type checker was involved.

    What is checked is the shape, not the size: Welt's own rendering caps
    (how many buttons one Slack block holds, how long a button value may
    be) are Welt's to enforce, and a copy of them here would be four copies
    to keep in step with a number only Welt knows.

    Args:
        message (str): The text Welt shows above the widgets.
        options (Sequence[OptionSpec] | None): One dict per button: a
            required `value` (any JSON value, which the interrupting tool
            receives as the response when the button is pressed), an
            optional `label` (the button text; omitted, Welt shows the
            value), and an optional `style` ("primary" or "danger").
            None omits the buttons.
        input (InputSpec | None): The free-text field: an optional `label`
            (the field's label) and an optional `multiline` (whether the
            field accepts multiple lines) — `{}` takes Welt's defaults for
            both. None omits the field.

    Returns:
        dict: The reason to pass to `ToolContext.interrupt`.

    Raises:
        TypeError: If a value is of the wrong type.
        ValueError: If a key is unknown or a required string is empty.
    """
    reason: dict = {"message": _checked_message(message)}
    if options is not None:
        reason["options"] = _checked_options(options)
    if input is not None:
        reason["input"] = _checked_input(input)
    return reason


def _checked_message(message: object) -> str:
    """
    Check a reason's message.

    Args:
        message (object): The message the caller passed.

    Returns:
        str: The message.

    Raises:
        TypeError: If it is not a string.
        ValueError: If it is empty.
    """
    if not isinstance(message, str):
        raise TypeError(f"message must be a str, not {type(message).__name__}")
    if not message:
        raise ValueError("message must not be empty")
    return message


def _checked_options(options: object) -> list[dict]:
    """
    Check a reason's options.

    Args:
        options (object): The options the caller passed.

    Returns:
        list[dict]: The options, as a list.

    Raises:
        TypeError: If the options, or one of them, are of the wrong type.
        ValueError: If there are none, or one carries an unknown key or an
            empty value.
    """
    if isinstance(options, (str, bytes)) or not isinstance(options, Sequence):
        raise TypeError(f"options must be a sequence, not {type(options).__name__}")
    if not options:
        raise ValueError("options must not be empty; omit it to show no buttons")
    return [_checked_option(option) for option in options]


def _checked_option(option: object) -> dict:
    """
    Check one option of a reason.

    Args:
        option (object): The option the caller passed.

    Returns:
        dict: The option.

    Raises:
        TypeError: If it, or one of its values, is of the wrong type.
        ValueError: If it carries an unknown key, no `value`, an empty
            `label`, or a style Welt does not render.
    """
    if not isinstance(option, dict):
        raise TypeError(f"an option must be a dict, not {type(option).__name__}")
    _refuse_unknown_keys(option, _OPTION_KEYS, "an option")
    # Only presence is checked: an option's value is whatever JSON value
    # the tool wants back, and nothing about it is a typo to catch.
    if "value" not in option:
        raise ValueError("an option needs a value")
    # `label` and `style` are read by presence instead of by None, since a
    # key carrying None reaches Welt as a null, which Welt reads as a
    # malformed field rather than an omitted one and answers with its
    # default buttons instead.
    if "label" in option:
        label = option.get("label")
        if not isinstance(label, str):
            raise TypeError(
                f"an option's label must be a str, not {type(label).__name__}"
            )
        if not label:
            raise ValueError("an option's label must not be empty")
    if "style" in option and option.get("style") not in _STYLES:
        raise ValueError(f"an option's style must be one of {sorted(_STYLES)}")
    return option


def _checked_input(input_spec: object) -> dict:
    """
    Check a reason's free-text field.

    Args:
        input_spec (object): The field the caller passed.

    Returns:
        dict: The field.

    Raises:
        TypeError: If it, or one of its values, is of the wrong type.
        ValueError: If it carries an unknown key or an empty label.
    """
    if not isinstance(input_spec, dict):
        raise TypeError(f"input must be a dict, not {type(input_spec).__name__}")
    _refuse_unknown_keys(input_spec, _INPUT_KEYS, "input")
    if "label" in input_spec:
        label = input_spec.get("label")
        if not isinstance(label, str):
            raise TypeError(f"input's label must be a str, not {type(label).__name__}")
        if not label:
            raise ValueError("input's label must not be empty")
    if "multiline" in input_spec:
        multiline = input_spec.get("multiline")
        if not isinstance(multiline, bool):
            raise TypeError(
                f"input's multiline must be a bool, not {type(multiline).__name__}"
            )
    return input_spec


def _refuse_unknown_keys(value: dict, allowed: frozenset[str], subject: str) -> None:
    """
    Refuse keys the wire contract does not name.

    A misspelled key is the mistake worth catching: Welt drops the whole
    reason to its default rendering rather than ignoring the stray key.

    Args:
        value (dict): The dict the caller passed.
        allowed (frozenset[str]): The keys the contract names.
        subject (str): What the dict is, for the error message.

    Returns:
        None

    Raises:
        ValueError: If the dict carries a key outside `allowed`.
    """
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(
            f"{subject} carries unknown key(s): {', '.join(unknown)}"
            f" (known: {', '.join(sorted(allowed))})"
        )


class _Agent(Protocol):
    """What `renderable_events` reads from the Agent being streamed.

    Importing the SDK to read one attribute off an Agent would say what a
    line of code already says. This names the attribute instead, and an
    Agent satisfies it.
    """

    messages: list


async def renderable_events(
    events: AsyncIterator[dict],
    *,
    agent: _Agent | None = None,
    files_from: Collection[str] | None = None,
) -> AsyncIterator[dict]:
    """
    Reduce Strands `stream_async` events to the subset Welt renders.

    Which of the agent's files belong in the reply is the agent's call, so
    a tool's files become `file` events only when the tool is named in
    `files_from` — a tool that hands the model a file to read
    (strands-tools' `file_read`, say) stays off the wire unless it is
    listed. Files the model itself returns are its reply, and always go.

    Each event carries only what Welt reads. A `current_tool_use` is cut
    down to the name and id behind the indicator, so the tool's arguments —
    which Strands re-sends in full on every input delta — stay off the
    wire, and an event with nothing to render (a text chunk the model left
    empty, a file with no bytes) is not sent at all.

    Args:
        events (AsyncIterator[dict]): Raw `stream_async` events.
        agent (_Agent | None): The Agent being streamed, whose messages
            name the tool behind each tool result — the only place the name
            survives a resume, where the stream carries the result alone.
            Required with a non-empty `files_from`.
        files_from (Collection[str] | None): The names of the tools whose
            files become `file` events. None takes files from none of them.

    Yields:
        dict: A `data` event per non-empty text chunk, a `current_tool_use`
            event (name and toolUseId) per tool-use update, and per
            completed tool a `tool_result` event — slimmed to the toolUseId
            and status, so text tool output stays off the wire — followed
            by a `file` event (filename plus base64 bytes, which Welt
            uploads to the Slack thread) per image, document, or video
            block a tool listed in `files_from` returned. Such blocks in
            the assistant message itself become `file` events
            unconditionally. A stream that stops for human input ends with
            an `interrupt` event per pending interrupt (its id, name, and
            reason, which Welt renders as buttons in the Slack thread).

    Raises:
        ValueError: If `files_from` names tools without an agent, which
            leaves the tool behind a file unknowable.
    """
    if files_from and agent is None:
        raise ValueError("files_from needs the agent the tool names come from")
    async for event in events:
        if "data" in event:
            # An empty chunk carries nothing to render.
            if event["data"]:
                yield {"data": event["data"]}
        elif "current_tool_use" in event:
            yield {"current_tool_use": _tool_use(event["current_tool_use"])}
        elif "message" in event:
            for rendered in _message_events(event["message"], agent, files_from):
                yield rendered
        elif "result" in event:
            # Strands ends the stream with the AgentResult, whose interrupts
            # carry one pending question each when the run stopped for human
            # input. The reason travels on unmodified: it is any
            # JSON-serializable value by Strands' contract, and interpreting
            # it is the renderer's job.
            for interrupt in event["result"].interrupts or ():
                yield {
                    "interrupt": {
                        "id": interrupt.id,
                        "name": interrupt.name,
                        "reason": interrupt.reason,
                    }
                }


def _tool_use(current_tool_use: dict) -> dict:
    """
    Keep the two fields Welt reads from a tool-use update.

    Args:
        current_tool_use (dict): The framework's own tool-use state, which
            accumulates the tool's input as the model streams it.

    Returns:
        dict: The name and toolUseId it carries, whichever are set.
    """
    return {
        key: current_tool_use[key]
        for key in ("name", "toolUseId")
        if key in current_tool_use
    }


def _message_events(
    message: dict, agent: _Agent | None, files_from: Collection[str] | None
) -> list[dict]:
    """
    Extract renderable events from a Strands message event.

    Strands adds tool results to the conversation as a message whose content
    blocks each carry a `toolResult`; a `tool_result` entry is slimmed to the
    toolUseId and status, followed by a `file` event per image/document/video
    block a tool the agent takes files from returned. Model messages carry
    text (nothing to extract — it already streamed as `data` events) and, for
    models that generate files, image/document/video blocks, which become
    `file` events too.

    Args:
        message (dict): The `message` value of a stream event.
        agent (_Agent | None): The Agent being streamed.
        files_from (Collection[str] | None): The names of the tools whose
            files become `file` events.

    Returns:
        list[dict]: The `tool_result` and `file` events, in content order.
    """
    events: list[dict] = []
    for block in message["content"]:
        if "toolResult" not in block:
            event = _file_event_from_block(block, "the model")
            if event is not None:
                events.append(event)
            continue
        tool_result = block["toolResult"]
        events.append(
            {
                "tool_result": {
                    "toolUseId": tool_result["toolUseId"],
                    "status": tool_result["status"],
                }
            }
        )
        tool_name = _emitting_tool(agent, files_from, tool_result["toolUseId"])
        if tool_name is not None:
            events.extend(
                event
                for result_block in tool_result["content"]
                if (event := _file_event_from_block(result_block, tool_name))
                is not None
            )
    return events


def _emitting_tool(
    agent: _Agent | None, files_from: Collection[str] | None, tool_use_id: str
) -> str | None:
    """
    Name the tool behind a tool result, if its files belong on the wire.

    Args:
        agent (_Agent | None): The Agent being streamed.
        files_from (Collection[str] | None): The names of the tools whose
            files become `file` events.
        tool_use_id (str): The `toolUseId` of the tool result.

    Returns:
        str | None: The tool's name, or None if its files stay off the
            wire.
    """
    if not files_from or agent is None:
        return None
    name = _tool_name(agent, tool_use_id)
    return name if name in files_from else None


def _tool_name(agent: _Agent, tool_use_id: str) -> str | None:
    """
    Find the name of the tool a tool use id belongs to.

    The stream carries the name in the assistant message that requested the
    tool, but a resumed run streams the tool's result alone — the agent's
    messages, which outlive the interrupt, hold the request either way.

    Args:
        agent (_Agent): The Agent being streamed.
        tool_use_id (str): The id of the tool use to name.

    Returns:
        str | None: The tool's name, or None if the agent's messages do not
            hold the request.
    """
    for message in reversed(agent.messages):
        for block in message["content"]:
            if "toolUse" in block and block["toolUse"]["toolUseId"] == tool_use_id:
                return block["toolUse"]["name"]
    return None


# Converse format tokens double as filename extensions, except this one.
_EXTENSION_BY_FORMAT = {"three_gp": "3gp"}


def _file_event_from_block(block: dict, origin: str) -> dict | None:
    """
    Build a `file` event from a content block carrying raw file bytes.

    Args:
        block (dict): A Converse content block (from a toolResult or an
            assistant message).
        origin (str): What produced the block, for the log line an empty
            file leaves behind.

    Returns:
        dict | None: The `file` event (name plus base64 bytes), or None for
            blocks without raw image/document/video bytes.
    """
    for kind in ("image", "document", "video"):
        if kind not in block:
            continue
        media = block[kind]
        # A Converse source carries the file's bytes or points at it in S3,
        # and there is nothing to upload from a pointer.
        data = media["source"].get("bytes")
        if not data:
            if data is not None:
                # Slack refuses a zero-byte upload, and the whole reply
                # fails with it, so an empty file does not go on the wire.
                logger.warning(
                    "Skipped an empty file from %s: %s",
                    origin,
                    _file_name(kind, media),
                )
            return None
        return _file_event(_file_name(kind, media), data)
    return None


def _file_event(name: str, data: bytes) -> dict:
    """
    Build a `file` wire event, which Welt uploads to the Slack thread.

    Args:
        name (str): The upload filename, extension included.
        data (bytes): The raw file bytes.

    Returns:
        dict: The `file` event (name plus base64 bytes).
    """
    return {"file": {"name": name, "bytes": base64.b64encode(data).decode("ascii")}}


def _file_name(kind: str, media: dict) -> str:
    """
    Synthesize an upload filename for a file block.

    Args:
        kind (str): The block kind (image, document, or video).
        media (dict): The block's value, whose optional `name` (document
            blocks) and `format` provide the filename parts.

    Returns:
        str: The block's name (or its kind) plus the format as extension.
    """
    base = media.get("name") or kind
    file_format = media.get("format")
    if not file_format:
        return base
    return f"{base}.{_EXTENSION_BY_FORMAT.get(file_format, file_format)}"
