from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple
import json
import threading
import time


@dataclass(frozen=True)
class IdentityRecord:
    """In-memory representation of a stable identity."""

    stable_uuid: str
    role: str


class IdentityResolver:
    """Resolve transient speaker IDs to persistent identities.

    The resolver keeps a small in-memory cache for rapid lookups and persists
    stable identities with their known temporary aliases on disk. A confidence
    threshold ensures that low-certainty matches are treated as guests unless a
    recent high-confidence association exists.
    """

    def __init__(
        self,
        map_path: Path = Path("speakerid/identity_map.json"),
        *,
        cache_ttl: float = 180.0,
        alias_ttl: float = 7 * 24 * 3600.0,
        min_voice_confidence: float = 0.55,
        degrade_confidence: float = 0.35,
    ) -> None:
        self.map_path = map_path
        self.cache_ttl = cache_ttl
        self.alias_ttl = alias_ttl
        self.min_voice_confidence = min_voice_confidence
        if not 0.0 <= min_voice_confidence <= 1.0:
            raise ValueError("min_voice_confidence must be within [0, 1]")
        if not 0.0 <= degrade_confidence <= 1.0:
            raise ValueError("degrade_confidence must be within [0, 1]")
        if degrade_confidence > min_voice_confidence:
            raise ValueError("degrade_confidence must be <= min_voice_confidence")

        self.degrade_confidence = degrade_confidence

        # Cache: speaker_temp_id -> (stable_uuid, role, expiry_ts)
        self._cache: Dict[str, Tuple[str, str, float]] = {}
        self._identities: Dict[str, Dict[str, object]] = {}
        self._alias_index: Dict[str, Tuple[str, float]] = {}
        self._lock = threading.RLock()
        self._load()

    # ------------------------------------------------------------------
    # Persistence helpers
    def _load(self) -> None:
        """Load the identity map from disk, gracefully handling corruption."""
        if not self.map_path.exists():
            return

        try:
            data = json.loads(self.map_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            # Corrupt file: keep a backup and reset state
            backup = self.map_path.with_suffix(self.map_path.suffix + ".bak")
            backup.write_bytes(self.map_path.read_bytes())
            self._identities = {}
            self._alias_index = {}
            return

        identities = data.get("identities", {}) if isinstance(data, dict) else {}
        now = time.time()
        self._identities = {}
        self._alias_index = {}
        for stable_uuid, payload in identities.items():
            if not isinstance(payload, dict):
                continue
            role = str(payload.get("role", "guest"))
            aliases = payload.get("aliases", {})
            alias_map: Dict[str, float] = {}
            if isinstance(aliases, dict):
                for alias, ts in aliases.items():
                    try:
                        last_seen = float(ts)
                    except (TypeError, ValueError):
                        continue
                    if now - last_seen <= self.alias_ttl:
                        alias_map[str(alias)] = last_seen
                        self._alias_index[str(alias)] = (stable_uuid, last_seen)
            self._identities[stable_uuid] = {
                "role": role,
                "aliases": alias_map,
                "created_at": float(payload.get("created_at", now)),
            }

    def _save(self) -> None:
        payload = {
            "identities": {
                stable_uuid: {
                    "role": data["role"],
                    "aliases": data["aliases"],
                    "created_at": data.get("created_at", time.time()),
                }
                for stable_uuid, data in self._identities.items()
            }
        }
        self.map_path.parent.mkdir(parents=True, exist_ok=True)
        self.map_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    # ------------------------------------------------------------------
    # Public API
    def resolve(self, speaker_temp_id: str, voice_prob: float) -> Tuple[Optional[str], str]:
        """Resolve a transient ID to a stable identity and inferred role.

        Parameters
        ----------
        speaker_temp_id:
            Session-local identifier assigned by the upstream diarization module.
        voice_prob:
            Confidence score from speaker verification (0-1). Low confidence will
            suppress a lookup unless a recent alias binding exists, mitigating
            accidental impersonation.
        """

        now = time.time()
        with self._lock:
            cached = self._cache.get(speaker_temp_id)
            if cached and cached[2] > now:
                return cached[0], cached[1]

            identity = self._lookup_alias(speaker_temp_id, now)
            if identity and voice_prob >= self.degrade_confidence:
                stable_uuid, role = identity
                if voice_prob < self.min_voice_confidence:
                    # Degrade to guest role while still returning UUID for auditing.
                    role = "guest"
                self._remember(speaker_temp_id, stable_uuid, role, now)
                return stable_uuid, role

            if voice_prob >= self.min_voice_confidence:
                # High confidence but unseen alias: treat as guest until registered
                return None, "guest"

            return None, "guest"

    def register_identity(self, speaker_temp_id: str, stable_uuid: str, role: str) -> None:
        """Associate a transient speaker with a stable identity and role."""
        now = time.time()
        safe_role = role or "guest"
        with self._lock:
            record = self._identities.setdefault(
                stable_uuid,
                {"role": safe_role, "aliases": {}, "created_at": now},
            )
            record["role"] = safe_role
            aliases = record.setdefault("aliases", {})
            aliases[speaker_temp_id] = now
            self._alias_index[speaker_temp_id] = (stable_uuid, now)
            self._remember(speaker_temp_id, stable_uuid, safe_role, now)
            self._save()

    def forget_identity(self, stable_uuid: str) -> int:
        """Forget a stable identity and return the number of aliases removed."""
        with self._lock:
            record = self._identities.pop(stable_uuid, None)
            if not record:
                return 0
            aliases = record.get("aliases", {})
            for alias in list(aliases.keys()):
                self._cache.pop(alias, None)
                self._alias_index.pop(alias, None)
            self._save()
            return len(aliases)

    # ------------------------------------------------------------------
    # Internal helpers
    def _lookup_alias(self, speaker_temp_id: str, now: float) -> Optional[Tuple[str, str]]:
        entry = self._alias_index.get(speaker_temp_id)
        if not entry:
            return None
        stable_uuid, last_seen = entry
        if now - last_seen > self.alias_ttl:
            # Alias expired – drop it entirely.
            self._alias_index.pop(speaker_temp_id, None)
            record = self._identities.get(stable_uuid)
            if record:
                record.get("aliases", {}).pop(speaker_temp_id, None)
                self._save()
            return None
        record = self._identities.get(stable_uuid)
        if not record:
            return None
        return stable_uuid, str(record.get("role", "guest"))

    def _remember(self, speaker_temp_id: str, stable_uuid: str, role: str, now: float) -> None:
        self._cache[speaker_temp_id] = (stable_uuid, role, now + self.cache_ttl)
        record = self._identities.get(stable_uuid)
        if record is None:
            return
        aliases: Dict[str, float] = record.setdefault("aliases", {})  # type: ignore[assignment]
        aliases[speaker_temp_id] = now
        self._alias_index[speaker_temp_id] = (stable_uuid, now)


__all__ = ["IdentityResolver", "IdentityRecord"]
