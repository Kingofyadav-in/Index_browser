import unittest
from http.client import HTTPConnection
from unittest.mock import patch

from browser_state import BrowserState
import proxy_server
from router import BrowsingMode, Router
from search import SearchEngine
from PyQt6.QtCore import QUrl
from ui.request_interceptor import OnionRequestInterceptor
from ui.web_page import _onion_url_normalizer_script
from url_utils import forces_tor, is_onion_url, looks_like_url, normalize_url


class DummyTor:
    def __init__(self, connected=True):
        self._connected = connected

    def is_connected(self):
        return self._connected

    def get_proxy_dict(self):
        return {"http": "socks5h://127.0.0.1:9050"}

    def new_identity(self):
        return True


class UrlUtilsTests(unittest.TestCase):
    def test_detects_onion_hosts_without_substring_false_positive(self):
        self.assertTrue(is_onion_url("exampleabc123.onion"))
        self.assertTrue(is_onion_url("http://exampleabc123.onion/path"))
        self.assertFalse(is_onion_url("search .onion docs"))
        self.assertFalse(is_onion_url("https://example.com/path/onion"))

    def test_forces_tor_for_onion_and_tor_check(self):
        self.assertTrue(forces_tor("exampleabc123.onion"))
        self.assertTrue(forces_tor("https://check.torproject.org/api/ip"))
        self.assertFalse(forces_tor("https://example.com"))

    def test_url_classification_and_normalization(self):
        self.assertTrue(looks_like_url("example.com"))
        self.assertTrue(looks_like_url("http://example.com"))
        self.assertFalse(looks_like_url("example search"))
        self.assertEqual(normalize_url("example.com"), "https://example.com")
        self.assertEqual(normalize_url("abc.onion"), "http://abc.onion")


class SearchEngineTests(unittest.TestCase):
    def test_search_query_detection(self):
        engine = SearchEngine()
        self.assertTrue(engine.is_search_query("private search"))
        self.assertFalse(engine.is_search_query("example.com"))
        self.assertFalse(engine.is_search_query("https://example.com"))

    def test_build_url_uses_mode(self):
        engine = SearchEngine()
        self.assertIn("hello+world", engine.build_url("hello world", BrowsingMode.CLEAR))
        self.assertIn("hello+world", engine.build_url("hello world", BrowsingMode.TOR))
        self.assertIn(".onion", engine.build_url("hello world", BrowsingMode.TOR))


class RouterTests(unittest.TestCase):
    def test_router_uses_state_and_forces_tor_for_onion(self):
        state = BrowserState(BrowsingMode.CLEAR)
        router = Router(DummyTor(), state)
        self.assertEqual(router.resolve_mode_for_url("https://example.com"), BrowsingMode.CLEAR)
        self.assertEqual(router.resolve_mode_for_url("abc.onion"), BrowsingMode.TOR)

        router.set_mode(BrowsingMode.TOR)
        self.assertEqual(state.get_mode(), BrowsingMode.TOR)
        self.assertEqual(router.resolve_mode_for_url("https://example.com"), BrowsingMode.TOR)


class OnionPolicyTests(unittest.TestCase):
    def test_blocks_clearnet_subresource_on_onion_page(self):
        should_block = OnionRequestInterceptor._should_block_onion_subresource(
            QUrl("https://cdn.example.com/app.js"),
            "cdn.example.com",
            "siteabcdef.onion",
        )
        self.assertTrue(should_block)

    def test_allows_onion_and_local_subresources_on_onion_page(self):
        self.assertFalse(OnionRequestInterceptor._should_block_onion_subresource(
            QUrl("http://assetsabcdef.onion/app.js"),
            "assetsabcdef.onion",
            "siteabcdef.onion",
        ))
        self.assertFalse(OnionRequestInterceptor._should_block_onion_subresource(
            QUrl("http://127.0.0.1:8118/__status__"),
            "127.0.0.1",
            "siteabcdef.onion",
        ))


class OnionNormalizerScriptTests(unittest.TestCase):
    def test_normalizer_covers_fetch_request_and_url_inputs(self):
        script = _onion_url_normalizer_script()
        self.assertIn("window.fetch", script)
        self.assertIn("window.Request", script)
        self.assertIn("input instanceof URL", script)
        self.assertIn("url.protocol = 'http:'", script)
        self.assertIn("const NativeURL = window.URL", script)
        self.assertIn("window.URL = WrappedURL", script)
        self.assertIn("Element.prototype.setAttribute", script)
        self.assertIn("HTMLAnchorElement.prototype", script)


