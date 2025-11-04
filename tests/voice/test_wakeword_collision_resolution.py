"""Tests for wakeword collision resolution."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.voice_pipeline.wakeword_bus import WakeEvent, WakewordBus


class EventCollector:
    """Collects wakeword events for testing."""

    def __init__(self):
        self.events: list[WakeEvent] = []

    def __call__(self, event: WakeEvent) -> None:
        self.events.append(event)


def test_wakeword_collision_higher_confidence_wins():
    """Test that higher confidence wins in collision resolution."""
    bus = WakewordBus(redis_url="memory://test")

    collector = EventCollector()
    bus.subscribe(collector)

    # Emit two wake events within collision window (300ms)
    bus.emit_wake("mic_1", confidence=0.9, keyword="halcyon")
    time.sleep(0.1)  # 100ms later
    bus.emit_wake("mic_2", confidence=0.6, keyword="halcyon")

    # Wait a bit for processing
    time.sleep(0.1)

    # Should have only one event (higher confidence)
    assert len(collector.events) == 1
    assert collector.events[0].mic_id == "mic_1"
    assert collector.events[0].confidence == 0.9


def test_wakeword_collision_tie_breaks_on_first():
    """Test that tie breaks on first event (or last_room if available)."""
    bus = WakewordBus(redis_url="memory://test")

    collector = EventCollector()
    bus.subscribe(collector)

    # Emit two wake events with same confidence
    bus.emit_wake("mic_1", confidence=0.8, keyword="halcyon")
    time.sleep(0.1)
    bus.emit_wake("mic_2", confidence=0.8, keyword="halcyon")

    # Wait a bit for processing
    time.sleep(0.1)

    # Should have one event (first one wins in tie)
    assert len(collector.events) == 1
    # First event should win
    assert collector.events[0].mic_id in ("mic_1", "mic_2")


def test_wakeword_no_collision_beyond_window():
    """Test that events beyond collision window are both emitted."""
    bus = WakewordBus(redis_url="memory://test")

    collector = EventCollector()
    bus.subscribe(collector)

    # Emit two wake events beyond collision window
    bus.emit_wake("mic_1", confidence=0.9, keyword="halcyon")
    time.sleep(0.5)  # 500ms later (beyond 300ms window)
    bus.emit_wake("mic_2", confidence=0.6, keyword="halcyon")

    # Wait a bit for processing
    time.sleep(0.1)

    # Should have two events (no collision)
    assert len(collector.events) == 2


def test_wakeword_debouncing_per_mic():
    """Test that per-mic debouncing prevents rapid re-emission."""
    bus = WakewordBus(redis_url="memory://test")

    collector = EventCollector()
    bus.subscribe(collector)

    # Emit rapid events from same mic
    bus.emit_wake("mic_1", confidence=0.9, keyword="halcyon")
    time.sleep(0.1)
    bus.emit_wake("mic_1", confidence=0.9, keyword="halcyon")  # Should be debounced

    # Wait a bit for processing
    time.sleep(0.1)

    # Should have only one event (debounced)
    assert len(collector.events) == 1

