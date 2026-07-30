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


def test_no_answers_decode_to_no_items() -> None:
    assert decode_interrupt_responses({}) == []
