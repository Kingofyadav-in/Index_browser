import os
from urllib.parse import urlparse, parse_qs, quote_plus

_ICON_PATH = os.path.join(os.path.dirname(__file__), "assets", "icon.png")

from PyQt6.QtWidgets import (
    QMainWindow, QTabWidget, QPushButton,
    QWidget, QVBoxLayout, QStatusBar, QTabBar, QApplication,
)
from PyQt6.QtGui import QKeySequence, QShortcut, QIcon, QFont
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWebEngineCore import (
    QWebEngineProfile, QWebEngineScript, QWebEngineSettings,
)

from .toolbar import Toolbar
from .browser_tab import BrowserTab, DevToolsTab
from .log_panel import LogPanel
from .download_manager import DownloadPanel
from .request_interceptor import OnionRequestInterceptor
from .web_page import _onion_url_normalizer_script
from router import Router, BrowsingMode
from search import SearchEngine
from config.settings import (
    APP_NAME, WINDOW_WIDTH, WINDOW_HEIGHT,
    DEFAULT_HOME_URL_CLEAR, DEFAULT_HOME_URL_TOR,
)
from url_utils import forces_tor

_TOR_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; rv:109.0) Gecko/20100101 Firefox/109.0"

# ── Palette (matches toolbar.py) ──────────────────────────────────────────
_BG      = "#1e1e21"
_TOOLBAR = "#28282c"
_PANEL   = "#232328"
_BORDER  = "#44444a"
_TEXT    = "#f0f0f5"
_MUTED   = "#9898a0"
_ACCENT  = "#0a84ff"
_HOVER   = "#46464c"
_GREEN   = "#30d158"
_RED     = "#ff453a"

