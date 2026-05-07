from __future__ import annotations

from threading import Lock

from router import BrowsingMode


class BrowserState:
    """Thread-safe shared browser mode state."""

    def __init__(self, mode: BrowsingMode = BrowsingMode.CLEAR):
        self._mode = mode
        self._lock = Lock()

    def set_mode(self, mode: BrowsingMode):
        with self._lock:
            self._mode = mode

    def get_mode(self) -> BrowsingMode:
        with self._lock:
            return self._mode

    def is_tor_mode(self) -> bool:
        return self.get_mode() == BrowsingMode.TOR
