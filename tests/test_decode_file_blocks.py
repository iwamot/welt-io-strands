import base64

from welt_io import decode_file_blocks


def encoded(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def test_decodes_image_document_and_video_blocks() -> None:
    image_source: dict[str, object] = {"bytes": encoded(b"img")}
    document_source: dict[str, object] = {"bytes": encoded(b"doc")}
    video_source: dict[str, object] = {"bytes": encoded(b"vid")}
    messages = [
        {
            "role": "user",
            "content": [
                {"image": {"format": "png", "source": image_source}},
                {"document": {"format": "pdf", "source": document_source}},
                {"video": {"format": "mp4", "source": video_source}},
            ],
        }
    ]

    decode_file_blocks(messages)

    assert image_source["bytes"] == b"img"
    assert document_source["bytes"] == b"doc"
    assert video_source["bytes"] == b"vid"


def test_decodes_across_multiple_messages() -> None:
    first_source: dict[str, object] = {"bytes": encoded(b"a")}
    second_source: dict[str, object] = {"bytes": encoded(b"b")}
    messages = [
        {"role": "user", "content": [{"image": {"source": first_source}}]},
        {"role": "user", "content": [{"image": {"source": second_source}}]},
    ]

    decode_file_blocks(messages)

    assert first_source["bytes"] == b"a"
    assert second_source["bytes"] == b"b"


def test_leaves_text_blocks_alone() -> None:
    messages = [{"role": "user", "content": [{"text": "hello"}]}]

    decode_file_blocks(messages)

    assert messages == [{"role": "user", "content": [{"text": "hello"}]}]


def test_no_op_on_empty_messages() -> None:
    messages: list = []

    decode_file_blocks(messages)

    assert messages == []


def test_skips_non_dict_message() -> None:
    messages = ["not a dict"]

    decode_file_blocks(messages)

    assert messages == ["not a dict"]


def test_skips_non_list_content() -> None:
    messages = [{"role": "user", "content": "not a list"}]

    decode_file_blocks(messages)

    assert messages == [{"role": "user", "content": "not a list"}]


def test_skips_non_dict_block() -> None:
    messages = [{"role": "user", "content": ["not a dict"]}]

    decode_file_blocks(messages)

    assert messages == [{"role": "user", "content": ["not a dict"]}]


def test_skips_non_dict_media() -> None:
    messages = [{"role": "user", "content": [{"image": "not a dict"}]}]

    decode_file_blocks(messages)

    assert messages == [{"role": "user", "content": [{"image": "not a dict"}]}]


def test_skips_non_dict_source() -> None:
    messages = [{"role": "user", "content": [{"image": {"source": "not a dict"}}]}]

    decode_file_blocks(messages)

    assert messages == [
        {"role": "user", "content": [{"image": {"source": "not a dict"}}]}
    ]


def test_skips_bytes_that_are_not_str() -> None:
    source: dict[str, object] = {"bytes": b"raw"}
    messages = [{"role": "user", "content": [{"image": {"source": source}}]}]

    decode_file_blocks(messages)

    assert source["bytes"] == b"raw"
