from __future__ import annotations

from dataclasses import dataclass
from threading import Lock

from router import BrowsingMode


@dataclass
class BrowserState:
    """Thread-safe shared browser mode state."""

    _mode: BrowsingMode = BrowsingMode.CLEAR

    def __post_init__(self):
        self._lock = Lock()

    def set_mode(self, mode: BrowsingMode):
        with self._lock:
            self._mode = mode

    def get_mode(self) -> BrowsingMode:
        with self._lock:
            return self._mode

    def is_tor_mode(self) -> bool:
        return self.get_mode() == BrowsingMode.TOR
