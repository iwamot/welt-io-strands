import base64

import pytest

from welt_io_strands import decode_messages


def encoded(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def user_message(*content: object) -> dict:
    return {"role": "user", "content": list(content)}


def image_block(raw: bytes = b"img") -> dict:
    return {"image": {"format": "png", "source": {"bytes": encoded(raw)}}}


def test_decodes_image_document_and_video_blocks() -> None:
    messages = [
        user_message(
            image_block(),
            {
                "document": {
                    "format": "pdf",
                    "name": "report",
                    "source": {"bytes": encoded(b"doc")},
                }
            },
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
        user_message({"text": "hello"}),
        {"role": "assistant", "content": [{"text": "hi"}]},
    ]

    assert decode_messages(messages) == messages


def test_no_op_on_empty_messages() -> None:
    assert decode_messages([]) == []


def test_passes_an_unknown_format_token_through() -> None:
    messages = [
        user_message({"image": {"format": "avif", "source": {"bytes": encoded(b"x")}}})
    ]

    decoded = decode_messages(messages)

    assert decoded[0]["content"][0]["image"]["format"] == "avif"


def test_rejects_messages_that_are_not_a_list() -> None:
    with pytest.raises(TypeError, match="messages must be a list, got dict"):
        decode_messages({"role": "user"})


def test_rejects_a_message_that_is_not_a_dict() -> None:
    with pytest.raises(TypeError, match="a message must be a dict, got 'nope'"):
        decode_messages(["nope"])


def test_rejects_an_unknown_role() -> None:
    with pytest.raises(ValueError, match="role must be 'user' or 'assistant'"):
        decode_messages([{"role": "system", "content": [{"text": "hi"}]}])


def test_rejects_content_that_is_not_a_list() -> None:
    with pytest.raises(TypeError, match="content must be a list, got 'hi'"):
        decode_messages([{"role": "user", "content": "hi"}])


def test_rejects_a_block_that_is_not_a_dict() -> None:
    with pytest.raises(TypeError, match="a content block must be a dict, got 'hi'"):
        decode_messages([user_message("hi")])


def test_rejects_text_that_is_not_a_str() -> None:
    with pytest.raises(TypeError, match="text must be a str, got 12"):
        decode_messages([user_message({"text": 12})])


def test_rejects_a_block_carrying_none_of_the_known_keys() -> None:
    with pytest.raises(ValueError, match=r"got the keys \['toolUse'\]"):
        decode_messages([user_message({"toolUse": {}})])


def test_rejects_a_file_block_in_an_assistant_message() -> None:
    messages = [{"role": "assistant", "content": [image_block()]}]

    with pytest.raises(ValueError, match="assistant message carries text blocks only"):
        decode_messages(messages)


def test_rejects_media_that_is_not_a_dict() -> None:
    with pytest.raises(TypeError, match="an image block must be a dict, got 'hi'"):
        decode_messages([user_message({"image": "hi"})])


def test_rejects_a_missing_format() -> None:
    with pytest.raises(TypeError, match="a video block needs a format, got None"):
        decode_messages([user_message({"video": {"source": {"bytes": encoded(b"v")}}})])


def test_rejects_a_document_without_a_name() -> None:
    block = {"document": {"format": "pdf", "source": {"bytes": encoded(b"d")}}}

    with pytest.raises(TypeError, match="a document block needs a name, got None"):
        decode_messages([user_message(block)])


def test_rejects_a_document_whose_name_is_empty() -> None:
    block = {
        "document": {"format": "pdf", "name": "", "source": {"bytes": encoded(b"d")}}
    }

    with pytest.raises(ValueError, match="name must not be empty"):
        decode_messages([user_message(block)])


def test_rejects_a_source_that_is_not_a_dict() -> None:
    block = {"image": {"format": "png", "source": "hi"}}

    with pytest.raises(TypeError, match="an image block needs a source dict"):
        decode_messages([user_message(block)])


def test_rejects_bytes_that_are_not_a_str() -> None:
    block = {"image": {"format": "png", "source": {"bytes": b"raw"}}}

    with pytest.raises(TypeError, match="an image block needs base64 source.bytes"):
        decode_messages([user_message(block)])


def test_rejects_empty_bytes() -> None:
    block = {"image": {"format": "png", "source": {"bytes": ""}}}

    with pytest.raises(ValueError, match="source.bytes must not be empty"):
        decode_messages([user_message(block)])


def test_rejects_bytes_that_are_not_valid_base64() -> None:
    block = {"image": {"format": "png", "source": {"bytes": "!!!SGVs%%%bG8="}}}

    with pytest.raises(ValueError, match="source.bytes is not valid base64"):
        decode_messages([user_message(block)])
