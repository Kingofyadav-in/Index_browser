import json
import queue as _queue
import urllib.parse
import urllib.request

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QLineEdit, QPushButton,
    QLabel, QFrame, QSizePolicy, QCompleter, QProgressBar,
)
from PyQt6.QtCore import pyqtSignal, Qt, QTimer, QThread, QStringListModel
from PyQt6.QtGui import QFont

from router import BrowsingMode
from url_utils import is_onion_url
import proxy_server as _proxy

# ── Palette ────────────────────────────────────────────────────────────────
_BG       = "#1e1e21"
_TOOLBAR  = "#28282c"
_INPUT_BG = "#38383d"
_HOVER    = "#46464c"
_BORDER   = "#44444a"
_TEXT     = "#f0f0f5"
_MUTED    = "#9898a0"
_ACCENT   = "#0a84ff"
_TOR_COL  = "#bf5af2"
_TOR_BG   = "#2e1a42"
_TOR_BD   = "#7a38c0"
_GREEN    = "#30d158"
_AMBER    = "#ffd60a"
_RED      = "#ff453a"


# ── Async suggestion fetcher ──────────────────────────────────────────────
class _SuggestionWorker(QThread):
    """
    Fetches DuckDuckGo autocomplete suggestions through our local proxy so
    that Tor mode is automatically respected. A single background thread
    drains a max-1 queue; stale queries are replaced rather than queued.
    """
    ready = pyqtSignal(str, list)   # (query, [suggestion, ...])

    def __init__(self, parent=None):
        super().__init__(parent)
        self._q = _queue.Queue(maxsize=1)
        self.setTerminationEnabled(True)

    def request(self, query: str):
        try:
            self._q.get_nowait()          # drop pending stale query
        except _queue.Empty:
            pass
        self._q.put(query)
        if not self.isRunning():
            self.start()

    def run(self):
        while True:
            try:
                query = self._q.get(timeout=4)
            except _queue.Empty:
                return                    # idle timeout — thread exits

            if not query or len(query) < 2:
                continue

            try:
                port = _proxy.get_port()
                url  = (
                    "https://duckduckgo.com/ac/?type=list&q="
                    + urllib.parse.quote(query)
                )
                handler = urllib.request.ProxyHandler({
                    "http":  f"http://127.0.0.1:{port}",
                    "https": f"http://127.0.0.1:{port}",
                })
                opener = urllib.request.build_opener(handler)
                opener.addheaders = [("User-Agent", "Mozilla/5.0")]
                with opener.open(url, timeout=2) as r:
                    data = json.loads(r.read())
                suggestions = data[1][:7] if len(data) > 1 else []
                self.ready.emit(query, suggestions)
            except Exception:
                pass


# ── URL input — selects-all on focus; emits focus_changed for border update ─
class _UrlInput(QLineEdit):
    focus_changed = pyqtSignal(bool)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        QTimer.singleShot(0, self.selectAll)
        self.focus_changed.emit(True)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.focus_changed.emit(False)


