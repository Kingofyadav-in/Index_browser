from PyQt6.QtWidgets import (
    QMainWindow, QTabWidget, QPushButton,
    QWidget, QVBoxLayout, QStatusBar, QProgressBar
)
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWebEngineCore import QWebEngineProfile
from .toolbar import Toolbar
from .browser_tab import BrowserTab
from .log_panel import LogPanel
from router import Router, BrowsingMode
from search import SearchEngine
from config.settings import (
    APP_NAME, WINDOW_WIDTH, WINDOW_HEIGHT,
    DEFAULT_HOME_URL_CLEAR, DEFAULT_HOME_URL_TOR
)
from url_utils import forces_tor

# Matches Tor Browser's UA — prevents fingerprinting by version in Tor mode.
_TOR_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; rv:109.0) Gecko/20100101 Firefox/109.0"


class TorConnectThread(QThread):
    connected = pyqtSignal(bool)

    def __init__(self, tor_client):
        super().__init__()
        self._tor = tor_client

    def run(self):
        ok = self._tor.start()
        self.connected.emit(ok)


class MainWindow(QMainWindow):
    def __init__(self, router: Router, search: SearchEngine, tor_client):
        super().__init__()
        self._router = router
        self._search = search
        self._tor = tor_client
        self._tor_thread = None
        self._default_ua = QWebEngineProfile.defaultProfile().httpUserAgent()
        self._setup_ui()
        self._connect_tor_async()

    def _setup_ui(self):
        self.setWindowTitle(APP_NAME)
        self.resize(WINDOW_WIDTH, WINDOW_HEIGHT)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._toolbar = Toolbar()
        self._toolbar.navigate_requested.connect(self._navigate)
        self._toolbar.mode_changed.connect(self._on_mode_change)
        self._toolbar.new_identity_requested.connect(self._new_identity)
        self._toolbar.back_requested.connect(lambda: self._current_tab().back())
        self._toolbar.forward_requested.connect(lambda: self._current_tab().forward())
        self._toolbar.reload_requested.connect(lambda: self._current_tab().reload())

        self._tabs = QTabWidget()
        self._tabs.setTabsClosable(True)
        self._tabs.tabCloseRequested.connect(self._close_tab)
        self._tabs.currentChanged.connect(self._on_tab_switch)
        # NOTE: do NOT call setDocumentMode(True) — it hides the corner widget

        # "+" button in the tab-bar corner
        new_tab_btn = QPushButton("  +  ")
        new_tab_btn.setToolTip("New Tab  (Ctrl+T)")
        new_tab_btn.setStyleSheet("""
            QPushButton {
                border: 1px solid #444;
                border-radius: 5px;
                font-size: 15px;
                font-weight: bold;
                color: #ccc;
                background: #1a1a2e;
                padding: 2px 8px;
                margin: 2px 4px;
            }
            QPushButton:hover {
                background: #2e1f5e;
                color: #fff;
                border-color: #7c6fff;
            }
        """)
        new_tab_btn.clicked.connect(self._open_tab)
        self._tabs.setCornerWidget(new_tab_btn)

        root.addWidget(self._toolbar)
        root.addWidget(self._tabs)

        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setFixedWidth(150)
        self._progress.setTextVisible(False)
        self._progress.hide()
        self._status.addPermanentWidget(self._progress)

        self._log_panel = LogPanel(self)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self._log_panel)
        self._log_panel.hide()

        QShortcut(QKeySequence("Ctrl+T"), self).activated.connect(self._open_tab)
        QShortcut(QKeySequence("Ctrl+W"), self).activated.connect(
            lambda: self._close_tab(self._tabs.currentIndex())
        )
        _log_sc = QShortcut(QKeySequence("Ctrl+Shift+J"), self)
        _log_sc.setContext(Qt.ShortcutContext.ApplicationShortcut)
        _log_sc.activated.connect(self._toggle_log_panel)

        self._open_tab()

    def _open_tab(self, url: str = ""):
        tab = BrowserTab()
        tab.url_changed.connect(self._on_url_change)
        tab.title_changed.connect(self._on_title_change)
        tab.set_mode(self._router.get_mode())
        tab.js_log.connect(
            lambda level, message, line, source, t=tab:
                self._log_panel.add_js_log(level, f"{self._tab_log_label(t)} {message}", line, source)
        )
        tab.url_changed.connect(lambda url, t=tab: self._log_panel.add_nav(f"{self._tab_log_label(t)} {url}"))
        tab.load_finished.connect(lambda ok, t=tab: self._log_panel.add_load(ok, self._tab_log_label(t)))
        tab.load_finished.connect(self._on_load_finished)
        tab.load_progress.connect(self._on_load_progress)

        mode = self._router.get_mode()
        home = DEFAULT_HOME_URL_TOR if mode == BrowsingMode.TOR else DEFAULT_HOME_URL_CLEAR
        idx = self._tabs.addTab(tab, "New Tab")
        self._tabs.setCurrentIndex(idx)
        tab.load(url or home)

    def _close_tab(self, index: int):
        if self._tabs.count() > 1:
            self._tabs.removeTab(index)

    def _current_tab(self) -> BrowserTab:
        return self._tabs.currentWidget()

    def _navigate(self, text: str):
        # Auto-switch to Tor mode for .onion URLs so the user never has to
        # manually toggle the combobox before navigating to a .onion address.
        if forces_tor(text) and self._router.get_mode() != BrowsingMode.TOR:
            self._set_mode(BrowsingMode.TOR)

        if self._search.is_search_query(text):
            url = self._search.build_url(text, self._router.get_mode())
        else:
            url = text
        tab = self._current_tab()
        tab.set_mode(self._router.get_mode())
        tab.load(url)

    def _set_mode(self, mode: BrowsingMode):
        """Switch mode programmatically and keep the toolbar combobox in sync."""
        self._router.set_mode(mode)
        self._toolbar.set_mode_silent(mode)
        # Switch to Tor Browser UA in Tor mode to prevent version fingerprinting.
        ua = _TOR_USER_AGENT if mode == BrowsingMode.TOR else self._default_ua
        QWebEngineProfile.defaultProfile().setHttpUserAgent(ua)
        tab = self._current_tab()
        if tab:
            tab.set_mode(mode)

    def _on_mode_change(self, mode: BrowsingMode):
        self._set_mode(mode)
        tab = self._current_tab()
        if tab:
            tab.set_mode(mode)
        if mode == BrowsingMode.TOR and not self._tor.is_connected():
            self._status.showMessage("Connecting to Tor…")
            self._connect_tor_async()

    def _new_identity(self):
        ok = self._router.new_tor_identity()
        self._status.showMessage("New Tor identity obtained." if ok else "Failed to get new identity.")

    def _on_url_change(self, url: str):
        if self._tabs.currentWidget() == self.sender():
            self._toolbar.set_url(url)

    def _on_title_change(self, title: str):
        tab = self.sender()
        idx = self._tabs.indexOf(tab)
        if idx >= 0:
            self._tabs.setTabText(idx, title[:24] or "New Tab")

    def _on_load_progress(self, value: int):
        if self._tabs.currentWidget() != self.sender():
            return
        self._progress.setValue(value)
        self._progress.setVisible(value < 100)

    def _on_load_finished(self, _ok: bool):
        if self._tabs.currentWidget() == self.sender():
            self._progress.hide()

    def _tab_log_label(self, tab: BrowserTab) -> str:
        idx = self._tabs.indexOf(tab)
        return f"[Tab {idx + 1 if idx >= 0 else '?'}]"

    def _on_tab_switch(self, index: int):
        tab = self._tabs.widget(index)
        if tab:
            self._toolbar.set_url(tab.current_url())

    def _connect_tor_async(self):
        if self._tor_thread and self._tor_thread.isRunning():
            return
        self._tor_thread = TorConnectThread(self._tor)
        self._tor_thread.connected.connect(self._on_tor_connected)
        self._tor_thread.start()

    def _on_tor_connected(self, ok: bool):
        self._toolbar.set_tor_status(ok)
        self._status.showMessage("Tor connected." if ok else "Tor unavailable — running in clear mode.")

    def _toggle_log_panel(self):
        if self._log_panel.isVisible():
            self._log_panel.hide()
        else:
            self._log_panel.show()
