"""
state.py
---------
Minimal, dependency-free state persistence to a local JSON file.
Tracks: current position side, entry price, current stop price, leverage in use.

This is intentionally simple. For real production use, Kraken's own
OpenPositions / OpenOrders endpoints are the ground truth - this local
state file is a convenience cache, and main.py reconciles against Kraken
on startup rather than trusting this file blindly.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass


@dataclass
class BotState:
    position_side: str | None = None     # "LONG", "SHORT", or None
    entry_price: float | None = None
    stop_price: float | None = None
    leverage: float = 1.0
    last_action: str | None = None


def load_state(path: str) -> BotState:
    if not os.path.exists(path):
        return BotState()
    try:
        with open(path, "r") as f:
            data = json.load(f)
        return BotState(**data)
    except (json.JSONDecodeError, TypeError, ValueError):
        return BotState()


def save_state(path: str, state: BotState) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(asdict(state), f, indent=2)
    os.replace(tmp_path, path)  # atomic on POSIX
