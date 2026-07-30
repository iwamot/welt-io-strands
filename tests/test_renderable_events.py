import asyncio
import base64
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest
from strands.agent import AgentResult
from strands.interrupt import Interrupt
from strands.telemetry import EventLoopMetrics

from welt_io_strands import renderable_events

PNG_BYTES = b"\x89PNG\r\n\x1a\n"
PNG_BASE64 = base64.b64encode(PNG_BYTES).decode("ascii")


@dataclass
class FakeAgent:
    """The one attribute renderable_events reads off a Strands Agent."""

    messages: list = field(default_factory=list)


def agent_calling(tool_use_id: str, name: str) -> FakeAgent:
    """Build an agent whose messages name one tool use."""
    return FakeAgent(
        messages=[
            {"role": "user", "content": [{"text": "make me one"}]},
            {
                "role": "assistant",
                "content": [
                    {"text": "on it"},
                    {"toolUse": {"toolUseId": tool_use_id, "name": name, "input": {}}},
                ],
            },
        ]
    )


def result_of(*interrupts: Interrupt) -> AgentResult:
    """Build the AgentResult that ends a stream."""
    return AgentResult(
        stop_reason="interrupt" if interrupts else "end_turn",
        message={"role": "assistant", "content": []},
        metrics=EventLoopMetrics(),
        state={},
        interrupts=list(interrupts) or None,
    )


def rendered(
    events: list,
    *,
    agent: FakeAgent | None = None,
    files_from: set[str] | None = None,
) -> list[dict]:
    async def source() -> AsyncIterator[dict]:
        for event in events:
            yield event

    async def gather() -> list[dict]:
        return [
            event
            async for event in renderable_events(
                source(), agent=agent, files_from=files_from
            )
        ]

    return asyncio.run(gather())


def test_data_event_is_reduced_to_its_data_key() -> None:
    events = [{"data": "hello", "delta": {"text": "hello"}, "agent": object()}]

    assert rendered(events) == [{"data": "hello"}]


def test_current_tool_use_event_keeps_only_the_name_and_id() -> None:
    tool_use = {"toolUseId": "id-1", "name": "current_time", "input": '{"city":'}
    events = [{"current_tool_use": tool_use, "agent": object()}]

    assert rendered(events) == [
        {"current_tool_use": {"name": "current_time", "toolUseId": "id-1"}}
    ]


def test_a_tool_use_the_stream_has_not_named_yet_carries_what_it_has() -> None:
    events = [{"current_tool_use": {"toolUseId": "id-1", "input": ""}}]

    assert rendered(events) == [{"current_tool_use": {"toolUseId": "id-1"}}]


def test_an_empty_data_chunk_carries_nothing_to_render() -> None:
    events = [{"data": "", "delta": {"text": ""}}, {"data": "hi"}]

    assert rendered(events) == [{"data": "hi"}]


def test_data_takes_precedence_over_current_tool_use() -> None:
    events = [{"data": "hello", "current_tool_use": {"toolUseId": "id-1"}}]

    assert rendered(events) == [{"data": "hello"}]


def test_tool_result_message_is_slimmed_to_id_and_status() -> None:
    events = [
        {
            "message": {
                "role": "user",
                "content": [
                    {
                        "toolResult": {
                            "toolUseId": "id-1",
                            "status": "success",
                            "content": [{"text": "arbitrarily large output"}],
                        }
                    }
                ],
            }
        }
    ]

    assert rendered(events) == [
        {"tool_result": {"toolUseId": "id-1", "status": "success"}}
    ]


def test_yields_one_tool_result_per_block() -> None:
    events = [
        {
            "message": {
                "role": "user",
                "content": [
                    {
                        "toolResult": {
                            "toolUseId": "id-1",
                            "status": "success",
                            "content": [],
                        }
                    },
                    {
                        "toolResult": {
                            "toolUseId": "id-2",
                            "status": "error",
                            "content": [],
                        }
                    },
                ],
            }
        }
    ]

    assert rendered(events) == [
        {"tool_result": {"toolUseId": "id-1", "status": "success"}},
        {"tool_result": {"toolUseId": "id-2", "status": "error"}},
    ]


