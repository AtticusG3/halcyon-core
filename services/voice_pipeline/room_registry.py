"""Room registry for multi-room voice pipeline configuration."""
from __future__ import annotations

import os
import socket
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


class RoomRegistryError(RuntimeError):
    """Raised when room registry operations fail."""


class RoomRegistry:
    """Manages room configuration and provides room lookup services."""

    def __init__(
        self,
        rooms_config_path: Optional[str] = None,
        *,
        privacy_zones: Optional[str] = None,
        dnd_zones: Optional[str] = None,
    ) -> None:
        """Initialize the room registry.

        Parameters
        ----------
        rooms_config_path:
            Path to the rooms.yaml configuration file. If None, reads from
            ROOMS_CONFIG_PATH environment variable.
        privacy_zones:
            Comma-separated list of room IDs that are privacy zones.
            If None, reads from PRIVACY_ZONES environment variable.
        dnd_zones:
            Comma-separated list of room IDs that are do-not-disturb zones.
            If None, reads from DND_ZONES environment variable.
        """
        if yaml is None:
            raise RoomRegistryError("PyYAML is required. Install with: pip install pyyaml")

        config_path = rooms_config_path or os.getenv("ROOMS_CONFIG_PATH", "./services/voice_pipeline/rooms.yaml")
        self._config_path = Path(config_path).resolve()

        privacy_env = privacy_zones or os.getenv("PRIVACY_ZONES", "")
        dnd_env = dnd_zones or os.getenv("DND_ZONES", "")

        self._privacy_zones = set(zone.strip() for zone in privacy_env.split(",") if zone.strip())
        self._dnd_zones = set(zone.strip() for zone in dnd_env.split(",") if zone.strip())

        self._rooms: Dict[str, Dict] = {}
        self._mic_to_room: Dict[str, str] = {}
        self._load_config()

    def _load_config(self) -> None:
        """Load and validate the rooms configuration from YAML."""
        if not self._config_path.exists():
            raise RoomRegistryError(f"Rooms configuration file not found: {self._config_path}")

        try:
            with open(self._config_path, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
        except Exception as exc:
            raise RoomRegistryError(f"Failed to load rooms config: {exc}") from exc

        if not isinstance(data, dict) or "rooms" not in data:
            raise RoomRegistryError("Invalid rooms.yaml structure: missing 'rooms' key")

        rooms_list = data.get("rooms", [])
        if not isinstance(rooms_list, list):
            raise RoomRegistryError("Invalid rooms.yaml structure: 'rooms' must be a list")

        self._rooms = {}
        self._mic_to_room = {}

        for room_data in rooms_list:
            if not isinstance(room_data, dict):
                continue
            room_id = room_data.get("id")
            if not room_id or not isinstance(room_id, str):
                continue

            wyoming_host = room_data.get("wyoming_host", "127.0.0.1")
            wyoming_port = room_data.get("wyoming_port")
            if wyoming_port is None:
                raise RoomRegistryError(f"Room '{room_id}' missing wyoming_port")

            try:
                wyoming_port = int(wyoming_port)
                if not (1 <= wyoming_port <= 65535):
                    raise ValueError(f"Port {wyoming_port} out of range")
            except (ValueError, TypeError) as exc:
                raise RoomRegistryError(f"Room '{room_id}' has invalid wyoming_port: {exc}") from exc

            mics = room_data.get("mics", [])
            if not isinstance(mics, list):
                mics = []

            mic_list = []
            for mic_data in mics:
                if not isinstance(mic_data, dict):
                    continue
                mic_id = mic_data.get("id")
                if not mic_id or not isinstance(mic_id, str):
                    continue
                mic_list.append({"id": mic_id, "device": mic_data.get("device", "")})
                self._mic_to_room[mic_id] = room_id

            self._rooms[room_id] = {
                "id": room_id,
                "wyoming_host": str(wyoming_host),
                "wyoming_port": wyoming_port,
                "mics": mic_list,
            }

        # Validate Wyoming targets are reachable (non-blocking check)
        self._validate_wyoming_targets()

    def _validate_wyoming_targets(self) -> None:
        """Perform basic validation of Wyoming target connectivity."""
        for room_id, room_data in self._rooms.items():
            host = room_data["wyoming_host"]
            port = room_data["wyoming_port"]
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(0.5)
                result = sock.connect_ex((host, port))
                sock.close()
                if result != 0:
                    # Warning only - service may start later
                    pass
            except Exception:
                # Non-critical - validation is best-effort
                pass

    def get_room(self, room_id: str) -> Optional[Dict]:
        """Get room configuration by room ID.

        Returns
        -------
        Room configuration dict with keys: id, wyoming_host, wyoming_port, mics
        Returns None if room not found.
        """
        return self._rooms.get(room_id)

    def list_rooms(self) -> List[Dict]:
        """List all configured rooms.

        Returns
        -------
        List of room configuration dictionaries.
        """
        return list(self._rooms.values())

    def get_room_for_mic(self, mic_id: str) -> Optional[str]:
        """Get the room ID for a given microphone ID.

        Returns
        -------
        Room ID if mic is registered, None otherwise.
        """
        return self._mic_to_room.get(mic_id)

    def get_output_target(self, room_id: str) -> Tuple[str, int]:
        """Get the Wyoming output target (host, port) for a room.

        Parameters
        ----------
        room_id:
            Room identifier.

        Returns
        -------
        Tuple of (host, port) for Wyoming TTS output.

        Raises
        ------
        RoomRegistryError:
            If room not found or configuration invalid.
        """
        room = self.get_room(room_id)
        if not room:
            raise RoomRegistryError(f"Room '{room_id}' not found")
        return (room["wyoming_host"], room["wyoming_port"])

    def is_privacy_zone(self, room_id: str) -> bool:
        """Check if a room is a privacy zone.

        Privacy zones deny speech output and recording.
        """
        return room_id in self._privacy_zones

    def is_dnd_zone(self, room_id: str) -> bool:
        """Check if a room is a do-not-disturb zone.

        DND zones deny automatic speech output but allow SCARLET critical announcements.
        """
        return room_id in self._dnd_zones

    def get_default_room(self) -> Optional[str]:
        """Get the default room ID from environment or first room."""
        default = os.getenv("DEFAULT_ROOM")
        if default and default in self._rooms:
            return default
        if self._rooms:
            return next(iter(self._rooms.keys()))
        return None


__all__ = ["RoomRegistry", "RoomRegistryError"]