class ProxyServerTests(unittest.TestCase):
    def tearDown(self):
        proxy_server.stop()
        proxy_server.configure_state(None)
        proxy_server.configure_tor_client(None)

    def test_status_reports_mode_separately_from_tor_connectivity(self):
        state = BrowserState(BrowsingMode.TOR)
        proxy_server.configure_state(state)
        proxy_server.configure_tor_client(DummyTor(connected=False))
        port = proxy_server.start()

        conn = HTTPConnection("127.0.0.1", port, timeout=3)
        try:
            conn.request("GET", "/__status__")
            response = conn.getresponse()
            payload = response.read().decode("utf-8")
        finally:
            conn.close()

        self.assertEqual(response.status, 200)
        self.assertIn('"mode": "tor"', payload)
        self.assertIn('"tor_mode": true', payload)
        self.assertIn('"tor_connected": false', payload)

    def test_make_connection_forces_onion_hosts_through_socks(self):
        with patch.object(proxy_server, "_socks5_connect", return_value="tor") as socks:
            with patch.object(proxy_server, "_direct_connect", return_value="clear") as direct:
                self.assertEqual(proxy_server._make_connection("siteabcdef.onion", 80), "tor")

        socks.assert_called_once_with("siteabcdef.onion", 80)
        direct.assert_not_called()

    def test_make_connection_uses_direct_connection_in_clear_mode(self):
        proxy_server.configure_state(BrowserState(BrowsingMode.CLEAR))
        with patch.object(proxy_server, "_socks5_connect", return_value="tor") as socks:
            with patch.object(proxy_server, "_direct_connect", return_value="clear") as direct:
                self.assertEqual(proxy_server._make_connection("example.com", 443), "clear")

        direct.assert_called_once_with("example.com", 443)
        socks.assert_not_called()

    def test_rewrite_onion_response_repairs_https_urls_and_adds_cors(self):
        # CSP has one safe directive (default-src) and one blocking directive.
        # Only the blocking directive should be stripped; default-src must survive.
        response = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/javascript\r\n"
            b"Content-Security-Policy: default-src https:; upgrade-insecure-requests\r\n"
            b"Content-Length: 113\r\n"
            b"\r\n"
            b"fetch('https://siteabcdef.onion/blog-data.json');"
            b"const escaped = 'https:\\/\\/siteabcdef.onion/pages/blog-data.json';"
        )

        rewritten = proxy_server._rewrite_onion_response(
            "siteabcdef.onion",
            "http://siteabcdef.onion",
            response,
        )

        self.assertIn(b"Access-Control-Allow-Origin: http://siteabcdef.onion", rewritten)
        self.assertIn(b"Access-Control-Allow-Credentials: true", rewritten)
        # Harmful directive removed
        self.assertNotIn(b"upgrade-insecure-requests", rewritten)
        # Safe directive preserved
        self.assertIn(b"Content-Security-Policy", rewritten)
        self.assertIn(b"default-src https:", rewritten)
        self.assertIn(b"fetch('http://siteabcdef.onion/blog-data.json')", rewritten)
        self.assertIn(b"http:\\/\\/siteabcdef.onion/pages/blog-data.json", rewritten)
        self.assertNotIn(b"https://siteabcdef.onion", rewritten)

    def test_rewrite_onion_response_handles_chunked_text_body(self):
        chunk = b"fetch('https://siteabcdef.onion/blog-data.json');"
        response = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/json\r\n"
            b"Transfer-Encoding: chunked\r\n"
            b"\r\n" +
            f"{len(chunk):x}".encode("ascii") + b"\r\n" +
            chunk + b"\r\n0\r\n\r\n"
        )

        rewritten = proxy_server._rewrite_onion_response(
            "siteabcdef.onion",
            "http://siteabcdef.onion",
            response,
        )

        self.assertNotIn(b"Transfer-Encoding: chunked", rewritten)
        self.assertIn(b"Content-Length:", rewritten)
        self.assertIn(b"fetch('http://siteabcdef.onion/blog-data.json')", rewritten)


if __name__ == "__main__":
    unittest.main()
