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
  generated files base64-encoded — the inbound encoding in reverse.
  `file_event` builds the same `file` event from a name and raw bytes, so
  agents can attach files of their own alongside the reduced stream.
  `interrupt_reason` builds the reason shape Welt renders as a message with
  buttons, a free-text field, or both when a tool interrupts for human
  input.
"""

import base64
import copy
import warnings
from collections.abc import AsyncIterator, Sequence

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
    """
    decoded = copy.deepcopy(messages)
    _restore_file_bytes(decoded)
    return decoded


def decode_file_blocks(messages: list) -> None:
    """
    Decode base64 image/document/video bytes back to raw bytes, in place.

    Deprecated: use `decode_messages`, which returns a decoded copy
    instead of mutating its input.

    Args:
        messages (list): The Converse-shaped messages from Welt's payload.

    Returns:
        None
    """
    warnings.warn(
        "decode_file_blocks is deprecated; use decode_messages, which returns"
        " a decoded copy instead of mutating its input",
        DeprecationWarning,
        stacklevel=2,
    )
    _restore_file_bytes(messages)


def _restore_file_bytes(messages: list) -> None:
    """
    Decode base64 image/document/video bytes back to raw bytes, in place.

    Args:
        messages (list): The Converse-shaped messages from Welt's payload.

    Returns:
        None
    """
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            for key in ("image", "document", "video"):
                media = block.get(key)
                if not isinstance(media, dict):
                    continue
                source = media.get("source")
                if isinstance(source, dict) and isinstance(source.get("bytes"), str):
                    source["bytes"] = base64.b64decode(source["bytes"])


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


def file_event(name: str, data: bytes) -> dict:
    """
    Build a `file` wire event, which Welt uploads to the Slack thread.

    `renderable_events` emits these for the files a tool or the model
    generates; this builds the same event from arbitrary bytes, for agents
    that attach files of their own alongside the reduced stream.

    Args:
        name (str): The upload filename, extension included.
        data (bytes): The raw file bytes.

    Returns:
        dict: The `file` event (name plus base64 bytes).

    Raises:
        ValueError: If the name is empty (Welt drops a nameless file).
    """
    if not name:
        raise ValueError("name must not be empty")
    return {"file": {"name": name, "bytes": base64.b64encode(data).decode("ascii")}}


