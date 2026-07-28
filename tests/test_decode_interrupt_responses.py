import pytest
from jsonschema.exceptions import ValidationError

from welt_io_strands import decode_interrupt_responses


def test_each_answer_becomes_an_interrupt_response_item() -> None:
    answers = {"i-1": "approve", "i-2": "n"}

    assert decode_interrupt_responses(answers) == [
        {"interruptResponse": {"interruptId": "i-1", "response": "approve"}},
        {"interruptResponse": {"interruptId": "i-2", "response": "n"}},
    ]


def test_answer_order_is_preserved() -> None:
    answers = {"i-2": "n", "i-1": "y"}

    decoded = decode_interrupt_responses(answers)

    assert [item["interruptResponse"]["interruptId"] for item in decoded] == [
        "i-2",
        "i-1",
    ]


@pytest.mark.parametrize(
    "responses",
    [
        {},
        [("i-1", "approve")],
        "not a mapping",
        {"i-1": 42},
        {"i-1": None},
        {"i-1": "y", "i-2": 42},
    ],
)
def test_rejects_a_payload_that_violates_the_wire_contract(responses: object) -> None:
    with pytest.raises(ValidationError):
        decode_interrupt_responses(responses)
