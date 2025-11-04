"""Lightweight taste profiling and scoring utilities."""
from __future__ import annotations

from collections import Counter
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


FeatureWeights = Dict[str, float]


class TasteProfile:
    """Constructs and evaluates household viewing preferences."""

    def __init__(self, history: Sequence[Mapping[str, object]], *, max_items: int = 120) -> None:
        self.history = list(history)[-max_items:]
        self.profile = self._build_profile(self.history)

    # ------------------------------------------------------------------
    @staticmethod
    def _build_profile(history: Sequence[Mapping[str, object]]) -> FeatureWeights:
        features: Counter[str] = Counter()
        for item in history:
            TasteProfile._ingest_item(features, item)
        total = sum(features.values())
        if total == 0:
            return {}
        return {feature: count / total for feature, count in features.items()}

    @staticmethod
    def _ingest_item(features: Counter[str], item: Mapping[str, object]) -> None:
        genres = item.get("genres") or []
        for genre in genres:
            features[f"genre:{str(genre).lower()}"] += 1

        networks = item.get("networks") or []
        for network in networks:
            features[f"network:{str(network).lower()}"] += 0.5

        runtime = TasteProfile._runtime_bucket(item.get("runtime"))
        if runtime:
            features[f"pace:{runtime}"] += 0.4

        release = TasteProfile._release_bucket(item.get("release_year"))
        if release:
            features[f"year:{release}"] += 0.6

    @staticmethod
    def _runtime_bucket(runtime: object) -> Optional[str]:
        try:
            minutes = int(runtime)
        except (TypeError, ValueError):
            return None
        if minutes < 30:
            return "short"
        if minutes < 60:
            return "medium"
        if minutes < 110:
            return "feature"
        return "epic"

    @staticmethod
    def _release_bucket(year: object) -> Optional[str]:
        try:
            y = int(year)
        except (TypeError, ValueError):
            return None
        if y < 2000:
            return "classic"
        if y < 2010:
            return "mid"
        if y < 2020:
            return "recent"
        return "new"

    # ------------------------------------------------------------------
    @staticmethod
    def score(candidate: Mapping[str, object], profile: FeatureWeights) -> float:
        if not profile:
            return 0.5
        candidate_features = Counter[str]()
        TasteProfile._ingest_item(candidate_features, candidate)
        if not candidate_features:
            return 0.3
        numerator = sum(profile.get(feature, 0.0) for feature in candidate_features.keys())
        return max(0.0, min(1.0, numerator))

    @staticmethod
    def explain(candidate: Mapping[str, object], profile: FeatureWeights) -> str:
        if not profile:
            return "These are popular picks right now."
        candidate_features = Counter[str]()
        TasteProfile._ingest_item(candidate_features, candidate)
        scored = [
            (profile.get(feature, 0.0), feature)
            for feature in candidate_features.keys()
            if feature in profile
        ]
        scored.sort(reverse=True)
        if not scored:
            return "It offers something a little different from your recent viewing."
        phrases = [TasteProfile._feature_phrase(feature) for _, feature in scored[:2]]
        return " and ".join(phrases)

    @staticmethod
    def _feature_phrase(feature: str) -> str:
        kind, _, value = feature.partition(":")
        if kind == "genre":
            return f"It leans into {value.replace('-', ' ')} stories."
        if kind == "network":
            return f"It comes from {value.title()}, a frequent favorite."
        if kind == "pace":
            descriptors = {
                "short": "quick episodes",
                "medium": "snappy pacing",
                "feature": "feature-length runs",
                "epic": "long-form epics",
            }
            return descriptors.get(value, "It matches your pacing preferences.")
        if kind == "year":
            mapping = {
                "classic": "classic era", "mid": "2000s era", "recent": "recent releases", "new": "brand new releases"
            }
            return f"It fits your taste for {mapping.get(value, value)}."
        return "It's aligned with your viewing profile."


__all__ = ["TasteProfile", "FeatureWeights"]
