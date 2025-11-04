"""Tests for privacy and DND zone handling."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.voice_pipeline.conversation_router import ConversationRouter
from services.voice_pipeline.room_registry import RoomRegistry


def test_privacy_zone_denies_speak():
    """Test that privacy zones deny speech output."""
    yaml_content = """
rooms:
  - id: laundry
    wyoming_host: 127.0.0.1
    wyoming_port: 10700
    mics: []
  - id: lounge
    wyoming_host: 127.0.0.1
    wyoming_port: 10710
    mics: []
"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        temp_path = f.name

    try:
        registry = RoomRegistry(rooms_config_path=temp_path, privacy_zones="laundry")
        router = ConversationRouter(registry, redis_url="memory://test")

        # Privacy zone should deny speech for both personas
        assert router.can_speak_in("laundry", "HALSTON") is False
        assert router.can_speak_in("laundry", "SCARLET") is False

        # Non-privacy zone should allow speech
        assert router.can_speak_in("lounge", "HALSTON") is True
        assert router.can_speak_in("lounge", "SCARLET") is True
    finally:
        os.unlink(temp_path)


def test_dnd_zone_allows_scarlet_only():
    """Test that DND zones allow SCARLET critical announcements only."""
    yaml_content = """
rooms:
  - id: bedroom_master
    wyoming_host: 127.0.0.1
    wyoming_port: 10700
    mics: []
  - id: lounge
    wyoming_host: 127.0.0.1
    wyoming_port: 10710
    mics: []
"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        temp_path = f.name

    try:
        registry = RoomRegistry(rooms_config_path=temp_path, dnd_zones="bedroom_master")
        router = ConversationRouter(registry, redis_url="memory://test")

        # DND zone should deny HALSTON
        assert router.can_speak_in("bedroom_master", "HALSTON") is False

        # DND zone should allow SCARLET (critical override)
        assert router.can_speak_in("bedroom_master", "SCARLET") is True

        # Non-DND zone should allow both
        assert router.can_speak_in("lounge", "HALSTON") is True
        assert router.can_speak_in("lounge", "SCARLET") is True
    finally:
        os.unlink(temp_path)


def test_privacy_overrides_dnd():
    """Test that privacy zones take precedence over DND."""
    yaml_content = """
rooms:
  - id: laundry
    wyoming_host: 127.0.0.1
    wyoming_port: 10700
    mics: []
"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        temp_path = f.name

    try:
        registry = RoomRegistry(
            rooms_config_path=temp_path,
            privacy_zones="laundry",
            dnd_zones="laundry",  # Same room in both
        )
        router = ConversationRouter(registry, redis_url="memory://test")

        # Privacy should take precedence - deny even SCARLET
        assert router.can_speak_in("laundry", "SCARLET") is False
    finally:
        os.unlink(temp_path)

