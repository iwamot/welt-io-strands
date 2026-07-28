import base64
import binascii

import pytest
from jsonschema.exceptions import ValidationError

from welt_io_strands import decode_messages


def encoded(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def user_message(*content: object) -> dict:
    return {"role": "user", "content": list(content)}


def image_block(raw: bytes = b"img") -> dict:
    return {"image": {"format": "png", "source": {"bytes": encoded(raw)}}}


def document_block(name: str = "report") -> dict:
    return {
        "document": {
            "format": "pdf",
            "name": name,
            "source": {"bytes": encoded(b"doc")},
        }
    }


def test_decodes_image_document_and_video_blocks() -> None:
    messages = [
        user_message(
            image_block(),
            document_block(),
            {"video": {"format": "mp4", "source": {"bytes": encoded(b"vid")}}},
        )
    ]

    decoded = decode_messages(messages)

    assert decoded == [
        {
            "role": "user",
            "content": [
                {"image": {"format": "png", "source": {"bytes": b"img"}}},
                {
                    "document": {
                        "format": "pdf",
                        "name": "report",
                        "source": {"bytes": b"doc"},
                    }
                },
                {"video": {"format": "mp4", "source": {"bytes": b"vid"}}},
            ],
        }
    ]


def test_decodes_across_multiple_messages() -> None:
    messages = [user_message(image_block(b"a")), user_message(image_block(b"b"))]

    decoded = decode_messages(messages)

    assert [
        message["content"][0]["image"]["source"]["bytes"] for message in decoded
    ] == [b"a", b"b"]


def test_leaves_input_untouched() -> None:
    messages = [user_message(image_block())]

    decode_messages(messages)

    assert messages[0]["content"][0]["image"]["source"]["bytes"] == encoded(b"img")


def test_leaves_text_blocks_alone() -> None:
    messages = [
        user_message({"text": "<@U1>: hello"}),
        {"role": "assistant", "content": [{"text": "hi"}]},
    ]

    assert decode_messages(messages) == messages


@pytest.mark.parametrize(
    "messages",
    [
        [],
        "not a list",
        ["not a dict"],
        [{"role": "system", "content": [{"text": "hi"}]}],
        [{"role": "assistant", "content": [{"text": "hi"}]}],
        [{"role": "user", "content": "not a list"}],
        [{"role": "user", "content": []}],
        [user_message({"toolUse": {}})],
        [user_message({"text": 12})],
        [user_message({"text": ""})],
        [{"role": "assistant", "content": [image_block()]}],
        [user_message({"image": {"format": "avif", "source": {"bytes": "aW1n"}}})],
        [user_message({"image": {"source": {"bytes": "aW1n"}}})],
        [user_message({"document": {"format": "pdf", "source": {"bytes": "ZG9j"}}})],
        [user_message(document_block(name=""))],
        [user_message(document_block(name="report.pdf"))],
        [user_message({"image": {"format": "png", "source": {}}})],
        [user_message({"image": {"format": "png", "source": {"bytes": ""}}})],
        [user_message({"image": {"format": "png", "source": {"bytes": 12}}})],
    ],
)
def test_rejects_a_payload_that_violates_the_wire_contract(messages: object) -> None:
    with pytest.raises(ValidationError):
        decode_messages(messages)


def test_the_error_names_the_block_that_violates_the_contract() -> None:
    block = {"image": {"format": "png", "source": {"bytes": ""}}}
    messages = [user_message({"text": "<@U1>: hi"}), user_message(block)]

    with pytest.raises(ValidationError) as caught:
        decode_messages(messages)

    assert caught.value.json_path == "$[1].content[0].image.source.bytes"
    assert caught.value.message == "'' should be non-empty"


def test_bytes_the_schema_vouched_for_but_base64_refuses_raise() -> None:
    block = {"image": {"format": "png", "source": {"bytes": "!!!SGVs%%%bG8="}}}

    with pytest.raises(binascii.Error):
        decode_messages([user_message(block)])
