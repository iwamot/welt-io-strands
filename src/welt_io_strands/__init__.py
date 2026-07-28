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
  encoding in reverse. `file_event` builds the same `file` event from a
  name and raw bytes, for the files the host app attaches itself.
  `interrupt_reason` builds the reason shape Welt renders as a message with
  buttons, a free-text field, or both when a tool interrupts for human
  input.

Neither direction is checked by hand. What arrives is checked against
Welt's published schemas, vendored as `schema/` and compiled into
`_schema.py`, and what the builders produce is checked against them before
it is returned. The reply stream is read as what Strands documents it to
be: `stream_async` yields plain event dicts, so the keys are read as keys,
and the AgentResult ending the stream as the object it is.
"""

import base64
import copy
import warnings
from collections.abc import AsyncIterator, Collection, Sequence
from typing import Protocol, cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match
from jsonschema.protocols import Validator

from ._schema import REPLY_EVENTS, REQUEST_PAYLOAD

try:
    from ._version import __version__
except ImportError:
    __version__ = "0.0.0+unknown"

__all__ = [
    "decode_file_blocks",
    "decode_interrupt_responses",
    "decode_messages",
    "file_event",
    "interrupt_reason",
    "renderable_events",
]


# One validator per envelope value, built once: `decode_messages` and
# `decode_interrupt_responses` each take the value rather than the whole
# payload, so each points at the schema's definition for it.
def _validator(schema: dict, definition: str) -> Validator:
    """
    Build a validator for one definition of a wire schema.

    Args:
        schema (dict): The schema carrying the definition.
        definition (str): The name under the schema's `$defs`.

    Returns:
        Validator: The validator.
    """
    return Draft202012Validator(
        {"$ref": f"#/$defs/{definition}", "$defs": schema["$defs"]}
    )


# Inbound: the two envelope values, each taken on its own.
_MESSAGES = _validator(REQUEST_PAYLOAD, "messages")
_INTERRUPT_RESPONSES = _validator(REQUEST_PAYLOAD, "interruptResponses")

# Outbound: what the builders below must produce for Welt to render it.
_FILE = _validator(REPLY_EVENTS, "file")
_STRUCTURED_REASON = _validator(REPLY_EVENTS, "structuredReason")


def _checked(validator: Validator, payload: object) -> None:
    """
    Check a payload, raising the most specific error it produced.

    A message is checked against one definition per role, so a violation
    inside one fails the pair and is reported against the message as a
    whole. The error that says which block, and why, is among the sub-
    errors, which is the one worth raising.

    Args:
        validator (Validator): The validator for this envelope value.
        payload (object): The value from Welt's payload.

    Returns:
        None

    Raises:
        jsonschema.exceptions.ValidationError: If the payload violates the
            wire contract.
    """
    error = best_match(validator.iter_errors(payload))
    if error is not None:
        raise error


def decode_messages(messages: object) -> list:
    """
    Decode Welt's messages payload into the messages Strands consumes.

    Strands (Bedrock Converse) consumes Welt's Converse-shaped messages
    as they are, except that the image/document/video bytes arrive
    base64-encoded — JSON cannot carry raw bytes — and Strands expects
    them raw.

    The payload is checked against Welt's published schema first, so a
    payload that departs from the wire contract raises rather than reaching
    the agent as a conversation with a turn missing.

    Args:
        messages (object): The `messages` value of Welt's payload.

    Returns:
        list: A decoded copy of the messages; the input is left untouched.

    Raises:
        jsonschema.exceptions.ValidationError: If the payload violates the
            wire contract. The error names the offending path.
        binascii.Error: If a file block's bytes are not valid base64, which
            the schema annotates but does not assert.
    """
    _checked(_MESSAGES, messages)
    # The schema has vouched for the shape; the cast tells the type checker.
    decoded = copy.deepcopy(cast(list, messages))
    _decode_sources(decoded)
    return decoded


def decode_file_blocks(messages: object) -> None:
    """
    Decode base64 image/document/video bytes back to raw bytes, in place.

    Deprecated: use `decode_messages`, which returns a decoded copy
    instead of mutating its input.

    Args:
        messages (object): The Converse-shaped messages from Welt's
            payload.

    Returns:
        None

    Raises:
        jsonschema.exceptions.ValidationError: If the payload violates the
            wire contract. The input is left untouched, since the whole
            payload is checked before any of it is decoded.
        binascii.Error: If a file block's bytes are not valid base64, which
            the schema annotates but does not assert.
    """
    warnings.warn(
        "decode_file_blocks is deprecated; use decode_messages, which returns"
        " a decoded copy instead of mutating its input",
        DeprecationWarning,
        stacklevel=2,
    )
    _checked(_MESSAGES, messages)
    # The schema has vouched for the shape; the cast tells the type checker.
    _decode_sources(cast(list, messages))


def _decode_sources(messages: list) -> None:
    """
    Restore the raw bytes of every file block, in place.

    Args:
        messages (list): Messages already checked against the schema.

    Returns:
        None

    Raises:
        binascii.Error: If a block's bytes are not valid base64.
    """
    for message in messages:
        for block in message["content"]:
            for kind in ("image", "document", "video"):
                if kind in block:
                    source = block[kind]["source"]
                    # validate=True: the default discards what is not base64
                    # and returns bytes that were never encoded, where this
                    # refuses them.
                    source["bytes"] = base64.b64decode(source["bytes"], validate=True)


def decode_interrupt_responses(responses: object) -> list:
    """
    Decode Welt's interrupt answers into Strands' resume input.

    Welt resumes an interrupted run with a payload mapping each interrupt
    id to the answer a human chose in the thread. Strands resumes from a
    list of `interruptResponse` content items; the returned list feeds
    `Agent.stream_async` directly.

    The payload is checked against Welt's published schema first, so
    resuming a run with an answer short raises rather than happening
    quietly.

    Args:
        responses (object): The `interrupt_responses` value of Welt's
            payload.

    Returns:
        list: One `interruptResponse` item per answered interrupt.

    Raises:
        jsonschema.exceptions.ValidationError: If the payload violates the
            wire contract. The error names the offending path.
    """
    _checked(_INTERRUPT_RESPONSES, responses)
    # The schema has vouched for the shape; the cast tells the type checker.
    return [
        {"interruptResponse": {"interruptId": interrupt_id, "response": response}}
        for interrupt_id, response in cast(dict, responses).items()
    ]


def file_event(name: str, data: bytes) -> dict:
    """
    Build a `file` wire event, which Welt uploads to the Slack thread.

    `renderable_events` emits these for the files the model returns and the
    files of the tools the agent names; this builds the same event from
    arbitrary bytes, for the files the host app attaches itself.

    Args:
        name (str): The upload filename, extension included.
        data (bytes): The raw file bytes.

    Returns:
        dict: The `file` event (name plus base64 bytes).

    Raises:
        jsonschema.exceptions.ValidationError: If the event would not be
            one Welt renders — a nameless file, which it drops.
    """
    event = {"file": {"name": name, "bytes": base64.b64encode(data).decode("ascii")}}
    _checked(_FILE, event["file"])
    return event


def interrupt_reason(
    message: str,
    options: Sequence[dict] | None = None,
    *,
    input: dict | None = None,
) -> dict:
    """
    Build an interrupt reason that Welt renders as the specified widgets.

    Welt renders this shape as `message` followed by one button per option
    (`options`), a free-text field whose submitted text becomes the
    interrupt's response (`input`), or both — whichever answer comes
    first, a pressed button or the submitted text, settles the question.
    Both widget specs are the wire's own shapes; building them through
    this helper checks the result against Welt's published schema, so a
    typo raises here instead of reaching the thread as Welt's default
    rendering — which is what a reason it cannot match falls back to,
    silently.

    Args:
        message (str): The text Welt shows above the widgets.
        options (Sequence[dict] | None): One dict per button: a required
            `value` (what the interrupting tool receives as the response
            when the button is pressed), an optional `label` (the button
            text; omitted, Welt shows the value), and an optional `style`
            ("primary" or "danger").
        input (dict | None): The free-text field: an optional `label` (the
            field's label) and an optional `multiline` (whether the field
            accepts multiple lines) — `{}` takes Welt's defaults for both.
            None omits the field.

    Returns:
        dict: The reason to pass to `ToolContext.interrupt`.

    Raises:
        jsonschema.exceptions.ValidationError: If the reason would not be
            one Welt renders as widgets.
    """
    reason: dict = {"message": message}
    if options is not None:
        reason["options"] = list(options)
    if input is not None:
        reason["input"] = input
    _checked(_STRUCTURED_REASON, reason)
    return reason


class _Agent(Protocol):
    """What `renderable_events` reads from the Agent being streamed.

    Strands' Agent is not a dependency of this package — an adapter that
    imports the framework to read one attribute off it costs its callers a
    dependency to say what a line of code already says. This names the
    attribute instead, and an Agent satisfies it.
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

    Args:
        events (AsyncIterator[dict]): Raw `stream_async` events.
        agent (_Agent | None): The Agent being streamed, whose messages
            name the tool behind each tool result — the only place the name
            survives a resume, where the stream carries the result alone.
            Required with `files_from`.
        files_from (Collection[str] | None): The names of the tools whose
            files become `file` events. None takes files from none of them.

    Yields:
        dict: A `data` event per text chunk, a `current_tool_use` event per
            tool-use update, and per completed tool a `tool_result` event —
            slimmed to the toolUseId and status, so text tool output stays
            off the wire — followed by a `file` event (filename plus base64
            bytes, which Welt uploads to the Slack thread) per image,
            document, or video block a tool listed in `files_from`
            returned. Such blocks in the assistant message itself become
            `file` events unconditionally. A stream that stops for human
            input ends with an `interrupt` event per pending interrupt (its
            id, name, and reason, which Welt renders as buttons in the
            Slack thread).

    Raises:
        ValueError: If `files_from` is given without an agent, which
            leaves the tool behind a file unknowable.
    """
    if files_from is not None and agent is None:
        raise ValueError("files_from needs the agent the tool names come from")
    async for event in events:
        if "data" in event:
            yield {"data": event["data"]}
        elif "current_tool_use" in event:
            yield {"current_tool_use": event["current_tool_use"]}
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
            event = _file_event_from_block(block)
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
        if _emits_files(agent, files_from, tool_result["toolUseId"]):
            events.extend(
                event
                for result_block in tool_result["content"]
                if (event := _file_event_from_block(result_block)) is not None
            )
    return events


def _emits_files(
    agent: _Agent | None, files_from: Collection[str] | None, tool_use_id: str
) -> bool:
    """
    Tell whether the tool behind a tool result is one to take files from.

    Args:
        agent (_Agent | None): The Agent being streamed.
        files_from (Collection[str] | None): The names of the tools whose
            files become `file` events.
        tool_use_id (str): The `toolUseId` of the tool result.

    Returns:
        bool: Whether the tool's files belong on the wire.
    """
    if not files_from or agent is None:
        return False
    return _tool_name(agent, tool_use_id) in files_from


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


def _file_event_from_block(block: dict) -> dict | None:
    """
    Build a `file` event from a content block carrying raw file bytes.

    Args:
        block (dict): A Converse content block (from a toolResult or an
            assistant message).

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
        return None if data is None else file_event(_file_name(kind, media), data)
    return None


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
