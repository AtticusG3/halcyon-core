"""Media recommendation pipeline integrating Plex and TMDB."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from orchestrator.logging.event_bus import EventBus
from services.media.plex_client import PlexClient
from services.media.taste_profile import FeatureWeights, TasteProfile
from services.media.tmdb_client import TMDBClient


class MediaRecommender:
    """Combine household history with TMDB metadata to suggest content."""

    def __init__(
        self,
        *,
        plex_client: PlexClient,
        tmdb_client: TMDBClient,
        event_bus: Optional[EventBus] = None,
    ) -> None:
        self._plex = plex_client
        self._tmdb = tmdb_client
        self._event_bus = event_bus or EventBus()

    # ------------------------------------------------------------------
    def recommend_for_user(self, user_uuid: Optional[str], k: int = 3) -> List[Dict[str, Any]]:
        """Return the top ``k`` recommendations for ``user_uuid``."""

        history_movies = self._plex.get_user_history(user_uuid, "movie", limit=200)
        history_shows = self._plex.get_user_history(user_uuid, "show", limit=200)
        history: List[Dict[str, Any]] = history_movies + history_shows
        personalized = bool(user_uuid and history)
        profile = TasteProfile(history).profile
        watched_tmdb_ids = {item.get("tmdb_id") for item in history if item.get("tmdb_id")}

        candidate_pool, candidate_sources = self._build_candidate_pool(user_uuid, history)
        scored: List[Tuple[float, Dict[str, Any]]] = []
        for candidate in candidate_pool:
            tmdb_id = candidate.get("tmdb_id")
            if tmdb_id is None or tmdb_id in watched_tmdb_ids:
                continue
            features = self._candidate_features(candidate)
            score = self._score_candidate(features, profile, candidate)
            candidate["score"] = score
            candidate["reason"] = TasteProfile.explain(features, profile)
            candidate["personalized"] = personalized
            scored.append((score, candidate))
        scored.sort(key=lambda item: item[0], reverse=True)
        top = [item for _, item in scored[:k]]
        self._event_bus.publish(
            "media/recommendation",
            {
                "uuid": user_uuid,
                "n_options": len(top),
                "sources": sorted(candidate_sources),
            },
        )
        return top

    def format_spoken(self, options: Sequence[Dict[str, Any]], persona: str) -> str:
        """Generate persona-aligned spoken summary for the provided options."""

        if not options:
            return "I couldn't find anything suitable right now."
        personalized = any(option.get("personalized") for option in options)
        if persona.upper() == "SCARLET":
            header = "Three candidates." if options else "No candidates."  # pragma: no cover - guard
        else:
            header = (
                "Based on your recent habits, here are three options."
                if personalized
                else "Here are three popular options worth a look."
            )
        parts = [header]
        for idx, option in enumerate(options, start=1):
            title = option.get("title", "")
            reason = option.get("reason", "")
            snippet = f"{idx}: {title}" if persona.upper() == "HALSTON" else f"{title}"
            if persona.upper() == "HALSTON" and reason:
                snippet = f"{snippet} — {reason}".strip()
            elif persona.upper() == "SCARLET" and reason:
                snippet = f"{snippet}. {reason}".strip()
            parts.append(snippet)
        if persona.upper() == "HALSTON":
            parts.append("Which would you like?")
        else:
            parts.append("Choose one.")
        return " ".join(part for part in parts if part)

    # ------------------------------------------------------------------
    def _candidate_features(self, candidate: Dict[str, Any]) -> Dict[str, Any]:
        genres = candidate.get("genre_ids") or candidate.get("genres") or []
        if isinstance(genres, list) and genres and isinstance(genres[0], dict):
            genres = [g.get("name") for g in genres if g.get("name")]
        networks = []
        if candidate.get("network"):
            networks = [candidate["network"]]
        elif candidate.get("origin_country"):
            networks = candidate.get("origin_country", [])
        runtime = candidate.get("runtime") or candidate.get("episode_run_time", [None])[0]
        return {
            "genres": genres,
            "networks": networks,
            "runtime": runtime,
            "release_year": self._extract_year(candidate.get("release_date") or candidate.get("first_air_date")),
        }

    def _score_candidate(
        self,
        candidate_features: Dict[str, Any],
        profile: FeatureWeights,
        candidate: Dict[str, Any],
    ) -> float:
        base = TasteProfile.score(candidate_features, profile)
        novelty = 0.1 if candidate.get("popularity", 0) < 10 else 0.0
        source_bonus = 0.2 if candidate.get("source") == "continue" else 0.0
        score = base + novelty + source_bonus
        return max(0.0, min(1.0, score))

    def _build_candidate_pool(
        self,
        user_uuid: Optional[str],
        history: Sequence[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        pool: List[Dict[str, Any]] = []
        sources: List[str] = []

        for type_name in ("movie", "tv"):
            for entry in self._tmdb.trending(type_name):
                pool.append(self._normalize_tmdb(entry, type_name, source="trending"))
            sources.append("trending")

        if user_uuid:
            continue_list = self._plex.get_continue_watching(user_uuid)
            for item in continue_list:
                normalized = {
                    "tmdb_id": item.get("tmdb_id"),
                    "type": item.get("type", "movie"),
                    "title": item.get("title"),
                    "overview": item.get("summary"),
                    "runtime": item.get("runtime"),
                    "release_year": item.get("release_year"),
                    "genres": item.get("genres", []),
                    "source": "continue",
                }
                pool.append(normalized)
            if continue_list:
                sources.append("continue")

            top_history = [item for item in history if item.get("tmdb_id")] [:10]
            for item in top_history:
                tmdb_id = item.get("tmdb_id")
                if not tmdb_id:
                    continue
                related = self._tmdb.recommendations(tmdb_id, item.get("type", "movie"))
                for candidate in related[:5]:
                    pool.append(self._normalize_tmdb(candidate, item.get("type", "movie"), source="related"))
                if related:
                    sources.append("related")
        return pool, list(dict.fromkeys(sources))

    def _normalize_tmdb(self, item: Dict[str, Any], type_name: str, *, source: str) -> Dict[str, Any]:
        title = item.get("title") or item.get("name")
        overview = item.get("overview")
        runtime = item.get("runtime") or item.get("episode_run_time", [None])[0]
        release = self._extract_year(item.get("release_date") or item.get("first_air_date"))
        return {
            "tmdb_id": item.get("id"),
            "type": type_name,
            "title": title,
            "overview": overview,
            "runtime": runtime,
            "release_year": release,
            "genres": item.get("genres") or item.get("genre_ids") or [],
            "popularity": item.get("popularity", 0.0),
            "source": source,
        }

    def _extract_year(self, date_str: Optional[str]) -> Optional[int]:
        if not date_str:
            return None
        try:
            return int(date_str.split("-", 1)[0])
        except ValueError:
            return None


__all__ = ["MediaRecommender"]
