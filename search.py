from urllib.parse import quote_plus
from router import BrowsingMode
from config.settings import (
    DEFAULT_SEARCH_ENGINE_CLEAR,
    DEFAULT_SEARCH_ENGINE_TOR,
)
from url_utils import looks_like_url

# ── Bang commands — prefix a query with one of these to jump straight ──────
# to any search engine or site without changing the default engine.
BANGS: dict[str, str] = {
    "!g":    "https://www.google.com/search?q={}",
    "!ddg":  "https://duckduckgo.com/?q={}",
    "!b":    "https://www.bing.com/search?q={}",
    "!brave":"https://search.brave.com/search?q={}",
    "!yt":   "https://www.youtube.com/results?search_query={}",
    "!gh":   "https://github.com/search?q={}",
    "!gl":   "https://gitlab.com/search?search={}",
    "!w":    "https://en.wikipedia.org/w/index.php?search={}",
    "!r":    "https://www.reddit.com/search/?q={}",
    "!so":   "https://stackoverflow.com/search?q={}",
    "!npm":  "https://www.npmjs.com/search?q={}",
    "!py":   "https://pypi.org/search/?q={}",
    "!img":  "https://www.google.com/search?tbm=isch&q={}",
    "!maps": "https://www.google.com/maps/search/{}",
    "!tw":   "https://twitter.com/search?q={}",
    "!aws":  "https://docs.aws.amazon.com/search/doc-search.html#facet_doc_product&searchPath=documentation-guide&searchQuery={}",
    "!mdn":  "https://developer.mozilla.org/en-US/search?q={}",
    "!arch": "https://wiki.archlinux.org/index.php?search={}",
}


class SearchEngine:
    def build_url(self, query: str, mode: BrowsingMode) -> str:
        # Check for bang command at start of query
        parts = query.strip().split(None, 1)
        if parts:
            bang = parts[0].lower()
            if bang in BANGS:
                rest = parts[1] if len(parts) > 1 else ""
                return BANGS[bang].format(quote_plus(rest))

        encoded = quote_plus(query)
        if mode == BrowsingMode.TOR:
            return DEFAULT_SEARCH_ENGINE_TOR.format(encoded)
        return DEFAULT_SEARCH_ENGINE_CLEAR.format(encoded)

    def is_search_query(self, text: str) -> bool:
        return not looks_like_url(text)

    @staticmethod
    def bang_hint(prefix: str) -> list[str]:
        """Return bang commands that start with *prefix* (for autocomplete hints)."""
        p = prefix.lower()
        return [b for b in BANGS if b.startswith(p)]
