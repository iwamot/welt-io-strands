"""Adapters for the two directions of Welt's wire contract.

The wire between Welt and the agent is JSON, and plain Strands values do not
fit it in either direction:

- Inbound, JSON cannot carry raw bytes, so Welt base64-encodes the `bytes`
  slot of the Converse image/document/video blocks it builds from Slack
  uploads. `decode_file_blocks` restores them before Strands (Bedrock
  Converse) sees the messages; without uploads it is a no-op.
- Outbound, raw `stream_async` events carry values that are not
  JSON-serializable (the Agent itself, UUIDs, traces, raw file bytes), which
  the AgentCore Runtime SDK would degrade to a plain string on the SSE wire.
  `renderable_events` reduces the stream to the events Welt renders, with
  generated files base64-encoded — the inbound encoding in reverse.
  `file_event` builds the same `file` event from a name and raw bytes, so
  agents can attach files of their own alongside the reduced stream.
"""

import base64
from collections.abc import AsyncIterator

try:
    from ._version import __version__
except ImportError:
    __version__ = "0.0.0+unknown"

__all__ = ["decode_file_blocks", "file_event", "renderable_events"]


def decode_file_blocks(messages: list) -> None:
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
    """
    return {"file": {"name": name, "bytes": base64.b64encode(data).decode("ascii")}}


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
    """
    async for event in events:
        if "data" in event:
            yield {"data": event["data"]}
        elif "current_tool_use" in event:
            yield {"current_tool_use": event["current_tool_use"]}
        elif "message" in event:
            for rendered in _message_events(event["message"]):
                yield rendered


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
