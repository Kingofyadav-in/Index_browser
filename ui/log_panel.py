from datetime import datetime
from PyQt6.QtWidgets import (
    QDockWidget, QWidget, QVBoxLayout, QHBoxLayout,
    QTextEdit, QPushButton, QLabel, QCheckBox
)
from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtGui import QTextCursor, QColor, QTextCharFormat, QFont


_LEVEL_NAMES = {0: "DBG", 1: "INF", 2: "WRN", 3: "ERR"}
_LEVEL_COLORS = {
    0: "#888888",   # debug  — grey
    1: "#5bc8f5",   # info   — cyan
    2: "#f5c842",   # warning — amber
    3: "#f55142",   # error  — red
}
_NAV_COLOR   = "#7c6fff"   # navigation events — purple
_LOAD_COLOR  = "#4caf50"   # load success — green
_FAIL_COLOR  = "#f55142"   # load failure — red


class LogPanel(QDockWidget):
    def __init__(self, parent=None):
        super().__init__("Website Logs", parent)
        self.setAllowedAreas(Qt.DockWidgetArea.BottomDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable |
            QDockWidget.DockWidgetFeature.DockWidgetClosable |
            QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        self._build_ui()

    def _build_ui(self):
        container = QWidget()
        self.setWidget(container)
        root = QVBoxLayout(container)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        # ── toolbar ───────────────────────────────────────────────────────
        bar = QHBoxLayout()

        self._lbl_count = QLabel("0 entries")
        self._lbl_count.setStyleSheet("color:#888; font-size:11px;")
        bar.addWidget(self._lbl_count)

        bar.addStretch()

        for level, name in [("DBG", 0), ("INF", 1), ("WRN", 2), ("ERR", 3)]:
            cb = QCheckBox(level)
            cb.setChecked(True)
            cb.setStyleSheet(f"color:{_LEVEL_COLORS[name]}; font-size:11px;")
            cb.stateChanged.connect(self._refilter)
            setattr(self, f"_cb_{level.lower()}", cb)
            bar.addWidget(cb)

        btn_clear = QPushButton("Clear")
        btn_clear.setFixedWidth(52)
        btn_clear.setStyleSheet("""
            QPushButton { background:#1e1e2e; color:#ccc; border:1px solid #444;
                          border-radius:4px; font-size:11px; padding:2px 6px; }
            QPushButton:hover { background:#2e2e4e; }
        """)
        btn_clear.clicked.connect(self._clear)
        bar.addWidget(btn_clear)

        root.addLayout(bar)

        # ── log view ──────────────────────────────────────────────────────
        self._view = QTextEdit()
        self._view.setReadOnly(True)
        self._view.setFont(QFont("Monospace", 10))
        self._view.setStyleSheet("""
            QTextEdit {
                background: #0d0d1a;
                color: #ccc;
                border: 1px solid #333;
                border-radius: 4px;
            }
        """)
        root.addWidget(self._view)

        self._entries = []   # list of (kind, level, html)
        self._count   = 0
        self._MAX     = 3000   # cap total stored entries to keep refilter fast

    # ── public API ────────────────────────────────────────────────────────

    @pyqtSlot(int, str, int, str)
    def add_js_log(self, level: int, message: str, line: int, source: str):
        color  = _LEVEL_COLORS.get(level, "#ccc")
        lname  = _LEVEL_NAMES.get(level, f"L{level}")
        ts     = datetime.now().strftime("%H:%M:%S")
        src    = source.split("/")[-1] if source else "—"
        line_s = f":{line}" if line else ""

        html = (
            f'<span style="color:#555;">[{ts}]</span> '
            f'<span style="color:{color};font-weight:bold;">{lname}</span> '
            f'<span style="color:{color};">{self._esc(message)}</span> '
            f'<span style="color:#555;font-size:9px;"> {self._esc(src)}{line_s}</span>'
        )
        self._entries.append(("js", level, html))
        self._count += 1
        self._maybe_append("js", level, html)
        self._update_count()

    @pyqtSlot(str)
    def add_nav(self, url: str):
        ts  = datetime.now().strftime("%H:%M:%S")
        html = (
            f'<span style="color:#555;">[{ts}]</span> '
            f'<span style="color:{_NAV_COLOR};font-weight:bold;">NAV</span> '
            f'<span style="color:{_NAV_COLOR};">{self._esc(url)}</span>'
        )
        self._entries.append(("nav", -1, html))
        self._count += 1
        self._maybe_append("nav", -1, html)
        self._update_count()

    @pyqtSlot(bool)
    def add_load(self, ok: bool, prefix: str = ""):
        ts    = datetime.now().strftime("%H:%M:%S")
        color = _LOAD_COLOR if ok else _FAIL_COLOR
        text  = "Page loaded" if ok else "Page load FAILED"
        label = f"{self._esc(prefix)} " if prefix else ""
        html  = (
            f'<span style="color:#555;">[{ts}]</span> '
            f'<span style="color:{color};font-weight:bold;">{"OK " if ok else "ERR"}</span> '
            f'<span style="color:{color};">{label}{text}</span>'
        )
        self._entries.append(("load", -1, html))
        self._count += 1
        self._maybe_append("load", -1, html)
        self._update_count()

    # ── internals ─────────────────────────────────────────────────────────

    def _level_visible(self, level: int) -> bool:
        return {
            0: self._cb_dbg.isChecked(),
            1: self._cb_inf.isChecked(),
            2: self._cb_wrn.isChecked(),
            3: self._cb_err.isChecked(),
        }.get(level, True)

    def _maybe_append(self, kind: str, level: int, html: str):
        # Evict oldest quarter when cap is reached so refilter stays fast
        if len(self._entries) >= self._MAX:
            keep = self._MAX * 3 // 4
            self._entries = self._entries[-keep:]
            self._view.clear()
            for ek, el, eh in self._entries:
                if ek == "js" and not self._level_visible(el):
                    continue
                self._append_html(eh)

        if kind == "js" and not self._level_visible(level):
            return
        self._append_html(html)

    def _append_html(self, html: str):
        cursor = self._view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self._view.setTextCursor(cursor)
        self._view.insertHtml(html + "<br>")
        self._view.ensureCursorVisible()

    def _refilter(self):
        self._view.clear()
        for kind, level, html in self._entries:
            if kind == "js" and not self._level_visible(level):
                continue
            self._append_html(html)

    def _clear(self):
        self._entries.clear()
        self._count = 0
        self._view.clear()
        self._update_count()

    def _update_count(self):
        self._lbl_count.setText(f"{self._count} entries")

    @staticmethod
    def _esc(text: str) -> str:
        return (text
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;"))
