import asyncio
import base64
import logging
from collections.abc import AsyncIterator
from copy import deepcopy
from dataclasses import dataclass, field
from itertools import product

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


def test_stream_projection_preserves_order_multiplicity_and_public_fields() -> None:
    cases = (
        ({"data": "a", "delta": {"text": "a"}}, [{"data": "a"}]),
        ({"data": "日本語\n "}, [{"data": "日本語\n "}]),
        ({"data": ""}, []),
        ({"init_event_loop": True}, []),
        ({"delta": {"text": "chunk"}}, []),
        (
            {
                "current_tool_use": {
                    "toolUseId": "id-1",
                    "name": "current_time",
                    "input": '{"city":',
                }
            },
            [{"current_tool_use": {"toolUseId": "id-1", "name": "current_time"}}],
        ),
        (
            {"current_tool_use": {"toolUseId": "id-1", "input": ""}},
            [{"current_tool_use": {"toolUseId": "id-1"}}],
        ),
        ({"message": {"role": "assistant", "content": [{"text": "hi"}]}}, []),
        (
            {
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "toolResult": {
                                "toolUseId": "id-1",
                                "status": "success",
                                "content": [{"text": "large output"}],
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
            },
            [
                {"tool_result": {"toolUseId": "id-1", "status": "success"}},
                {"tool_result": {"toolUseId": "id-2", "status": "error"}},
            ],
        ),
    )
    for size in range(4):
        for selected in product(cases, repeat=size):
            events = [deepcopy(event) for event, _ in selected]
            before = deepcopy(events)
            expected = [item for _, output in selected for item in output]
            assert rendered(events) == expected, events
            assert events == before


def test_interrupt_events_preserve_all_ids_names_and_reasons() -> None:
    reasons = (
        None,
        "Deploy?",
        {
            "message": "Deploy to prod?",
            "options": [{"value": "approve", "label": "Deploy", "style": "primary"}],
            "extra": {"nested": [1, {"deep": True}]},
        },
    )
    for size in range(4):
        for selected in product(reasons, repeat=size):
            interrupts = [
                Interrupt(id=f"i-{i}", name=f"approval-{i}", reason=reason)
                for i, reason in enumerate(selected)
            ]
            expected = [
                {
                    "interrupt": {
                        "id": f"i-{i}",
                        "name": f"approval-{i}",
                        "reason": reason,
                    }
                }
                for i, reason in enumerate(selected)
            ]
            actual = rendered([{"result": result_of(*interrupts)}])
            assert actual == expected
            for event, reason in zip(actual, selected, strict=True):
                assert event["interrupt"]["reason"] is reason


def test_data_takes_precedence_over_current_tool_use() -> None:
    events = [{"data": "hello", "current_tool_use": {"toolUseId": "id-1"}}]

    assert rendered(events) == [{"data": "hello"}]


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
        {"file": {"name": "document.bin", "bytes": base64.b64encode(b"raw").decode()}}
    ]


def test_named_document_without_a_format_gets_a_bin_extension() -> None:
    events = [
        {
            "message": {
                "role": "assistant",
                "content": [
                    {"document": {"name": "notes", "source": {"bytes": b"raw"}}}
                ],
            }
        }
    ]

    assert rendered(events) == [
        {"file": {"name": "notes.bin", "bytes": base64.b64encode(b"raw").decode()}}
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


def tool_result_with_a_file(
    tool_use_id: str = "id-1", data: bytes = PNG_BYTES
) -> list[dict]:
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
                                        "source": {"bytes": data},
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


def test_an_empty_file_is_logged_against_the_tool_that_returned_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        result = rendered(
            tool_result_with_a_file(data=b""),
            agent=agent_calling("id-1", "draw"),
            files_from={"draw"},
        )

    assert result == [{"tool_result": {"toolUseId": "id-1", "status": "success"}}]
    assert "draw" in caplog.text
    assert "image.png" in caplog.text


def test_an_empty_file_from_the_model_is_logged_against_the_model(
    caplog: pytest.LogCaptureFixture,
) -> None:
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

    with caplog.at_level(logging.WARNING):
        assert rendered(events) == []

    assert "the model" in caplog.text


def test_tool_use_the_agent_does_not_know_uploads_nothing() -> None:
    result = rendered(
        tool_result_with_a_file("id-other"),
        agent=agent_calling("id-1", "draw"),
        files_from={"draw"},
    )

    assert result == [{"tool_result": {"toolUseId": "id-other", "status": "success"}}]
