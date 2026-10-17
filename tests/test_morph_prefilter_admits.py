"""The loop prefilter must equal the original all/any expression."""

from itertools import product

from core.morph import _admits


def test_admits_matches_all_any_expression() -> None:
    texts = tuple(
        "".join(characters)
        for size in range(3)
        for characters in product(("a", "b"), repeat=size)
    )
    conditions = tuple(
        literals
        for size in range(3)
        for literals in product(("", "a", "b", "ab"), repeat=size)
    )
    any_of_conditions = tuple(
        groups
        for size in range(3)
        for groups in product(conditions, repeat=size)
    )
    for text, required, required_any_of in product(texts, conditions, any_of_conditions):
        expected = all(literal in text for literal in required) and all(
            any(literal in text for literal in group) for group in required_any_of
        )
        assert _admits(text, required, required_any_of) == expected, (
            text, required, required_any_of
        )
