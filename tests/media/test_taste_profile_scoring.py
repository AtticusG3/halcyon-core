from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.media.taste_profile import TasteProfile


def test_taste_profile_scoring_and_explanation() -> None:
    history = [
        {
            "genres": ["Sci-Fi", "Adventure"],
            "networks": ["Netflix"],
            "runtime": 50,
            "release_year": 2022,
        },
        {
            "genres": ["Sci-Fi", "Drama"],
            "networks": ["Netflix"],
            "runtime": 55,
            "release_year": 2021,
        },
    ]
    profile = TasteProfile(history).profile
    assert profile  # profile should not be empty

    candidate = {
        "genres": ["Sci-Fi", "Thriller"],
        "networks": ["Netflix"],
        "runtime": 52,
        "release_year": 2023,
    }
    score = TasteProfile.score(candidate, profile)
    assert score > 0.5

    explanation = TasteProfile.explain(candidate, profile)
    lower_explanation = explanation.lower()
    assert "sci fi" in lower_explanation
    assert any(token in lower_explanation for token in ["netflix", "brand new", "pacing", "recent releases"])
