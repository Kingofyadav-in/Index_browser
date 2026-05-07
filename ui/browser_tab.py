import os
from html import escape
from urllib.parse import quote, quote_plus, urlparse

from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEnginePage
from PyQt6.QtCore import QUrl, pyqtSignal, QTimer, Qt
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QMenu, QApplication
from router import BrowsingMode
import proxy_server
from .web_page import WebPage
from url_utils import normalize_url

_HOME_HTML = os.path.join(os.path.dirname(__file__), "home.html")


class BrowserTab(QWidget):
    url_changed           = pyqtSignal(str)
    title_changed         = pyqtSignal(str)
    load_started          = pyqtSignal()
    load_finished         = pyqtSignal(bool)
    load_progress         = pyqtSignal(int)
    js_log                = pyqtSignal(int, str, int, str)
    favicon_changed       = pyqtSignal(object)
    onion_navigation      = pyqtSignal(str)
    special_action        = pyqtSignal(str, str)
    devtools_requested    = pyqtSignal()       # right-click Inspect / F12
    view_source_requested = pyqtSignal(str)    # right-click View Source
    new_tab_requested     = pyqtSignal(str)    # right-click Open Link in New Tab

    IS_DEVTOOLS = False

    def __init__(self, incognito: bool = False, profile=None, parent=None):
        super().__init__(parent)
        self._mode       = BrowsingMode.CLEAR
        self._incognito  = incognito
        self._profile    = profile   # None  = default profile; pass OTR for incognito
        self._failed_url = ""
        self._in_recovery = False
        self._build_ui()

    @property
    def is_incognito(self) -> bool:
        return self._incognito

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._view = QWebEngineView()
        self._page = WebPage(profile=self._profile, parent=self._view)
        self._view.setPage(self._page)

        self._page.log_entry.connect(self.js_log)
        self._page.onion_navigation_detected.connect(self.onion_navigation)
        self._page.special_action.connect(self.special_action)

        self._view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._view.customContextMenuRequested.connect(self._on_context_menu)

        self._view.urlChanged.connect(lambda u: self.url_changed.emit(u.toString()))
        self._view.titleChanged.connect(self.title_changed)
        self._view.loadStarted.connect(self.load_started)
        self._view.loadFinished.connect(self._on_load_finished_internal)
        self._view.loadProgress.connect(self.load_progress)
        self._view.iconChanged.connect(lambda icon: self.favicon_changed.emit(icon))

        layout.addWidget(self._view)

    # ── Context menu ──────────────────────────────────────────────────────

    _MENU_STYLE = """
        QMenu {
            background: #232328; color: #f0f0f5;
            border: 1px solid #44444a; border-radius: 8px;
            padding: 4px 0; font-size: 13px;
        }
        QMenu::item { padding: 7px 20px 7px 14px; border-radius: 4px; margin: 1px 4px; }
        QMenu::item:selected { background: #46464c; }
        QMenu::separator { background: #3a3a3e; height: 1px; margin: 3px 10px; }
    """

    def _on_context_menu(self, pos):
        req  = self._view.lastContextMenuRequest()
        menu = self._view.createStandardContextMenu()
        if menu is None:
            menu = QMenu(self._view)
        menu.setStyleSheet(self._MENU_STYLE)

        # Extra link actions (before the divider to inspector)
        if req is not None and req.linkUrl().isValid():
            link = req.linkUrl().toString()
            menu.addSeparator()
            menu.addAction("🔗  Open Link in New Tab",
                lambda url=link: self.new_tab_requested.emit(url))
            menu.addAction("📋  Copy Link Address",
                lambda url=link: QApplication.clipboard().setText(url))

        menu.addSeparator()
        menu.addAction("🔧  Inspect Element", self._emit_inspect)
        menu.addAction("📄  View Page Source", self._emit_view_source)

        menu.exec(self._view.mapToGlobal(pos))

    def _emit_inspect(self):
        self.devtools_requested.emit()

    def _emit_view_source(self):
        url = self._page.requestedUrl().toString() or self.current_url()
        if url.startswith(("http://", "https://")):
            self.view_source_requested.emit(f"view-source:{url}")

    # ── Internal load-finished handler ────────────────────────────────────

    def _on_load_finished_internal(self, ok: bool):
        if self._in_recovery:
            self._in_recovery = False
            self.load_finished.emit(ok)
            return
        if not ok:
            failed = self._view.page().requestedUrl().toString()
            if (failed.startswith(("http://", "https://")) and
                    "127.0.0.1" not in failed and
                    "localhost" not in failed):
                self._failed_url = failed
                self._show_recovery(failed)
                return
        else:
            cur = self._view.url().toString()
            if cur.startswith(("http://", "https://", "ftp://")):
                self._failed_url = ""
        self.load_finished.emit(ok)

    def _show_recovery(self, url: str):
        self._in_recovery = True
        self._view.setHtml(_make_recovery_html(url), QUrl("index://recovery/"))

    # ── Navigation ────────────────────────────────────────────────────────

    def set_mode(self, mode: BrowsingMode):
        self._mode = mode

    def load(self, url: str):
        if url in ("index://newtab/", "index://newtab"):
            self._load_home()
            return
        if not url.startswith(("http://", "https://", "ftp://", "view-source:")):
            url = normalize_url(url)
        self._view.load(QUrl(url))

    def _load_home(self):
        try:
            with open(_HOME_HTML, "rb") as f:
                html = f.read()
        except FileNotFoundError:
            self._view.setHtml("<h1>Home page not found</h1>")
            return
        port = proxy_server.get_port()
        html = html.replace(b"__STATUS_URL__", f"http://127.0.0.1:{port}/__status__".encode())
        self._view.setHtml(html.decode("utf-8"), QUrl(f"http://127.0.0.1:{port}/"))

    def back(self):    self._view.back()
    def forward(self): self._view.forward()
    def stop(self):    self._view.stop()

    def reload(self):
        if self._failed_url:
            url, self._failed_url = self._failed_url, ""
            self.load(url)
            return
        tab_url = self._view.url().toString()
        if not tab_url or tab_url in ("about:blank",
                                       f"http://127.0.0.1:{proxy_server.get_port()}/"):
            self._load_home()
        else:
            self._view.reload()

    def current_url(self) -> str:  return self._view.url().toString()
    def current_title(self) -> str: return self._view.title()


