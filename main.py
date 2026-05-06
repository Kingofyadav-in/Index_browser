import os
import sys

os.environ.setdefault("FONTCONFIG_PATH", "/etc/fonts")

# proxy must start and env vars must be set BEFORE QApplication is created
import proxy_server
from browser_state import BrowserState
from router import BrowsingMode
from config.settings import (
    APP_NAME,
    IGNORE_CERTIFICATE_ERRORS,
    DISABLE_WEB_SECURITY,
    WEB_CACHE_DIR,
    WEB_STORAGE_DIR,
    WEB_CACHE_SIZE_MB,
)

_state = BrowserState(BrowsingMode.CLEAR)
proxy_server.configure_state(_state)
_proxy_port = proxy_server.start()

_chromium_flags = [
    f"--proxy-server=http://127.0.0.1:{_proxy_port}",
    "--enable-gpu-rasterization",
    "--enable-zero-copy",
    "--enable-quic",
    "--enable-tcp-fast-open",
]
if IGNORE_CERTIFICATE_ERRORS:
    _chromium_flags.append("--ignore-certificate-errors")
if DISABLE_WEB_SECURITY:
    _chromium_flags.append("--disable-web-security")
os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = " ".join(_chromium_flags)

from PyQt6.QtWidgets import QApplication
from PyQt6.QtWebEngineCore import QWebEngineProfile, QWebEngineScript, QWebEngineSettings
from tor_client import TorClient
from router import Router
from search import SearchEngine
from ui.main_window import MainWindow
from ui.request_interceptor import OnionRequestInterceptor
from ui.web_page import _onion_url_normalizer_script


def _set_web_attribute(settings, name: str, enabled: bool):
    try:
        settings.setAttribute(getattr(QWebEngineSettings.WebAttribute, name), enabled)
    except AttributeError:
        pass


def _configure_web_profile(profile: QWebEngineProfile):
    os.makedirs(WEB_CACHE_DIR, exist_ok=True)
    os.makedirs(WEB_STORAGE_DIR, exist_ok=True)

    profile.setCachePath(WEB_CACHE_DIR)
    profile.setPersistentStoragePath(WEB_STORAGE_DIR)
    profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.DiskHttpCache)
    profile.setHttpCacheMaximumSize(WEB_CACHE_SIZE_MB * 1024 * 1024)
    profile.setPersistentCookiesPolicy(
        QWebEngineProfile.PersistentCookiesPolicy.AllowPersistentCookies
    )

    settings = profile.settings()
    _set_web_attribute(settings, "DnsPrefetchEnabled", False)  # proxy handles all DNS
    _set_web_attribute(settings, "LocalStorageEnabled", True)
    _set_web_attribute(settings, "WebGLEnabled", True)
    _set_web_attribute(settings, "Accelerated2dCanvasEnabled", True)
    _set_web_attribute(settings, "ScrollAnimatorEnabled", True)


def _install_global_scripts(profile: QWebEngineProfile):
    script = QWebEngineScript()
    script.setName("IndexOnionUrlNormalizer")
    script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
    script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
    script.setRunsOnSubFrames(True)
    script.setSourceCode(_onion_url_normalizer_script())
    profile.scripts().insert(script)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)

    profile = QWebEngineProfile.defaultProfile()
    _configure_web_profile(profile)
    _install_global_scripts(profile)

    # Rewrite https://*.onion → http://*.onion  (wss → ws)
    _interceptor = OnionRequestInterceptor()
    profile.setUrlRequestInterceptor(_interceptor)

    tor    = TorClient()
    proxy_server.configure_tor_client(tor)
    router = Router(tor, _state)
    search = SearchEngine(tor_client=tor)

    window = MainWindow(router=router, search=search, tor_client=tor)
    window.show()

    try:
        exit_code = app.exec()
    finally:
        proxy_server.stop()
        tor.stop()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
