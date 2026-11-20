"""Intent parsing: instruction text → EditSpec.

The parser sits behind a small interface so the rule-based implementation can later be swapped for
a local model (it must stay offline) without touching the rest of the app:

    parser = get_parser()            # "rules" by default, AUTOCUT_INTENT_PARSER overrides
    spec = parser.parse("funny 60 second tiktok with captions", available_presets=[...])
"""

from __future__ import annotations

import os
from typing import Protocol, runtime_checkable

from ..editspec import EditSpec


@runtime_checkable
class IntentParser(Protocol):
    name: str

    def parse(self, text: str, *, available_presets: list[str] | None = None) -> EditSpec:
        ...


_REGISTRY: dict[str, type] = {}


def register(name: str):
    def deco(cls):
        _REGISTRY[name] = cls
        cls.name = name
        return cls
    return deco


def get_parser(name: str | None = None) -> IntentParser:
    from . import rules  # noqa: F401  (registers "rules")

    name = name or os.environ.get("AUTOCUT_INTENT_PARSER", "rules")
    if name not in _REGISTRY:
        raise ValueError(f"Unknown intent parser '{name}'. Available: {', '.join(_REGISTRY)}")
    return _REGISTRY[name]()


def parse(text: str, *, available_presets: list[str] | None = None) -> EditSpec:
    if available_presets is None:
        from ..style import list_profiles

        available_presets = list_profiles()
    return get_parser().parse(text, available_presets=available_presets)