# ── Combined security badge + URL field ───────────────────────────────────
class AddressBar(QFrame):
    navigate_requested = pyqtSignal(str)

    _SEC = {
        "onion": ("🧅", _TOR_COL),
        "https": ("🔒", _GREEN),
        "http":  ("⚠",  _AMBER),
        "home":  ("⌂",  _MUTED),
        "other": ("⌕",  _MUTED),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("addressBar")
        self.setFixedHeight(36)
        self._build()
        self._setup_suggestions()

    def _build(self):
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 12, 0)
        lay.setSpacing(6)

        self._icon = QLabel("⌕")
        self._icon.setFixedWidth(16)
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon.setStyleSheet(f"background:transparent; color:{_MUTED}; font-size:13px;")
        self._icon.setCursor(Qt.CursorShape.ArrowCursor)

        self._edit = _UrlInput()
        self._edit.setPlaceholderText("Search or enter address")
        self._edit.setFont(QFont("Segoe UI", 12))
        self._edit.setStyleSheet(f"""
            QLineEdit {{
                background: transparent;
                border: none;
                color: {_TEXT};
                font-size: 13px;
                selection-background-color: #1d4f8c;
            }}
        """)
        self._edit.returnPressed.connect(self._on_enter)
        self._edit.focus_changed.connect(self._refresh_frame)

        lay.addWidget(self._icon)
        lay.addWidget(self._edit)

        self._refresh_frame(focused=False)

    def _setup_suggestions(self):
        # Completer model (updated dynamically)
        self._sugg_model  = QStringListModel(self)
        self._completer   = QCompleter(self._sugg_model, self)
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._completer.setMaxVisibleItems(8)
        self._completer.setCompletionMode(
            QCompleter.CompletionMode.UnfilteredPopupCompletion)
        self._completer.activated.connect(self.navigate_requested)
        self._edit.setCompleter(self._completer)

        # Style the popup
        self._completer.popup().setStyleSheet(f"""
            QAbstractItemView {{
                background: #232328;
                color: {_TEXT};
                border: 1px solid {_BORDER};
                border-radius: 8px;
                selection-background-color: {_HOVER};
                selection-color: {_TEXT};
                font-size: 13px;
                padding: 4px 0;
                outline: none;
            }}
            QAbstractItemView::item {{
                padding: 7px 14px;
                border-radius: 4px;
            }}
            QAbstractItemView::item:hover {{
                background: {_HOVER};
            }}
            QScrollBar:vertical {{
                background: #232328;
                width: 6px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: #58585e;
                border-radius: 3px;
                min-height: 20px;
            }}
        """)

        # Async worker + debounce timer (200 ms)
        self._worker = _SuggestionWorker(self)
        self._worker.ready.connect(self._on_suggestions)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(200)
        self._debounce.timeout.connect(self._fetch_suggestions)
        self._edit.textEdited.connect(lambda _: self._debounce.start())

    def _refresh_frame(self, focused: bool):
        border = _ACCENT if focused else _BORDER
        bg     = "#232328" if focused else _INPUT_BG
        self.setStyleSheet(f"""
            AddressBar {{
                background: {bg};
                border: 1.5px solid {border};
                border-radius: 18px;
            }}
        """)

    def _on_enter(self):
        text = self._edit.text().strip()
        if text:
            self._completer.popup().hide()
            self.navigate_requested.emit(text)

    def _fetch_suggestions(self):
        text = self._edit.text().strip()
        if len(text) < 2:
            self._sugg_model.setStringList([])
            return
        self._worker.request(text)

    def _on_suggestions(self, query: str, suggestions: list):
        if query != self._edit.text().strip():
            return   # stale response — user has typed something else
        self._sugg_model.setStringList(suggestions)
        if suggestions and self._edit.hasFocus():
            self._completer.complete()

    # ── Public API ─────────────────────────────────────────────────────────

    def set_url(self, url: str):
        self._edit.setText(url)
        self._update_security(url)

    def text(self) -> str:
        return self._edit.text()

    def focus(self):
        self._edit.setFocus()
        self._edit.selectAll()

    def _update_security(self, url: str):
        if is_onion_url(url):
            key = "onion"
        elif url.startswith("https://"):
            key = "https"
        elif url.startswith("http://"):
            key = "http"
        elif not url or url.startswith(("about:", "index://")):
            key = "home"
        else:
            key = "other"
        icon, color = self._SEC[key]
        self._icon.setText(icon)
        self._icon.setStyleSheet(f"background:transparent; color:{color}; font-size:13px;")


