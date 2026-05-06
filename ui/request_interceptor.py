import ipaddress

from PyQt6.QtWebEngineCore import QWebEngineUrlRequestInterceptor
from PyQt6.QtCore import QUrl

from config.settings import BLOCK_CLEARNET_SUBRESOURCES_ON_ONION
from url_utils import is_onion_host

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "index.new"}
_BLOCKED_SCHEMES = {"http", "https", "ws", "wss"}


def _is_private_ip(host: str) -> bool:
    """Return True for RFC-1918 / loopback / link-local addresses."""
    try:
        return ipaddress.ip_address(host).is_private
    except ValueError:
        return False


class OnionRequestInterceptor(QWebEngineUrlRequestInterceptor):
    """
    Rewrites https://*.onion/* → http://*.onion/* before the request is sent.
    .onion services are end-to-end encrypted by the Tor protocol itself, so
    HTTPS is redundant and port 443 is usually not configured on .onion servers.
    Without this, CSS/JS/images served at https:// URLs silently fail to load.

    Also blocks clearnet and RFC-1918 subresource requests originating from
    onion pages to prevent network-level deanonymisation and local network probing.
    """

    def interceptRequest(self, info):
        url = info.requestUrl()
        host = url.host()
        first_party_host = info.firstPartyUrl().host()

        if self._should_block_onion_subresource(url, host, first_party_host):
            info.block(True)
            return

        if not is_onion_host(host):
            return
        scheme = url.scheme()
        # https → http   (CSS/JS/images — port 443 rarely configured on .onion)
        # wss   → ws     (WebSocket over TLS unnecessary; Tor already encrypts)
        if scheme == "https":
            downgraded = QUrl(url)
            downgraded.setScheme("http")
            info.redirect(downgraded)
        elif scheme == "wss":
            downgraded = QUrl(url)
            downgraded.setScheme("ws")
            info.redirect(downgraded)

    @staticmethod
    def _should_block_onion_subresource(url: QUrl, host: str, first_party_host: str) -> bool:
        if not BLOCK_CLEARNET_SUBRESOURCES_ON_ONION:
            return False
        if not first_party_host or not is_onion_host(first_party_host):
            return False
        if not host:
            return False
        # Allow onion and known-local hosts
        if host in _LOCAL_HOSTS or is_onion_host(host):
            return False
        # Block RFC-1918 / loopback — prevents onion pages probing the local network
        if _is_private_ip(host):
            return True
        return url.scheme() in _BLOCKED_SCHEMES