_APP_STYLE = f"""
/* ── Window ─────────────────────────────────────────────────────────── */
QMainWindow, QWidget {{
    background: {_BG};
    color: {_TEXT};
    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
}}

/* ── Tab widget ──────────────────────────────────────────────────────── */
QTabWidget::pane {{
    border: none;
    background: {_BG};
    margin-top: 0;
}}

QTabWidget > QTabBar {{
    left: 0;
    alignment: left;
}}

QTabBar {{
    background: {_TOOLBAR};
    border: none;
    border-bottom: 1px solid {_BORDER};
}}

QTabBar::tab {{
    background: {_TOOLBAR};
    color: {_MUTED};
    border: none;
    border-right: 1px solid #3a3a3e;
    border-bottom: 2px solid transparent;
    padding: 7px 14px 7px 10px;
    min-width: 90px;
    max-width: 210px;
    font-size: 12px;
    border-top-left-radius: 5px;
    border-top-right-radius: 5px;
}}

QTabBar::tab:selected {{
    background: {_BG};
    color: {_TEXT};
    border-bottom: 2px solid {_ACCENT};
    border-right: 1px solid {_BORDER};
}}

QTabBar::tab:hover:!selected {{
    background: {_HOVER};
    color: #c8c8d0;
}}

/* Scroll buttons when tabs overflow */
QTabBar QToolButton {{
    background: {_TOOLBAR};
    border: none;
    color: {_MUTED};
    padding: 4px;
    border-radius: 4px;
}}
QTabBar QToolButton:hover {{ background: {_HOVER}; color: {_TEXT}; }}

/* ── Status bar ──────────────────────────────────────────────────────── */
QStatusBar {{
    background: {_BG};
    color: {_MUTED};
    font-size: 11px;
    border-top: 1px solid {_BORDER};
}}
QStatusBar::item {{ border: none; }}

/* ── Dock widget (log panel) ─────────────────────────────────────────── */
QDockWidget {{
    background: {_PANEL};
    color: {_TEXT};
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
}}
QDockWidget::title {{
    background: {_TOOLBAR};
    padding: 5px 10px;
}}
QDockWidget::close-button,
QDockWidget::float-button {{
    background: transparent;
    border: none;
    padding: 2px;
    border-radius: 4px;
}}
QDockWidget::close-button:hover,
QDockWidget::float-button:hover {{ background: {_HOVER}; }}

/* ── Scrollbars ──────────────────────────────────────────────────────── */
QScrollBar:vertical {{
    background: {_BG};
    width: 8px;
    border: none;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: #58585e;
    border-radius: 4px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{ background: #76767e; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{
    background: {_BG};
    height: 8px;
    border: none;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background: #58585e;
    border-radius: 4px;
    min-width: 24px;
}}
QScrollBar::handle:horizontal:hover {{ background: #76767e; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

/* ── Tool tips ───────────────────────────────────────────────────────── */
QToolTip {{
    background: #2c2c30;
    color: {_TEXT};
    border: 1px solid {_BORDER};
    border-radius: 4px;
    padding: 4px 8px;
    font-size: 11px;
}}
"""


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
        self._router      = router
        self._search      = search
        self._tor         = tor_client
        self._tor_thread  = None
        self._default_ua  = QWebEngineProfile.defaultProfile().httpUserAgent()
        self._otr_profile = self._create_otr_profile()
        self._setup_ui()
        self._connect_tor_async()

    # ── UI Construction ────────────────────────────────────────────────────

    def _setup_ui(self):
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(QApplication.instance().windowIcon())
        self.resize(WINDOW_WIDTH, WINDOW_HEIGHT)
        self.setStyleSheet(_APP_STYLE)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Toolbar
        self._toolbar = Toolbar()
        self._toolbar.navigate_requested.connect(self._navigate)
        self._toolbar.mode_changed.connect(self._on_mode_change)
        self._toolbar.new_identity_requested.connect(self._new_identity)
        self._toolbar.back_requested.connect(lambda: self._current_tab().back())
        self._toolbar.forward_requested.connect(lambda: self._current_tab().forward())
        self._toolbar.reload_requested.connect(lambda: self._current_tab().reload())
        self._toolbar.stop_requested.connect(lambda: self._current_tab().stop())

        # Tab widget
        self._tabs = QTabWidget()
        self._tabs.setTabsClosable(False)   # using custom close buttons per tab
        self._tabs.currentChanged.connect(self._on_tab_switch)

        # "+" new-tab button in the tab-bar corner
        new_tab_btn = QPushButton("  +  ")
        new_tab_btn.setToolTip("New Tab  (Ctrl+T)")
        new_tab_btn.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        new_tab_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                border: none;
                border-radius: 5px;
                color: {_MUTED};
                padding: 2px 8px;
                margin: 2px 4px;
                font-size: 16px;
            }}
            QPushButton:hover  {{ background: {_HOVER}; color: {_TEXT}; }}
            QPushButton:pressed {{ background: #55555c; }}
        """)
        new_tab_btn.clicked.connect(self._open_tab)
        self._tabs.setCornerWidget(new_tab_btn, Qt.Corner.TopRightCorner)

        root.addWidget(self._toolbar)
        root.addWidget(self._tabs)

        # Status bar
        self._status = QStatusBar()
        self.setStatusBar(self._status)

        # Log panel
        self._log_panel = LogPanel(self)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self._log_panel)
        self._log_panel.hide()

        # Download panel
        self._dl_panel = DownloadPanel(self)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self._dl_panel)
        self._dl_panel.hide()
        self._dl_panel.active_count_changed.connect(self._toolbar.set_download_badge)
        self._toolbar.download_toggle.connect(self._toggle_dl_panel)
        QWebEngineProfile.defaultProfile().downloadRequested.connect(
            self._dl_panel.add_download)

        # ── Keyboard shortcuts ────────────────────────────────────────────
        QShortcut(QKeySequence("Ctrl+T"),       self).activated.connect(self._open_tab)
        QShortcut(QKeySequence("Ctrl+W"),       self).activated.connect(
            lambda: self._close_tab(self._tabs.currentIndex()))
        QShortcut(QKeySequence("Ctrl+L"),       self).activated.connect(
            self._toolbar.focus_address_bar)
        QShortcut(QKeySequence("Ctrl+R"),       self).activated.connect(
            lambda: self._current_tab().reload())
        QShortcut(QKeySequence("F5"),           self).activated.connect(
            lambda: self._current_tab().reload())
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self).activated.connect(
            lambda: self._current_tab().stop())
        QShortcut(QKeySequence("Alt+Left"),     self).activated.connect(
            lambda: self._current_tab().back())
        QShortcut(QKeySequence("Alt+Right"),    self).activated.connect(
            lambda: self._current_tab().forward())

        _log_sc = QShortcut(QKeySequence("Ctrl+Shift+J"), self)
        _log_sc.setContext(Qt.ShortcutContext.ApplicationShortcut)
        _log_sc.activated.connect(self._toggle_log_panel)

        _dl_sc = QShortcut(QKeySequence("Ctrl+J"), self)
        _dl_sc.setContext(Qt.ShortcutContext.ApplicationShortcut)
        _dl_sc.activated.connect(self._toggle_dl_panel)

        for seq in ("F12", "Ctrl+Shift+I"):
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.ShortcutContext.ApplicationShortcut)
            sc.activated.connect(self._open_devtools_current)

        _inc_sc = QShortcut(QKeySequence("Ctrl+Shift+N"), self)
        _inc_sc.setContext(Qt.ShortcutContext.ApplicationShortcut)
        _inc_sc.activated.connect(self._open_incognito_tab)

        self._toolbar.incognito_requested.connect(self._open_incognito_tab)

        self._open_tab()

    # ── Tab management ─────────────────────────────────────────────────────

    def _open_tab(self, url: str = "", incognito: bool = False):
        profile = self._otr_profile if incognito else None
        tab = BrowserTab(incognito=incognito, profile=profile)

        tab.url_changed.connect(self._on_url_change)
        tab.title_changed.connect(self._on_title_change)
        tab.favicon_changed.connect(
            lambda icon, t=tab: self._on_favicon_change(t, icon))
        tab.load_started.connect(
            lambda t=tab: self._on_load_started(t))
        tab.load_finished.connect(
            lambda ok, t=tab: self._on_load_finished_tab(t, ok))
        tab.load_progress.connect(
            lambda v, t=tab: self._on_load_progress_tab(t, v))
        tab.onion_navigation.connect(self._on_onion_navigation)
        tab.special_action.connect(
            lambda action, idx_url, t=tab: self._on_special_action(t, action, idx_url))
        tab.devtools_requested.connect(
            lambda t=tab: self._open_devtools_for(t))
        tab.view_source_requested.connect(self._open_source_tab)
        tab.new_tab_requested.connect(
            lambda u, t=tab: self._open_tab(url=u, incognito=t.is_incognito))
        tab.js_log.connect(
            lambda lv, msg, line, src, t=tab:
                self._log_panel.add_js_log(lv, f"{self._tab_label(t)} {msg}", line, src))
        tab.url_changed.connect(
            lambda url, t=tab: self._log_panel.add_nav(f"{self._tab_label(t)} {url}"))
        tab.load_finished.connect(
            lambda ok, t=tab: self._log_panel.add_load(ok, self._tab_label(t)))
        tab.set_mode(self._router.get_mode())

        mode = self._router.get_mode()
        home = DEFAULT_HOME_URL_TOR if mode == BrowsingMode.TOR else DEFAULT_HOME_URL_CLEAR
        label = "🕵 New Tab" if incognito else "New Tab"
        idx = self._tabs.addTab(tab, label)

        # Custom close button
        close_btn = QPushButton("×")
        close_btn.setFixedSize(18, 18)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                border: none;
                border-radius: 9px;
                color: {_MUTED};
                font-size: 15px;
                font-weight: bold;
                padding: 0;
                line-height: 1;
            }}
            QPushButton:hover  {{ background: #60606a; color: {_TEXT}; }}
            QPushButton:pressed {{ background: #75757f; }}
        """)
        close_btn.clicked.connect(lambda: self._close_tab(self._tabs.indexOf(tab)))
        self._tabs.tabBar().setTabButton(idx, QTabBar.ButtonPosition.RightSide, close_btn)

        self._tabs.setCurrentIndex(idx)
        tab.load(url or home)

    def _close_tab(self, index: int):
        if self._tabs.count() <= 1:
            self._status.showMessage("Can't close the last tab.", 3000)
            return
        self._tabs.removeTab(index)

    def _current_tab(self) -> BrowserTab:
        return self._tabs.currentWidget()

    # ── Navigation ─────────────────────────────────────────────────────────

    def _navigate(self, text: str):
        if forces_tor(text) and self._router.get_mode() != BrowsingMode.TOR:
            self._set_mode(BrowsingMode.TOR)
        url = self._search.build_url(text, self._router.get_mode()) \
              if self._search.is_search_query(text) else text
        tab = self._current_tab()
        tab.set_mode(self._router.get_mode())
        tab.load(url)

    def _on_onion_navigation(self, url: str):
        """Switch to Tor mode when any tab navigates to a .onion / Tor-forced URL."""
        if self._router.get_mode() != BrowsingMode.TOR:
            self._set_mode(BrowsingMode.TOR)
            if not self._tor.is_connected():
                self._status.showMessage("Connecting to Tor…")
                self._connect_tor_async()

    _SEARCH_ENGINES = {
        "brave":  "https://search.brave.com/search?q={}",
        "google": "https://www.google.com/search?q={}",
        "ddg":    "https://duckduckgo.com/?q={}",
        "bing":   "https://www.bing.com/search?q={}",
        "wiki":   "https://en.wikipedia.org/w/index.php?search={}",
    }

    def _on_special_action(self, tab: "BrowserTab", action: str, index_url: str):
        """Handle recovery-page actions: retry, try-tor, archive, search."""
        params = parse_qs(urlparse(index_url).query)
        url = params.get("url", [""])[0]

        if action == "retry":
            tab.load(url)

        elif action == "try-tor":
            self._set_mode(BrowsingMode.TOR)
            if not self._tor.is_connected():
                self._status.showMessage("Connecting to Tor…")
                self._connect_tor_async()
            tab.load(url)

        elif action == "archive":
            tab.load(f"https://web.archive.org/web/{url}")

        elif action == "search":
            engine = params.get("engine", ["brave"])[0]
            q = params.get("q", [""])[0]
            template = self._SEARCH_ENGINES.get(engine, self._SEARCH_ENGINES["brave"])
            tab.load(template.format(quote_plus(q)))

    # ── Mode switching ─────────────────────────────────────────────────────

    def _set_mode(self, mode: BrowsingMode):
        """Switch mode and keep toolbar + UA in sync."""
        self._router.set_mode(mode)
        self._toolbar.set_mode_silent(mode)
        ua = _TOR_USER_AGENT if mode == BrowsingMode.TOR else self._default_ua
        QWebEngineProfile.defaultProfile().setHttpUserAgent(ua)
        tab = self._current_tab()
        if tab:
            tab.set_mode(mode)

    def _on_mode_change(self, mode: BrowsingMode):
        self._set_mode(mode)
        if mode == BrowsingMode.TOR and not self._tor.is_connected():
            self._status.showMessage("Connecting to Tor…")
            self._connect_tor_async()

    # ── Tor identity ───────────────────────────────────────────────────────

    def _new_identity(self):
        ok = self._router.new_tor_identity()
        if ok:
            self._status.showMessage("New Tor identity obtained.", 4000)
        else:
            self._status.showMessage(
                "Please wait 10 seconds before requesting a new identity.", 4000)

    # ── Signal handlers ────────────────────────────────────────────────────

    def _on_url_change(self, url: str):
        if self._tabs.currentWidget() == self.sender():
            if url and url not in ("about:blank",):
                self._toolbar.set_url(url)

    def _on_title_change(self, title: str):
        tab = self.sender()
        idx = self._tabs.indexOf(tab)
        if idx < 0:
            return
        if getattr(tab, "IS_DEVTOOLS", False):
            self._tabs.setTabText(idx, "🔧 DevTools")
        else:
            prefix = "🕵 " if getattr(tab, "is_incognito", False) else ""
            label = (title[:28] + "…") if len(title) > 28 else title or "New Tab"
            self._tabs.setTabText(idx, prefix + label)

    def _on_favicon_change(self, tab: BrowserTab, icon):
        idx = self._tabs.indexOf(tab)
        if idx >= 0 and not icon.isNull():
            self._tabs.setTabIcon(idx, icon)

    def _on_load_started(self, tab: BrowserTab):
        if self._tabs.currentWidget() == tab:
            self._toolbar.set_loading(True)

    def _on_load_finished_tab(self, tab: BrowserTab, ok: bool):
        if self._tabs.currentWidget() == tab:
            self._toolbar.set_loading(False)
            self._toolbar.set_progress(100)

    def _on_load_progress_tab(self, tab: BrowserTab, value: int):
        if self._tabs.currentWidget() == tab:
            self._toolbar.set_progress(value)

    def _on_tab_switch(self, index: int):
        tab = self._tabs.widget(index)
        if tab:
            self._toolbar.set_url(tab.current_url())
            self._toolbar.set_incognito_indicator(
                getattr(tab, "is_incognito", False))

    def _tab_label(self, tab: BrowserTab) -> str:
        idx = self._tabs.indexOf(tab)
        return f"[Tab {idx + 1 if idx >= 0 else '?'}]"

    # ── Tor connection ─────────────────────────────────────────────────────

    def _connect_tor_async(self):
        if self._tor_thread and self._tor_thread.isRunning():
            return
        self._tor_thread = TorConnectThread(self._tor)
        self._tor_thread.connected.connect(self._on_tor_connected)
        self._tor_thread.start()

    def _on_tor_connected(self, ok: bool):
        self._toolbar.set_tor_status(ok)
        self._status.showMessage(
            "Tor connected." if ok else "Tor unavailable — running in clear mode.", 5000)

    # ── OTR / Incognito profile ────────────────────────────────────────────

    def _create_otr_profile(self) -> QWebEngineProfile:
        """Create an off-the-record (incognito) profile with same settings
        as the default profile but no disk persistence."""
        profile = QWebEngineProfile(self)   # no name → off-the-record

        settings = profile.settings()
        for attr, val in [
            ("DnsPrefetchEnabled",         False),
            ("LocalStorageEnabled",         True),
            ("WebGLEnabled",                True),
            ("Accelerated2dCanvasEnabled",  True),
            ("ScrollAnimatorEnabled",       True),
            ("FullScreenSupportEnabled",    True),
            ("PlaybackRequiresUserGesture", False),
            ("JavascriptEnabled",           True),
            ("PluginsEnabled",              False),
            ("HyperlinkAuditingEnabled",    False),
            ("PdfViewerEnabled",            True),
        ]:
            try:
                settings.setAttribute(
                    getattr(QWebEngineSettings.WebAttribute, attr), val)
            except AttributeError:
                pass

        script = QWebEngineScript()
        script.setName("IndexOnionUrlNormalizer")
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setRunsOnSubFrames(True)
        script.setSourceCode(_onion_url_normalizer_script())
        profile.scripts().insert(script)

        self._otr_interceptor = OnionRequestInterceptor()
        profile.setUrlRequestInterceptor(self._otr_interceptor)
        return profile

    def _open_incognito_tab(self):
        self._open_tab(incognito=True)

    # ── DevTools ───────────────────────────────────────────────────────────

    def _open_devtools_current(self):
        tab = self._current_tab()
        if tab and not tab.IS_DEVTOOLS:
            self._open_devtools_for(tab)

    def _open_devtools_for(self, tab: BrowserTab):
        dt = DevToolsTab(tab._page)
        idx = self._tabs.addTab(dt, "🔧 DevTools")

        close_btn = QPushButton("×")
        close_btn.setFixedSize(18, 18)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; border: none; border-radius: 9px;
                color: {_MUTED}; font-size: 15px; font-weight: bold; padding: 0;
            }}
            QPushButton:hover  {{ background: #60606a; color: {_TEXT}; }}
            QPushButton:pressed {{ background: #75757f; }}
        """)
        close_btn.clicked.connect(lambda: self._close_tab(self._tabs.indexOf(dt)))
        self._tabs.tabBar().setTabButton(idx, QTabBar.ButtonPosition.RightSide, close_btn)

        dt.title_changed.connect(self._on_title_change)
        dt.load_started.connect(lambda t=dt: self._on_load_started(t))
        dt.load_finished.connect(lambda ok, t=dt: self._on_load_finished_tab(t, ok))
        dt.load_progress.connect(lambda v, t=dt: self._on_load_progress_tab(t, v))

        self._tabs.setCurrentIndex(idx)

    def _open_source_tab(self, url: str):
        self._open_tab(url=url)

    # ── Panels ─────────────────────────────────────────────────────────────

    def _toggle_log_panel(self):
        if self._log_panel.isVisible():
            self._log_panel.hide()
        else:
            self._log_panel.show()

    def _toggle_dl_panel(self):
        if self._dl_panel.isVisible():
            self._dl_panel.hide()
        else:
            self._dl_panel.show()
            self._dl_panel.raise_()
