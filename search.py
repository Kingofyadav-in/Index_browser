from urllib.parse import quote_plus
from router import BrowsingMode
from config.settings import (
    DEFAULT_SEARCH_ENGINE_CLEAR,
    DEFAULT_SEARCH_ENGINE_TOR
)
from url_utils import looks_like_url


class SearchEngine:
    def __init__(self, tor_client=None):
        self._tor = tor_client

    def build_url(self, query: str, mode: BrowsingMode) -> str:
        encoded = quote_plus(query)
        if mode == BrowsingMode.TOR:
            return DEFAULT_SEARCH_ENGINE_TOR.format(encoded)
        return DEFAULT_SEARCH_ENGINE_CLEAR.format(encoded)

    def is_search_query(self, text: str) -> bool:
        """Return True if text looks like a search query rather than a URL."""
        return not looks_like_url(text)