def test_model_message_without_tool_results_yields_nothing() -> None:
    events = [{"message": {"role": "assistant", "content": [{"text": "hi"}]}}]

    assert rendered(events) == []


def test_tool_result_image_becomes_a_file_event_after_the_tool_result() -> None:
    events = [
        {
            "message": {
                "role": "user",
                "content": [
                    {
                        "toolResult": {
                            "toolUseId": "id-1",
                            "status": "success",
                            "content": [
                                {"text": "chart rendered"},
                                {
                                    "image": {
                                        "format": "png",
                                        "source": {"bytes": PNG_BYTES},
                                    }
                                },
                            ],
                        }
                    }
                ],
            }
        }
    ]

    assert rendered(
        events, agent=agent_calling("id-1", "draw"), files_from={"draw"}
    ) == [
        {"tool_result": {"toolUseId": "id-1", "status": "success"}},
        {"file": {"name": "image.png", "bytes": PNG_BASE64}},
    ]


def test_tool_result_document_file_is_named_from_its_name_and_format() -> None:
    events = [
        {
            "message": {
                "role": "user",
                "content": [
                    {
                        "toolResult": {
                            "toolUseId": "id-1",
                            "status": "success",
                            "content": [
                                {
                                    "document": {
                                        "format": "pdf",
                                        "name": "report",
                                        "source": {"bytes": b"%PDF-"},
                                    }
                                }
                            ],
                        }
                    }
                ],
            }
        }
    ]

    assert rendered(
        events, agent=agent_calling("id-1", "write"), files_from={"write"}
    ) == [
        {"tool_result": {"toolUseId": "id-1", "status": "success"}},
        {
            "file": {
                "name": "report.pdf",
                "bytes": base64.b64encode(b"%PDF-").decode("ascii"),
            }
        },
    ]


def test_assistant_message_image_becomes_a_file_event() -> None:
    events = [
        {
            "message": {
                "role": "assistant",
                "content": [
                    {"text": "here you go"},
                    {"image": {"format": "png", "source": {"bytes": PNG_BYTES}}},
                ],
            }
        }
    ]

    assert rendered(events) == [{"file": {"name": "image.png", "bytes": PNG_BASE64}}]


def test_three_gp_format_maps_to_3gp_extension() -> None:
    events = [
        {
            "message": {
                "role": "assistant",
                "content": [
                    {"video": {"format": "three_gp", "source": {"bytes": b"\x00"}}}
                ],
            }
        }
    ]

    assert rendered(events) == [
        {"file": {"name": "video.3gp", "bytes": base64.b64encode(b"\x00").decode()}}
    ]


def test_document_without_a_name_or_format_is_named_after_its_kind() -> None:
    events = [
        {
            "message": {
                "role": "assistant",
                "content": [{"document": {"source": {"bytes": b"raw"}}}],
            }
        }
    ]

    assert rendered(events) == [
        {"file": {"name": "document", "bytes": base64.b64encode(b"raw").decode()}}
    ]


def test_a_file_the_source_only_points_at_stays_off_the_wire() -> None:
    events = [
        {
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "video": {
                            "format": "mp4",
                            "source": {"s3Location": {"uri": "s3://bucket/key"}},
                        }
                    }
                ],
            }
        }
    ]

    assert rendered(events) == []


# --- which tools the agent uploads from --------------------------------------


def tool_result_with_a_file(tool_use_id: str = "id-1") -> list[dict]:
    """Build the stream of a tool that returned one image."""
    return [
        {
            "message": {
                "role": "user",
                "content": [
                    {
                        "toolResult": {
                            "toolUseId": tool_use_id,
                            "status": "success",
                            "content": [
                                {
                                    "image": {
                                        "format": "png",
                                        "source": {"bytes": PNG_BYTES},
                                    }
                                }
                            ],
                        }
                    }
                ],
            }
        }
    ]


