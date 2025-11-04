"""Tests for room registry YAML loading and validation."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.voice_pipeline.room_registry import RoomRegistry, RoomRegistryError


def test_room_registry_loads_yaml():
    """Test that room registry loads and parses YAML correctly."""
    yaml_content = """
rooms:
  - id: lounge
    wyoming_host: 127.0.0.1
    wyoming_port: 10700
    mics:
      - id: mic_lounge_1
        device: hw:2,0
  - id: kitchen
    wyoming_host: 127.0.0.1
    wyoming_port: 10710
    mics:
      - id: mic_kitchen_1
        device: hw:3,0
"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        temp_path = f.name

    try:
        registry = RoomRegistry(rooms_config_path=temp_path)

        # Test list_rooms
        rooms = registry.list_rooms()
        assert len(rooms) == 2
        room_ids = [r["id"] for r in rooms]
        assert "lounge" in room_ids
        assert "kitchen" in room_ids

        # Test get_room
        lounge = registry.get_room("lounge")
        assert lounge is not None
        assert lounge["id"] == "lounge"
        assert lounge["wyoming_host"] == "127.0.0.1"
        assert lounge["wyoming_port"] == 10700
        assert len(lounge["mics"]) == 1
        assert lounge["mics"][0]["id"] == "mic_lounge_1"

        # Test get_room_for_mic
        assert registry.get_room_for_mic("mic_lounge_1") == "lounge"
        assert registry.get_room_for_mic("mic_kitchen_1") == "kitchen"
        assert registry.get_room_for_mic("unknown_mic") is None

        # Test get_output_target
        host, port = registry.get_output_target("lounge")
        assert host == "127.0.0.1"
        assert port == 10700

        # Test non-existent room
        assert registry.get_room("bedroom") is None
        with pytest.raises(RoomRegistryError):
            registry.get_output_target("bedroom")

    finally:
        os.unlink(temp_path)


def test_room_registry_validates_wyoming_targets():
    """Test that room registry validates Wyoming port numbers."""
    yaml_content = """
rooms:
  - id: lounge
    wyoming_host: 127.0.0.1
    wyoming_port: 99999
    mics: []
"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        temp_path = f.name

    try:
        with pytest.raises(RoomRegistryError, match="invalid wyoming_port"):
            RoomRegistry(rooms_config_path=temp_path)
    finally:
        os.unlink(temp_path)


def test_room_registry_handles_privacy_zones():
    """Test privacy zone detection."""
    yaml_content = """
rooms:
  - id: laundry
    wyoming_host: 127.0.0.1
    wyoming_port: 10700
    mics: []
  - id: office
    wyoming_host: 127.0.0.1
    wyoming_port: 10710
    mics: []
"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        temp_path = f.name

    try:
        registry = RoomRegistry(rooms_config_path=temp_path, privacy_zones="laundry,office")

        assert registry.is_privacy_zone("laundry") is True
        assert registry.is_privacy_zone("office") is True
        assert registry.is_privacy_zone("lounge") is False
    finally:
        os.unlink(temp_path)


def test_room_registry_handles_dnd_zones():
    """Test DND zone detection."""
    yaml_content = """
rooms:
  - id: bedroom_master
    wyoming_host: 127.0.0.1
    wyoming_port: 10700
    mics: []
"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        temp_path = f.name

    try:
        registry = RoomRegistry(rooms_config_path=temp_path, dnd_zones="bedroom_master")

        assert registry.is_dnd_zone("bedroom_master") is True
        assert registry.is_dnd_zone("lounge") is False
    finally:
        os.unlink(temp_path)

