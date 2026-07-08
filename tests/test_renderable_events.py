import asyncio
import base64
from collections.abc import AsyncIterator

from welt_io import renderable_events

PNG_BYTES = b"\x89PNG\r\n\x1a\n"
PNG_BASE64 = base64.b64encode(PNG_BYTES).decode("ascii")


def rendered(events: list) -> list[dict]:
    async def source() -> AsyncIterator[dict]:
        for event in events:
            yield event

    async def gather() -> list[dict]:
        return [event async for event in renderable_events(source())]

    return asyncio.run(gather())


def test_data_event_is_reduced_to_its_data_key() -> None:
    events = [{"data": "hello", "delta": {"text": "hello"}, "agent": object()}]

    assert rendered(events) == [{"data": "hello"}]


def test_current_tool_use_event_is_reduced_to_its_key() -> None:
    tool_use = {"toolUseId": "id-1", "name": "current_time", "input": {}}
    events = [{"current_tool_use": tool_use, "agent": object()}]

    assert rendered(events) == [{"current_tool_use": tool_use}]


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
                    {"toolResult": {"toolUseId": "id-1", "status": "success"}},
                    {"toolResult": {"toolUseId": "id-2", "status": "error"}},
                ],
            }
        }
    ]

    assert rendered(events) == [
        {"tool_result": {"toolUseId": "id-1", "status": "success"}},
        {"tool_result": {"toolUseId": "id-2", "status": "error"}},
    ]


def test_missing_tool_result_fields_become_none() -> None:
    events = [{"message": {"role": "user", "content": [{"toolResult": {}}]}}]

    assert rendered(events) == [{"tool_result": {"toolUseId": None, "status": None}}]


def test_model_message_without_tool_results_yields_nothing() -> None:
    events = [{"message": {"role": "assistant", "content": [{"text": "hi"}]}}]

    assert rendered(events) == []


def test_non_dict_message_yields_nothing() -> None:
    assert rendered([{"message": "not a dict"}]) == []


def test_non_list_message_content_yields_nothing() -> None:
    assert rendered([{"message": {"role": "user", "content": "not a list"}}]) == []


def test_non_dict_content_block_is_skipped() -> None:
    assert rendered([{"message": {"role": "user", "content": ["not a dict"]}}]) == []


def test_non_dict_tool_result_is_skipped() -> None:
    events = [{"message": {"role": "user", "content": [{"toolResult": "not a dict"}]}}]

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

    assert rendered(events) == [
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

    assert rendered(events) == [
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


def test_file_block_without_format_gets_no_extension() -> None:
    events = [
        {
            "message": {
                "role": "assistant",
                "content": [{"image": {"source": {"bytes": PNG_BYTES}}}],
            }
        }
    ]

    assert rendered(events) == [{"file": {"name": "image", "bytes": PNG_BASE64}}]


def test_file_block_without_raw_bytes_yields_nothing() -> None:
    events = [
        {
            "message": {
                "role": "assistant",
                "content": [
                    {"image": {"format": "png", "source": {"bytes": "not raw bytes"}}},
                    {"image": {"format": "png", "source": "not a dict"}},
                    {
                        "video": {
                            "format": "mp4",
                            "source": {"s3Location": {"uri": "s3://bucket/key"}},
                        }
                    },
                ],
            }
        }
    ]

    assert rendered(events) == []


def test_non_dict_tool_result_content_block_is_skipped() -> None:
    events = [
        {
            "message": {
                "role": "user",
                "content": [
                    {
                        "toolResult": {
                            "toolUseId": "id-1",
                            "status": "success",
                            "content": ["not a dict"],
                        }
                    }
                ],
            }
        }
    ]

    assert rendered(events) == [
        {"tool_result": {"toolUseId": "id-1", "status": "success"}}
    ]


def test_unrenderable_events_are_dropped() -> None:
    events = [
        {"init_event_loop": True},
        {"delta": {"text": "chunk"}},
        {"result": object()},
    ]

    assert rendered(events) == []


def test_stream_order_is_preserved() -> None:
    events = [
        {"data": "a"},
        {"current_tool_use": {"toolUseId": "id-1"}},
        {
            "message": {
                "role": "user",
                "content": [{"toolResult": {"toolUseId": "id-1", "status": "success"}}],
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