# ── DevTools tab ──────────────────────────────────────────────────────────

class _DevToolsPage(QWebEnginePage):
    """QWebEnginePage subclass for DevTools that suppresses Autofill noise."""
    _SUPPRESS = frozenset([
        "Request Autofill.enable failed",
        "Request Autofill.setAddresses failed",
        "wasn't found",
    ])

    def javaScriptConsoleMessage(self, level, message, line_number, source_id):
        if any(s in message for s in self._SUPPRESS):
            return
        super().javaScriptConsoleMessage(level, message, line_number, source_id)


class DevToolsTab(QWidget):
    """Thin tab wrapper around a DevTools QWebEnginePage.
    Exposes the same public interface as BrowserTab so MainWindow
    can handle both types uniformly."""

    title_changed    = pyqtSignal(str)
    load_finished    = pyqtSignal(bool)
    load_started     = pyqtSignal()
    load_progress    = pyqtSignal(int)
    url_changed      = pyqtSignal(str)
    favicon_changed  = pyqtSignal(object)
    onion_navigation = pyqtSignal(str)
    special_action   = pyqtSignal(str, str)
    js_log           = pyqtSignal(int, str, int, str)
    devtools_requested    = pyqtSignal()
    view_source_requested = pyqtSignal(str)
    new_tab_requested     = pyqtSignal(str)

    IS_DEVTOOLS  = True
    is_incognito = False

    def __init__(self, inspected_page, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._view = QWebEngineView()
        self._page = _DevToolsPage(self._view)
        self._view.setPage(self._page)
        inspected_page.setDevToolsPage(self._page)

        self._view.titleChanged.connect(self.title_changed)
        self._view.loadFinished.connect(self.load_finished)
        self._view.loadStarted.connect(self.load_started)
        self._view.loadProgress.connect(self.load_progress)
        self._view.urlChanged.connect(lambda u: self.url_changed.emit(u.toString()))
        self._view.iconChanged.connect(lambda i: self.favicon_changed.emit(i))

        layout.addWidget(self._view)

    def set_mode(self, mode): pass
    def load(self, url):      pass
    def back(self):    self._view.back()
    def forward(self): self._view.forward()
    def reload(self):  self._view.reload()
    def stop(self):    self._view.stop()
    def current_url(self) -> str:   return self._view.url().toString()
    def current_title(self) -> str: return self._view.title()


# ── Recovery page ─────────────────────────────────────────────────────────

def _make_recovery_html(url: str) -> str:
    """Return a dark-themed HTML recovery page for the given failed URL."""
    try:
        netloc = urlparse(url).netloc or url
    except Exception:
        netloc = url
    url_enc = quote(url, safe="")
    q_enc   = quote_plus(netloc)
    display = escape(url)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Can't reach this page</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{
  background:#1e1e21;color:#f0f0f5;
  font-family:'Segoe UI',system-ui,-apple-system,sans-serif;
  display:flex;flex-direction:column;align-items:center;
  justify-content:center;min-height:100vh;padding:40px 20px;gap:0;
}}
.icon{{font-size:52px;margin-bottom:20px;opacity:.4;user-select:none}}
h1{{font-size:22px;font-weight:600;margin-bottom:8px}}
.desc{{color:#9898a0;font-size:13px;margin-bottom:8px}}
.furl{{
  color:#6868a0;font-size:11px;font-family:monospace;
  margin-bottom:32px;word-break:break-all;max-width:580px;
  text-align:center;line-height:1.6;
}}
.actions{{display:flex;flex-wrap:wrap;gap:10px;justify-content:center;margin-bottom:36px}}
.btn{{
  padding:10px 22px;border-radius:8px;font-size:13px;font-weight:500;
  text-decoration:none;cursor:pointer;border:none;
  transition:filter .15s;display:inline-flex;align-items:center;gap:6px;
  white-space:nowrap;
}}
.btn:hover{{filter:brightness(1.2)}}
.r{{background:#0a84ff;color:#fff}}
.t{{background:#6e3fa3;color:#fff}}
.a{{background:#1d5c35;color:#d4edda}}
.s{{background:#28282c;color:#f0f0f5;border:1px solid #44444a;padding:8px 18px;font-size:12px}}
hr{{width:100%;max-width:580px;border:none;border-top:1px solid #3a3a3e;margin-bottom:24px}}
.slabel{{font-size:12px;color:#9898a0;font-weight:500;margin-bottom:12px}}
.srow{{display:flex;flex-wrap:wrap;gap:8px;justify-content:center}}
</style>
</head>
<body>
<div class="icon">⚠</div>
<h1>Can't reach this page</h1>
<p class="desc">The page may be down, or you may have a connection problem.</p>
<p class="furl">{display}</p>
<div class="actions">
  <a class="btn r" href="index://retry/?url={url_enc}">&#8635;&nbsp;Retry</a>
  <a class="btn t" href="index://try-tor/?url={url_enc}">&#129797;&nbsp;Try via Tor</a>
  <a class="btn a" href="index://archive/?url={url_enc}">&#128230;&nbsp;Wayback Machine</a>
</div>
<hr>
<div class="slabel">Search for it instead</div>
<div class="srow">
  <a class="btn s" href="index://search/?engine=brave&q={q_enc}">Brave</a>
  <a class="btn s" href="index://search/?engine=google&q={q_enc}">Google</a>
  <a class="btn s" href="index://search/?engine=ddg&q={q_enc}">DuckDuckGo</a>
  <a class="btn s" href="index://search/?engine=bing&q={q_enc}">Bing</a>
  <a class="btn s" href="index://search/?engine=wiki&q={q_enc}">Wikipedia</a>
</div>
</body>
</html>"""
