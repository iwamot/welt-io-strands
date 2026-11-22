import json
from copy import deepcopy
from itertools import product

from welt_io_strands import decode_interrupt_responses


def test_answers_preserve_values_ids_and_order() -> None:
    values = (
        "approve",
        "make it shorter",
        True,
        False,
        None,
        42,
        "",
        ["a"],
        {"decision": "hold"},
    )
    for size in range(4):
        for chosen in product(values, repeat=size):
            for source in ("option", "input"):
                answers = {
                    f"i-{size - i}": {
                        "value": value,
                        "source": source if isinstance(value, str) else "option",
                    }
                    for i, value in enumerate(chosen)
                }
                expected = [
                    {
                        "interruptResponse": {
                            "interruptId": f"i-{size - i}",
                            "response": value,
                        }
                    }
                    for i, value in enumerate(chosen)
                ]
                before = deepcopy(answers)
                actual = decode_interrupt_responses(answers)
                # JSON distinguishes booleans from numbers, unlike Python equality.
                assert json.dumps(actual, sort_keys=True) == json.dumps(
                    expected, sort_keys=True
                ), answers
                assert answers == before
