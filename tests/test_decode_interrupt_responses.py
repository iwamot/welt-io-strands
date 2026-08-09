from welt_io_strands import decode_interrupt_responses


def test_each_answer_becomes_an_interrupt_response_item() -> None:
    answers = {
        "i-1": {"value": "approve", "source": "option"},
        "i-2": {"value": "make it shorter", "source": "input"},
    }

    assert decode_interrupt_responses(answers) == [
        {"interruptResponse": {"interruptId": "i-1", "response": "approve"}},
        {
            "interruptResponse": {
                "interruptId": "i-2",
                "response": "make it shorter",
            }
        },
    ]


def test_an_answer_travels_on_as_the_value_it_was_given() -> None:
    answers = {
        "i-1": {"value": True, "source": "option"},
        "i-2": {"value": None, "source": "option"},
        "i-3": {"value": {"decision": "hold"}, "source": "option"},
    }

    decoded = decode_interrupt_responses(answers)

    assert [item["interruptResponse"]["response"] for item in decoded] == [
        True,
        None,
        {"decision": "hold"},
    ]


def test_answer_order_is_preserved() -> None:
    answers = {
        "i-2": {"value": False, "source": "option"},
        "i-1": {"value": True, "source": "option"},
    }

    decoded = decode_interrupt_responses(answers)

    assert [item["interruptResponse"]["interruptId"] for item in decoded] == [
        "i-2",
        "i-1",
    ]


def test_no_answers_decode_to_no_items() -> None:
    assert decode_interrupt_responses({}) == []
