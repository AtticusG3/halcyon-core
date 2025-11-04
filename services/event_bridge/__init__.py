"""Event bridge adapters for external systems."""

from .homeassistant_mqtt import HAMQTTBridge, MQTTConnectionError

__all__ = ["HAMQTTBridge", "MQTTConnectionError"]
