from enum import Enum

from url_utils import forces_tor

class BrowsingMode(Enum):
    CLEAR = "clear"
    TOR = "tor"


class Router:
    def __init__(self, tor_client, state):
        self._tor = tor_client
        self._state = state

    def set_mode(self, mode: BrowsingMode):
        self._state.set_mode(mode)

    def get_mode(self) -> BrowsingMode:
        return self._state.get_mode()

    def resolve_mode_for_url(self, url: str) -> BrowsingMode:
        """Force Tor mode for .onion URLs regardless of current mode."""
        if forces_tor(url):
            return BrowsingMode.TOR
        return self.get_mode()

    def new_tor_identity(self):
        return self._tor.new_identity()