_BUTTON_STYLES = frozenset({"primary", "danger"})


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
    this helper turns a typo into an immediate ValueError instead of a
    silent fallback to Welt's default rendering.

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
        ValueError: If the message is empty, neither options nor input is
            given, or a widget spec is off — an unknown key, a missing
            value, an empty or non-string value/label, a style that is not
            "primary" or "danger", or a non-boolean multiline.
    """
    if not message:
        raise ValueError("message must not be empty")
    if options is None and input is None:
        raise ValueError("options or input must be given")
    reason: dict = {"message": message}
    if options is not None:
        reason["options"] = _built_options(options)
    if input is not None:
        reason["input"] = _built_input(input)
    return reason


_OPTION_KEYS = frozenset({"value", "label", "style"})


def _built_options(options: Sequence[dict]) -> list[dict]:
    """
    Validate and build the `options` entries of a structured reason.

    Only the keys the wire knows are passed through; an omitted label
    stays omitted, leaving its default (the value) to Welt.

    Args:
        options (Sequence[dict]): One dict per button: a required `value`,
            an optional `label`, and an optional `style`.

    Returns:
        list[dict]: The option dicts of the reason shape.

    Raises:
        ValueError: If no options are given, an option carries an unknown
            key, a value is missing, empty, or not a string, a label is
            empty or not a string, or a style is not "primary" or
            "danger".
    """
    if not options:
        raise ValueError("options must not be empty")
    built: list[dict] = []
    for option in options:
        unknown = set(option) - _OPTION_KEYS
        if unknown:
            raise ValueError(f"unknown option keys: {sorted(unknown)}")
        value = option.get("value")
        if not isinstance(value, str) or not value:
            raise ValueError("option value must be a non-empty string")
        entry: dict = {"value": value}
        if "label" in option:
            label = option["label"]
            if not isinstance(label, str) or not label:
                raise ValueError("option label must be a non-empty string")
            entry["label"] = label
        if "style" in option:
            style = option["style"]
            if style not in _BUTTON_STYLES:
                raise ValueError(f"style must be 'primary' or 'danger': {style!r}")
            entry["style"] = style
        built.append(entry)
    return built


_INPUT_KEYS = frozenset({"label", "multiline"})


def _built_input(input_spec: dict) -> dict:
    """
    Validate and build the `input` entry of a structured reason.

    Only the keys the wire knows are passed through; omitted ones stay
    omitted, leaving their defaults to Welt.

    Args:
        input_spec (dict): The field spec: an optional `label` and an
            optional `multiline`.

    Returns:
        dict: The `input` entry of the reason shape.

    Raises:
        ValueError: If the spec carries an unknown key, an empty or
            non-string label, or a non-boolean multiline.
    """
    unknown = set(input_spec) - _INPUT_KEYS
    if unknown:
        raise ValueError(f"unknown input keys: {sorted(unknown)}")
    built: dict = {}
    if "label" in input_spec:
        label = input_spec["label"]
        if not isinstance(label, str) or not label:
            raise ValueError("input label must be a non-empty string")
        built["label"] = label
    if "multiline" in input_spec:
        multiline = input_spec["multiline"]
        if not isinstance(multiline, bool):
            raise ValueError("input multiline must be a bool")
        built["multiline"] = multiline
    return built


async def renderable_events(events: AsyncIterator[dict]) -> AsyncIterator[dict]:
    """
    Reduce Strands `stream_async` events to the subset Welt renders.

    Args:
        events (AsyncIterator[dict]): Raw `stream_async` events.

    Yields:
        dict: A `data` event per text chunk, a `current_tool_use` event per
            tool-use update, and per completed tool a `tool_result` event —
            slimmed to the toolUseId and status, so text tool output stays
            off the wire — followed by a `file` event (filename plus base64
            bytes, which Welt uploads to the Slack thread) per image,
            document, or video block the tool returned. Such blocks in the
            assistant message itself become `file` events the same way.
            A stream that stops for human input ends with an `interrupt`
            event per pending interrupt (its id, name, and reason, which
            Welt renders as buttons in the Slack thread).
    """
    async for event in events:
        if "data" in event:
            yield {"data": event["data"]}
        elif "current_tool_use" in event:
            yield {"current_tool_use": event["current_tool_use"]}
        elif "message" in event:
            for rendered in _message_events(event["message"]):
                yield rendered
        elif "result" in event:
            for rendered in _interrupt_events(event["result"]):
                yield rendered


def _interrupt_events(result: object) -> list[dict]:
    """
    Serialize the interrupts of the stream's final result event.

    Strands ends the stream with the AgentResult; when the run stopped for
    human input, its `interrupts` carry one Interrupt per pending question.
    Each becomes an `interrupt` event — a faithful copy of the Interrupt's
    id, name, and reason, the reason passed through unmodified (it is any
    JSON-serializable value by Strands' contract, and interpreting it is
    the renderer's job). The usual result, without interrupts, yields
    nothing.

    Args:
        result (object): The `result` value of a stream event.

    Returns:
        list[dict]: One `interrupt` event per pending interrupt.
    """
    interrupts = getattr(result, "interrupts", None)
    if interrupts is None:
        return []
    return [
        {
            "interrupt": {
                "id": interrupt.id,
                "name": interrupt.name,
                "reason": interrupt.reason,
            }
        }
        for interrupt in interrupts
    ]


def _message_events(message: object) -> list[dict]:
    """
    Extract renderable events from a Strands message event.

    Strands adds tool results to the conversation as a message whose content
    blocks each carry a `toolResult`; a `tool_result` entry is slimmed to the
    toolUseId and status, followed by a `file` event per image/document/video
    block the tool returned. Model messages carry text (nothing to extract —
    it already streamed as `data` events) and, for models that generate files,
    image/document/video blocks, which become `file` events too.

    Args:
        message (object): The `message` value of a stream event.

    Returns:
        list[dict]: The `tool_result` and `file` events, in content order.
    """
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if not isinstance(content, list):
        return []
    events: list[dict] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        tool_result = block.get("toolResult")
        if isinstance(tool_result, dict):
            events.append(
                {
                    "tool_result": {
                        "toolUseId": tool_result.get("toolUseId"),
                        "status": tool_result.get("status"),
                    }
                }
            )
            result_content = tool_result.get("content")
            if isinstance(result_content, list):
                events.extend(
                    event
                    for result_block in result_content
                    if isinstance(result_block, dict)
                    and (event := _file_event_from_block(result_block)) is not None
                )
        else:
            event = _file_event_from_block(block)
            if event is not None:
                events.append(event)
    return events


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
        media = block.get(kind)
        if not isinstance(media, dict):
            continue
        source = media.get("source")
        data = source.get("bytes") if isinstance(source, dict) else None
        if not isinstance(data, bytes):
            continue
        return file_event(_file_name(kind, media), data)
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
    name = media.get("name")
    base = name if isinstance(name, str) and name else kind
    file_format = media.get("format")
    if not isinstance(file_format, str) or not file_format:
        return base
    return f"{base}.{_EXTENSION_BY_FORMAT.get(file_format, file_format)}"
