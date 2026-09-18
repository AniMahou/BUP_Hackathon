"""Deterministic number/fraction extraction from raw note text, used for the grounding check (G11)
and as one input to the optional regex cross-check (§4.7.3). Generic patterns only — not tuned to
any specific note wording, per planning.md §4.7.
"""

import re

from app.schemas.llm_output import Quantity

_WORD_NUMBERS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    "hundred": 100,
}

_FRACTION_WORDS = {
    "half": 0.5, "halved": 0.5,
    "third": 1 / 3, "a third": 1 / 3,
    "quarter": 0.25, "a quarter": 0.25,
    "three-quarters": 0.75, "three quarters": 0.75, "three-fourths": 0.75, "three fourths": 0.75,
    "two-thirds": 2 / 3, "two thirds": 2 / 3,
    "two-fifths": 0.4, "two fifths": 0.4, "three-fifths": 0.6, "three fifths": 0.6,
    "four-fifths": 0.8, "four fifths": 0.8,
    "fifth": 0.2, "one-fifth": 0.2, "one fifth": 0.2,
    "tenth": 0.1,
    "full": 1.0, "whole": 1.0,
    "none": 0.0, "zero": 0.0,
}

_TOL = 1e-3


def _word_number_value(text: str) -> set[float]:
    values: set[float] = set()
    tokens = re.findall(r"[a-z]+(?:-[a-z]+)?", text.lower())
    i = 0
    while i < len(tokens):
        tok = tokens[i].replace("-", " ")
        parts = tok.split()
        total = None
        for p in parts:
            if p in _WORD_NUMBERS:
                val = _WORD_NUMBERS[p]
                total = val if total is None else (total + val if val < 100 else total * val)
        if total is not None:
            values.add(float(total))
        i += 1
    # handle "one hundred twenty" style sequences across separate tokens
    joined = " ".join(tokens)
    for match in re.finditer(
        r"\b(one|two|three|four|five|six|seven|eight|nine)\s+hundred(\s+(and\s+)?"
        r"(twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|one|two|three|four|five|six|seven|eight|nine))?\b",
        joined,
    ):
        chunk = match.group(0).replace(" and ", " ")
        parts = chunk.split()
        total = 0.0
        i = 0
        while i < len(parts):
            p = parts[i]
            if p == "hundred":
                total *= 100
            elif p in _WORD_NUMBERS:
                total += _WORD_NUMBERS[p]
            i += 1
        values.add(total)
    return values


def extract_numbers(text: str) -> set[float]:
    values: set[float] = set()
    for m in re.finditer(r"-?\d+(?:\.\d+)?", text):
        try:
            values.add(float(m.group(0)))
        except ValueError:
            continue
    values |= _word_number_value(text)
    lower = text.lower()
    for word, val in _FRACTION_WORDS.items():
        if word in lower:
            values.add(val)
    return values


def is_grounded(quantity: Quantity, note_text: str) -> bool:
    numbers = extract_numbers(note_text)
    if not numbers:
        return False
    v = quantity.value
    candidates = {v, v * 100, v / 100 if v != 0 else 0.0}
    return any(abs(n - c) < _TOL for n in numbers for c in candidates)
