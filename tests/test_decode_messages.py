import base64

from welt_io import decode_messages


def encoded(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def test_decodes_image_document_and_video_blocks() -> None:
    messages = [
        {
            "role": "user",
            "content": [
                {"image": {"format": "png", "source": {"bytes": encoded(b"img")}}},
                {"document": {"format": "pdf", "source": {"bytes": encoded(b"doc")}}},
                {"video": {"format": "mp4", "source": {"bytes": encoded(b"vid")}}},
            ],
        }
    ]

    decoded = decode_messages(messages)

    assert decoded == [
        {
            "role": "user",
            "content": [
                {"image": {"format": "png", "source": {"bytes": b"img"}}},
                {"document": {"format": "pdf", "source": {"bytes": b"doc"}}},
                {"video": {"format": "mp4", "source": {"bytes": b"vid"}}},
            ],
        }
    ]


def test_decodes_across_multiple_messages() -> None:
    messages = [
        {"role": "user", "content": [{"image": {"source": {"bytes": encoded(b"a")}}}]},
        {"role": "user", "content": [{"image": {"source": {"bytes": encoded(b"b")}}}]},
    ]

    decoded = decode_messages(messages)

    assert decoded == [
        {"role": "user", "content": [{"image": {"source": {"bytes": b"a"}}}]},
        {"role": "user", "content": [{"image": {"source": {"bytes": b"b"}}}]},
    ]


def test_leaves_input_untouched() -> None:
    source: dict[str, object] = {"bytes": encoded(b"img")}
    messages = [{"role": "user", "content": [{"image": {"source": source}}]}]

    decode_messages(messages)

    assert source["bytes"] == encoded(b"img")


def test_leaves_text_blocks_alone() -> None:
    messages = [{"role": "user", "content": [{"text": "hello"}]}]

    decoded = decode_messages(messages)

    assert decoded == [{"role": "user", "content": [{"text": "hello"}]}]


def test_no_op_on_empty_messages() -> None:
    assert decode_messages([]) == []


def test_skips_non_dict_message() -> None:
    assert decode_messages(["not a dict"]) == ["not a dict"]


def test_skips_non_list_content() -> None:
    messages = [{"role": "user", "content": "not a list"}]

    assert decode_messages(messages) == messages


def test_skips_non_dict_block() -> None:
    messages = [{"role": "user", "content": ["not a dict"]}]

    assert decode_messages(messages) == messages


def test_skips_non_dict_media() -> None:
    messages = [{"role": "user", "content": [{"image": "not a dict"}]}]

    assert decode_messages(messages) == messages


def test_skips_non_dict_source() -> None:
    messages = [{"role": "user", "content": [{"image": {"source": "not a dict"}}]}]

    assert decode_messages(messages) == messages


def test_skips_bytes_that_are_not_str() -> None:
    messages = [{"role": "user", "content": [{"image": {"source": {"bytes": b"raw"}}}]}]

    assert decode_messages(messages) == messages