# ── Download badge button ─────────────────────────────────────────────────
class _BadgeButton(QWidget):
    """Icon button with an optional count badge in the top-right corner."""
    clicked = pyqtSignal()

    def __init__(self, text: str, tooltip: str = "", parent=None):
        super().__init__(parent)
        self.setFixedSize(36, 30)

        self._btn = QPushButton(text, self)
        self._btn.setGeometry(0, 0, 30, 30)
        self._btn.setToolTip(tooltip)
        self._btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; border: none; border-radius: 15px;
                color: {_MUTED}; font-size: 16px;
                min-width: 30px; max-width: 30px;
                min-height: 30px; max-height: 30px; padding: 0;
            }}
            QPushButton:hover   {{ background: {_HOVER}; color: {_TEXT}; }}
            QPushButton:pressed {{ background: #55555c; }}
        """)
        self._btn.clicked.connect(self.clicked)

        self._badge = QLabel("", self)
        self._badge.setGeometry(19, 0, 16, 16)
        self._badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._badge.setStyleSheet("""
            QLabel {
                background: #0a84ff; color: #fff;
                font-size: 9px; font-weight: 700;
                border-radius: 8px;
                min-width: 16px; min-height: 16px;
            }
        """)
        self._badge.hide()

    def set_count(self, n: int):
        if n > 0:
            self._badge.setText(str(n) if n < 100 else "99+")
            self._badge.show()
        else:
            self._badge.hide()


# ── Main toolbar ───────────────────────────────────────────────────────────
class Toolbar(QWidget):
    navigate_requested    = pyqtSignal(str)
    mode_changed          = pyqtSignal(BrowsingMode)
    new_identity_requested = pyqtSignal()
    back_requested        = pyqtSignal()
    forward_requested     = pyqtSignal()
    reload_requested      = pyqtSignal()
    stop_requested        = pyqtSignal()
    download_toggle       = pyqtSignal()
    incognito_requested   = pyqtSignal()

    _NAV_BTN = f"""
        QPushButton {{
            background: transparent;
            border: none;
            border-radius: 15px;
            color: {_MUTED};
            font-size: 18px;
            min-width: 30px; max-width: 30px;
            min-height: 30px; max-height: 30px;
            padding: 0;
        }}
        QPushButton:hover:enabled  {{ background: {_HOVER}; color: {_TEXT}; }}
        QPushButton:pressed:enabled {{ background: #55555c; }}
        QPushButton:disabled        {{ color: #44444a; }}
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loading = False
        self._mode    = BrowsingMode.CLEAR
        self._build()

    def _build(self):
        self.setFixedHeight(52)
        self.setStyleSheet(f"QWidget {{ background: {_TOOLBAR}; border: none; }}")

        root = QHBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 0)
        root.setSpacing(0)

        # ── Navigation cluster ────────────────────────────────────────────
        self._back_btn    = self._nav_btn("←", "Back  (Alt+Left)")
        self._forward_btn = self._nav_btn("→", "Forward  (Alt+Right)")
        self._reload_btn  = self._nav_btn("↻", "Reload  (Ctrl+R / F5)")
        self._back_btn.clicked.connect(self.back_requested)
        self._forward_btn.clicked.connect(self.forward_requested)
        self._reload_btn.clicked.connect(self._on_reload_click)

        nav_gap = QLabel()
        nav_gap.setFixedWidth(10)
        nav_gap.setStyleSheet("background:transparent;")

        # ── Address bar ───────────────────────────────────────────────────
        self._addr = AddressBar()
        self._addr.navigate_requested.connect(self.navigate_requested)
        self._addr.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        right_gap = QLabel()
        right_gap.setFixedWidth(10)
        right_gap.setStyleSheet("background:transparent;")

        # ── Mode toggle ───────────────────────────────────────────────────
        self._mode_btn = QPushButton("🌐  Clear")
        self._mode_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mode_btn.clicked.connect(self._on_mode_toggle)
        self._apply_mode_style(BrowsingMode.CLEAR)

        # ── New Identity ──────────────────────────────────────────────────
        self._new_id_btn = QPushButton("🔀  New ID")
        self._new_id_btn.setToolTip("Request a new Tor circuit  (10 s cooldown)")
        self._new_id_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._new_id_btn.setStyleSheet(f"""
            QPushButton {{
                background: {_TOR_BG};
                border: 1.5px solid {_TOR_BD};
                border-radius: 12px;
                color: #d1a0ff;
                font-size: 11px;
                font-weight: 600;
                padding: 4px 12px;
            }}
            QPushButton:hover  {{ background: #3c1f5a; color: #e4c0ff; }}
            QPushButton:pressed {{ background: #4a2070; }}
        """)
        self._new_id_btn.clicked.connect(self.new_identity_requested)
        self._new_id_btn.setVisible(False)

        # ── Tor status dot ────────────────────────────────────────────────
        self._dot = QLabel("●")
        self._dot.setFixedSize(22, 22)
        self._dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._dot.setToolTip("Tor: checking…")
        self._dot.setStyleSheet(f"background:transparent; color:{_MUTED}; font-size:11px;")

        # ── Download badge button ─────────────────────────────────────────
        self._dl_btn = _BadgeButton("↓", "Downloads  (Ctrl+J)")
        self._dl_btn.clicked.connect(self.download_toggle)

        # ── Incognito button ──────────────────────────────────────────────
        self._incognito_btn = self._nav_btn("🕵", "New Incognito Tab  (Ctrl+Shift+N)")
        self._incognito_btn.clicked.connect(self.incognito_requested)

        # ── Incognito active indicator ────────────────────────────────────
        self._incognito_badge = QLabel("🕵  Incognito")
        self._incognito_badge.setStyleSheet(f"""
            QLabel {{
                background: #2a1a3e;
                color: #bf5af2;
                font-size: 11px;
                font-weight: 600;
                padding: 3px 10px;
                border-radius: 10px;
                border: 1px solid {_TOR_BD};
            }}
        """)
        self._incognito_badge.hide()

        for w in (self._back_btn, self._forward_btn, self._reload_btn):
            root.addWidget(w)
        root.addWidget(nav_gap)
        root.addWidget(self._addr)
        root.addWidget(right_gap)
        root.addWidget(self._incognito_badge)
        root.addSpacing(4)
        root.addWidget(self._mode_btn)
        root.addSpacing(6)
        root.addWidget(self._new_id_btn)
        root.addSpacing(4)
        root.addWidget(self._dot)
        root.addSpacing(2)
        root.addWidget(self._dl_btn)
        root.addSpacing(2)
        root.addWidget(self._incognito_btn)

        # ── Progress bar (thin, anchored to bottom of toolbar) ───────────
        self._progress = QProgressBar(self)
        self._progress.setRange(0, 100)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(3)
        self._progress.setStyleSheet(f"""
            QProgressBar {{
                background: transparent;
                border: none;
                border-radius: 0;
            }}
            QProgressBar::chunk {{
                background: {_ACCENT};
                border-radius: 0;
            }}
        """)
        self._progress.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._progress.setGeometry(0, self.height() - 3, self.width(), 3)

    # ── Helpers ────────────────────────────────────────────────────────────

    def _nav_btn(self, text: str, tip: str = "") -> QPushButton:
        btn = QPushButton(text)
        btn.setStyleSheet(self._NAV_BTN)
        if tip:
            btn.setToolTip(tip)
        return btn

    def _on_reload_click(self):
        if self._loading:
            self.stop_requested.emit()
        else:
            self.reload_requested.emit()

    def _on_mode_toggle(self):
        new = BrowsingMode.CLEAR if self._mode == BrowsingMode.TOR else BrowsingMode.TOR
        self.mode_changed.emit(new)

    def _apply_mode_style(self, mode: BrowsingMode):
        if mode == BrowsingMode.TOR:
            self._mode_btn.setText("🧅  Tor")
            self._mode_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {_TOR_BG};
                    border: 1.5px solid {_TOR_BD};
                    border-radius: 12px;
                    color: #d1a0ff;
                    font-size: 11px;
                    font-weight: 600;
                    padding: 4px 12px;
                    min-width: 72px;
                }}
                QPushButton:hover  {{ background: #3c1f5a; color: #e4c0ff; }}
                QPushButton:pressed {{ background: #4a2070; }}
            """)
        else:
            self._mode_btn.setText("🌐  Clear")
            self._mode_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {_INPUT_BG};
                    border: 1.5px solid {_BORDER};
                    border-radius: 12px;
                    color: {_MUTED};
                    font-size: 11px;
                    font-weight: 600;
                    padding: 4px 12px;
                    min-width: 72px;
                }}
                QPushButton:hover  {{ background: {_HOVER}; color: {_TEXT}; }}
                QPushButton:pressed {{ background: #55555c; }}
            """)

    # ── Public API ─────────────────────────────────────────────────────────

    def set_url(self, url: str):
        self._addr.set_url(url)

    def focus_address_bar(self):
        self._addr.focus()

    def set_mode_silent(self, mode: BrowsingMode):
        self._mode = mode
        self._apply_mode_style(mode)
        self._new_id_btn.setVisible(mode == BrowsingMode.TOR)

    def set_tor_status(self, connected: bool):
        if connected:
            self._dot.setStyleSheet(
                f"background:transparent; color:{_GREEN}; font-size:11px;")
            self._dot.setToolTip("Tor: connected")
        else:
            self._dot.setStyleSheet(
                f"background:transparent; color:{_RED}; font-size:11px;")
            self._dot.setToolTip("Tor: not connected")

    def set_loading(self, loading: bool):
        self._loading = loading
        if loading:
            self._reload_btn.setText("✕")
            self._reload_btn.setToolTip("Stop loading  (Esc)")
        else:
            self._reload_btn.setText("↻")
            self._reload_btn.setToolTip("Reload  (Ctrl+R / F5)")

    def set_progress(self, value: int):
        if value <= 0 or value >= 100:
            self._progress.hide()
        else:
            self._progress.setValue(value)
            self._progress.show()

    def set_download_badge(self, count: int):
        self._dl_btn.set_count(count)

    def set_incognito_indicator(self, active: bool):
        self._incognito_badge.setVisible(active)
