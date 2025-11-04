"""Extended keyword intent mapping for media interactions."""
from __future__ import annotations

import re
from typing import Dict, Optional, Tuple

MEDIA_RECOMMEND_KEYWORDS = (
    "what should i watch",
    "recommend something",
    "suggest a show",
    "suggest something",
    "recommend a movie",
)

MEDIA_REQUEST_PATTERNS = (
    re.compile(r"add (?:number\s*)?(?P<num>[123])"),
    re.compile(r"add the (?P<word>first|second|third)", re.IGNORECASE),
    re.compile(r"add that"),
)

MEDIA_ADD_LIST_PATTERNS = (
    re.compile(r"add (?:it|that) to my list"),
    re.compile(r"save (?:it|that)"),
    re.compile(r"add to my list"),
)

ORDINAL_MAP = {"first": 1, "second": 2, "third": 3}


def detect_intent(text: str) -> Tuple[Optional[str], Dict[str, object]]:
    """Return the canonical media intent and extracted slots."""

    lowered = text.lower().strip()
    if not lowered:
        return None, {}

    for phrase in MEDIA_RECOMMEND_KEYWORDS:
        if phrase in lowered:
            return "MEDIA_RECOMMEND", {}

    for pattern in MEDIA_REQUEST_PATTERNS:
        match = pattern.search(lowered)
        if match:
            if "num" in match.groupdict():
                return "MEDIA_REQUEST", {"pick": int(match.group("num"))}
            if "word" in match.groupdict():
                word = match.group("word")
                return "MEDIA_REQUEST", {"pick": ORDINAL_MAP.get(word.lower(), 1)}
            return "MEDIA_REQUEST", {"pick": 1}

    for pattern in MEDIA_ADD_LIST_PATTERNS:
        if pattern.search(lowered):
            return "MEDIA_ADD_TO_LIST", {"pick": 1}

    return None, {}


__all__ = ["detect_intent"]
