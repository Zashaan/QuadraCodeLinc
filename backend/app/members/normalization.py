"""Bounded, literal parsing of an ID utterance; no fuzzy matching or word deletion."""

import re

from app.members.repository import MemberRepository

DIGITS = dict(
    zip(
        ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"],
        "0123456789",
        strict=True,
    )
)
DIGITS["oh"] = "0"
REPEATS = {"double": 2, "triple": 3}
MAX_CANDIDATES = 16


def member_id_candidates(spoken_id: str) -> list[str]:
    text = spoken_id.strip().lower()
    if not text or len(text) > 256 or not re.fullmatch(r"[a-z0-9\s\-–—]+", text):
        return []
    tokens = re.split(r"[\s\-–—]+", text)
    candidates = {""}
    index = 0
    while index < len(tokens):
        token = tokens[index]
        count = REPEATS.get(token, 1)
        if token in REPEATS:
            index += 1
            if index == len(tokens):
                return []
            token = tokens[index]
            if token not in DIGITS and not re.fullmatch(r"[0-9]", token):
                return []
        # A spoken standalone O/oh may be a letter or zero. Never alter O inside DEMO.
        choices = (
            {"O", "0"}
            if token in {"o", "oh"} and count == 1
            else {DIGITS.get(token, token.upper()) * count}
        )
        candidates = {prefix + suffix for prefix in candidates for suffix in choices}
        if len(candidates) > MAX_CANDIDATES or any(len(value) > 32 for value in candidates):
            return []
        index += 1
    return sorted(value for value in candidates if re.fullmatch(r"[A-Z0-9]{1,32}", value))


def resolve_member_ids(spoken_id: str, repository: MemberRepository) -> list[str]:
    return [
        candidate
        for candidate in member_id_candidates(spoken_id)
        if repository.get_member(candidate) is not None
    ]
