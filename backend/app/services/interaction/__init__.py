"""TelePilot interaction framework services."""

from __future__ import annotations

from typing import Any

from .contracts import guard_interaction_actions


def __getattr__(name: str) -> Any:
    if name == "InteractionDeliveryExecutor":
        from .delivery import InteractionDeliveryExecutor

        return InteractionDeliveryExecutor
    raise AttributeError(name)


__all__ = ["InteractionDeliveryExecutor", "guard_interaction_actions"]