def test_files_of_a_tool_left_out_of_files_from_stay_off_the_wire() -> None:
    result = rendered(
        tool_result_with_a_file(),
        agent=agent_calling("id-1", "file_read"),
        files_from={"draw"},
    )

    assert result == [{"tool_result": {"toolUseId": "id-1", "status": "success"}}]


def test_tool_files_stay_off_the_wire_without_files_from() -> None:
    result = rendered(tool_result_with_a_file())

    assert result == [{"tool_result": {"toolUseId": "id-1", "status": "success"}}]


def test_files_from_without_an_agent_is_an_error() -> None:
    with pytest.raises(ValueError, match="agent"):
        rendered(tool_result_with_a_file(), files_from={"draw"})


def test_an_empty_files_from_needs_no_agent() -> None:
    result = rendered(tool_result_with_a_file(), files_from=set())

    assert result == [{"tool_result": {"toolUseId": "id-1", "status": "success"}}]


def test_a_file_with_no_bytes_stays_off_the_wire() -> None:
    events = [
        {
            "message": {
                "role": "assistant",
                "content": [
                    {"image": {"format": "png", "source": {"bytes": b""}}},
                ],
            }
        }
    ]

    assert rendered(events) == []


def test_tool_use_the_agent_does_not_know_uploads_nothing() -> None:
    result = rendered(
        tool_result_with_a_file("id-other"),
        agent=agent_calling("id-1", "draw"),
        files_from={"draw"},
    )

    assert result == [{"tool_result": {"toolUseId": "id-other", "status": "success"}}]


def test_result_interrupt_becomes_an_interrupt_event() -> None:
    interrupt = Interrupt(id="i-1", name="deploy-approval", reason="Deploy?")

    assert rendered([{"result": result_of(interrupt)}]) == [
        {"interrupt": {"id": "i-1", "name": "deploy-approval", "reason": "Deploy?"}}
    ]


def test_yields_one_interrupt_event_per_interrupt() -> None:
    events = [
        {
            "result": result_of(
                Interrupt(id="i-1", name="approval-a", reason="A?"),
                Interrupt(id="i-2", name="approval-b", reason="B?"),
            )
        }
    ]

    assert rendered(events) == [
        {"interrupt": {"id": "i-1", "name": "approval-a", "reason": "A?"}},
        {"interrupt": {"id": "i-2", "name": "approval-b", "reason": "B?"}},
    ]


def test_interrupt_reason_is_passed_through_unmodified() -> None:
    reason = {
        "message": "Deploy to prod?",
        "options": [{"value": "approve", "label": "Deploy", "style": "primary"}],
        "extra": {"nested": [1, {"deep": True}]},
    }
    interrupt = Interrupt(id="i-1", name="deploy-approval", reason=reason)

    rendered_reason = rendered([{"result": result_of(interrupt)}])[0]["interrupt"][
        "reason"
    ]

    assert rendered_reason is reason


def test_interrupt_without_reason_keeps_none() -> None:
    events = [{"result": result_of(Interrupt(id="i-1", name="n"))}]

    assert rendered(events) == [
        {"interrupt": {"id": "i-1", "name": "n", "reason": None}}
    ]


def test_result_without_interrupts_yields_nothing() -> None:
    assert rendered([{"result": result_of()}]) == []


def test_unrenderable_events_are_dropped() -> None:
    events = [{"init_event_loop": True}, {"delta": {"text": "chunk"}}]

    assert rendered(events) == []


def test_stream_order_is_preserved() -> None:
    events = [
        {"data": "a"},
        {"current_tool_use": {"toolUseId": "id-1"}},
        {
            "message": {
                "role": "user",
                "content": [
                    {
                        "toolResult": {
                            "toolUseId": "id-1",
                            "status": "success",
                            "content": [],
                        }
                    }
                ],
            }
        },
        {"data": "b"},
    ]

    assert rendered(events) == [
        {"data": "a"},
        {"current_tool_use": {"toolUseId": "id-1"}},
        {"tool_result": {"toolUseId": "id-1", "status": "success"}},
        {"data": "b"},
    ]
