import os
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtCore import QUrl, pyqtSignal
from PyQt6.QtWidgets import QWidget, QVBoxLayout
from router import BrowsingMode
import proxy_server
from .web_page import WebPage
from url_utils import normalize_url

_HOME_HTML = os.path.join(os.path.dirname(__file__), "home.html")


class BrowserTab(QWidget):
    url_changed = pyqtSignal(str)
    title_changed = pyqtSignal(str)
    load_finished = pyqtSignal(bool)
    load_progress = pyqtSignal(int)
    js_log = pyqtSignal(int, str, int, str)   # forwarded from WebPage.log_entry

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mode = BrowsingMode.CLEAR
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._view = QWebEngineView()
        self._page = WebPage(self._view)
        self._view.setPage(self._page)
        self._page.log_entry.connect(self.js_log)
        self._view.urlChanged.connect(lambda u: self.url_changed.emit(u.toString()))
        self._view.titleChanged.connect(self.title_changed)
        self._view.loadFinished.connect(self.load_finished)
        self._view.loadProgress.connect(self.load_progress)
        layout.addWidget(self._view)

    def set_mode(self, mode: BrowsingMode):
        self._mode = mode

    def load(self, url: str):
        if url in ("index://newtab/", "index://newtab"):
            self._load_home()
            return
        if not url.startswith(("http://", "https://", "ftp://")):
            url = normalize_url(url)
        self._view.load(QUrl(url))

    def _load_home(self):
        """Load home page via setHtml() so JS runs without custom-scheme restrictions.
        baseUrl = our proxy's loopback address so fetch('/__status__') is same-origin."""
        try:
            with open(_HOME_HTML, "rb") as f:
                html = f.read()
        except FileNotFoundError:
            self._view.setHtml("<h1>Home page not found</h1>")
            return
        port = proxy_server.get_port()
        html = html.replace(b"__STATUS_URL__", f"http://127.0.0.1:{port}/__status__".encode())
        base = QUrl(f"http://127.0.0.1:{port}/")
        self._view.setHtml(html.decode("utf-8"), base)

    def back(self):
        self._view.back()

    def forward(self):
        self._view.forward()

    def reload(self):
        tab_url = self._view.url().toString()
        if not tab_url or tab_url in ("about:blank", f"http://127.0.0.1:{proxy_server.get_port()}/"):
            self._load_home()
        else:
            self._view.reload()

    def current_url(self) -> str:
        return self._view.url().toString()

    def current_title(self) -> str:
        return self._view.title()
