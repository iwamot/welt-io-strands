import base64

import pytest

from welt_io_strands import decode_file_blocks


def encoded(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def image_block(source: dict) -> dict:
    return {"image": {"format": "png", "source": source}}


def test_warns_deprecation_and_decodes_in_place() -> None:
    source: dict[str, object] = {"bytes": encoded(b"img")}
    messages = [{"role": "user", "content": [image_block(source)]}]

    with pytest.warns(DeprecationWarning, match="use decode_messages"):
        decode_file_blocks(messages)

    assert source["bytes"] == b"img"


def test_leaves_the_input_untouched_when_a_later_block_violates_the_contract() -> None:
    source: dict[str, object] = {"bytes": encoded(b"img")}
    messages = [
        {
            "role": "user",
            "content": [image_block(source), {"image": {"format": "png"}}],
        }
    ]

    with (
        pytest.warns(DeprecationWarning),
        pytest.raises(TypeError, match="needs a source dict"),
    ):
        decode_file_blocks(messages)

    assert source["bytes"] == encoded(b"img")
