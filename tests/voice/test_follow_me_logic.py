"""Tests for follow-me handoff logic."""
from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import pytest

import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.voice_pipeline.conversation_router import ConversationRouter
from services.voice_pipeline.room_registry import RoomRegistry


def test_follow_me_handoff_within_window():
    """Test that handoff occurs within FOLLOW_ME_MAX_GAP_SEC."""
    yaml_content = """
rooms:
  - id: lounge
    wyoming_host: 127.0.0.1
    wyoming_port: 10700
    mics: []
  - id: kitchen
    wyoming_host: 127.0.0.1
    wyoming_port: 10710
    mics: []
"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        temp_path = f.name

    try:
        registry = RoomRegistry(rooms_config_path=temp_path)
        router = ConversationRouter(
            registry,
            redis_url="memory://test",
            follow_me_max_gap_sec=10.0,
            handoff_min_confidence=0.75,
        )

        uuid = "test-uuid-123"

        # Set last room to lounge
        router.update_last_room(uuid, "lounge")
        time.sleep(0.1)

        # Attempt handoff to kitchen with high confidence (within window)
        candidates = [("kitchen", 0.85)]
        handoff_room = router.follow_me(uuid, candidates)

        assert handoff_room == "kitchen"
    finally:
        os.unlink(temp_path)


def test_follow_me_no_handoff_beyond_window():
    """Test that handoff does not occur beyond FOLLOW_ME_MAX_GAP_SEC."""
    yaml_content = """
rooms:
  - id: lounge
    wyoming_host: 127.0.0.1
    wyoming_port: 10700
    mics: []
  - id: kitchen
    wyoming_host: 127.0.0.1
    wyoming_port: 10710
    mics: []
"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        temp_path = f.name

    try:
        registry = RoomRegistry(rooms_config_path=temp_path)
        router = ConversationRouter(
            registry,
            redis_url="memory://test",
            follow_me_max_gap_sec=10.0,
            handoff_min_confidence=0.75,
        )

        uuid = "test-uuid-456"

        # Set last room to lounge
        router.update_last_room(uuid, "lounge")

        # Simulate time passing beyond window
        import redis

        redis_client = redis.from_url("memory://test", decode_responses=True)
        last_seen_key = f"halcyon:voice:last_seen:{uuid}"
        redis_client.set(last_seen_key, str(time.time() - 15.0), ex=3600)  # 15 seconds ago

        # Attempt handoff to kitchen
        candidates = [("kitchen", 0.85)]
        handoff_room = router.follow_me(uuid, candidates)

        # Should not handoff (beyond window)
        assert handoff_room is None
    finally:
        os.unlink(temp_path)


def test_follow_me_requires_min_confidence():
    """Test that handoff requires minimum confidence."""
    yaml_content = """
rooms:
  - id: lounge
    wyoming_host: 127.0.0.1
    wyoming_port: 10700
    mics: []
  - id: kitchen
    wyoming_host: 127.0.0.1
    wyoming_port: 10710
    mics: []
"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        temp_path = f.name

    try:
        registry = RoomRegistry(rooms_config_path=temp_path)
        router = ConversationRouter(
            registry,
            redis_url="memory://test",
            follow_me_max_gap_sec=10.0,
            handoff_min_confidence=0.75,
        )

        uuid = "test-uuid-789"

        # Set last room to lounge
        router.update_last_room(uuid, "lounge")
        time.sleep(0.1)

        # Attempt handoff with low confidence
        candidates = [("kitchen", 0.6)]  # Below 0.75 threshold
        handoff_room = router.follow_me(uuid, candidates)

        # Should not handoff (low confidence)
        assert handoff_room is None
    finally:
        os.unlink(temp_path)

