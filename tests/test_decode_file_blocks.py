import base64

import pytest

from welt_io import decode_file_blocks


def test_warns_deprecation_and_decodes_in_place() -> None:
    source: dict[str, object] = {"bytes": base64.b64encode(b"img").decode()}
    messages = [{"role": "user", "content": [{"image": {"source": source}}]}]

    with pytest.warns(DeprecationWarning, match="use decode_messages"):
        decode_file_blocks(messages)

    assert source["bytes"] == b"img"
