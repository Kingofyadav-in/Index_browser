"""
Download manager — full transparency: every byte, speed, ETA, state, and
error reason is shown to the user. Nothing is hidden.
"""
import os
import time
from collections import deque

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QScrollArea, QFrame, QDockWidget, QSizePolicy,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWebEngineCore import QWebEngineDownloadRequest

_DOWNLOADS_DIR = os.path.expanduser("~/Downloads")

# ── File-type metadata (icon + accent colour) ─────────────────────────────
_TYPE_MAP = [
    ({"mp4","mkv","avi","mov","webm","flv","wmv","m4v"},             "🎬", "#ff6b6b"),
    ({"mp3","flac","wav","ogg","aac","m4a","opus","wma"},             "🎵", "#ffd93d"),
    ({"jpg","jpeg","png","gif","svg","webp","bmp","tiff","ico"},      "🖼",  "#6bcb77"),
    ({"pdf","doc","docx","xls","xlsx","ppt","pptx","odt","ods"},     "📄", "#4d96ff"),
    ({"zip","tar","gz","bz2","xz","7z","rar","zst","lz4"},           "📦", "#c77dff"),
    ({"py","js","ts","html","css","json","xml","sh","c","cpp","rs"}, "📝", "#f8961e"),
    ({"deb","rpm","dmg","exe","msi","appimage"},                     "⚙",  "#ff9f43"),
]


def _type_info(filename: str):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    for exts, icon, color in _TYPE_MAP:
        if ext in exts:
            return icon, color
    return "📁", "#9898a0"


def _fmt_size(n: int) -> str:
    if n < 0:
        return "?"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _fmt_speed(bps: float) -> str:
    return (_fmt_size(int(bps)) + "/s") if bps > 0 else ""


def _fmt_eta(secs: float) -> str:
    if secs <= 0 or secs > 604800:
        return ""
    if secs < 60:
        return f"{int(secs)}s left"
    if secs < 3600:
        return f"{int(secs // 60)}m {int(secs % 60)}s left"
    return f"{int(secs // 3600)}h {int((secs % 3600) // 60)}m left"


# ── Single download row ───────────────────────────────────────────────────

class DownloadItem(QFrame):
    removed = pyqtSignal(object)

    def __init__(self, dl: QWebEngineDownloadRequest, parent=None):
        super().__init__(parent)
        self._dl      = dl
        self._samples = deque(maxlen=10)   # (monotonic_time, bytes_received)
        self._done    = False

        filename = dl.downloadFileName()
        self._icon_char, self._color = _type_info(filename)
        self._build(filename)

        dl.downloadProgress.connect(self._on_progress)
        dl.stateChanged.connect(self._on_state)

        self._timer = QTimer(self)
        self._timer.setInterval(400)
        self._timer.timeout.connect(self._tick_speed)
        self._timer.start()

    # ── UI ─────────────────────────────────────────────────────────────────

    def _build(self, filename: str):
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(f"""
            DownloadItem {{
                background: #28282c;
                border: 1px solid #3a3a3e;
                border-left: 3px solid {self._color};
                border-radius: 8px;
                margin: 2px 6px;
            }}
        """)
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 9, 10, 9)
        root.setSpacing(5)

        # Row 1 — icon · name · action buttons
        r1 = QHBoxLayout()
        r1.setSpacing(8)

        lbl_icon = QLabel(self._icon_char)
        lbl_icon.setFixedWidth(22)
        lbl_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_icon.setStyleSheet("background: transparent; font-size: 15px;")

        self._lbl_name = QLabel(filename)
        self._lbl_name.setStyleSheet(
            "color: #f0f0f5; font-size: 13px; font-weight: 500; background: transparent;")
        self._lbl_name.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._lbl_name.setToolTip(
            os.path.join(self._dl.downloadDirectory(), filename))

        self._b_pause  = self._mk_btn("⏸", "Pause",         self._toggle_pause)
        self._b_cancel = self._mk_btn("✕", "Cancel",         self._cancel)
        self._b_open   = self._mk_btn("↗", "Open file",      self._open_file)
        self._b_folder = self._mk_btn("📂","Show in folder", self._open_folder)
        self._b_del    = self._mk_btn("🗑", "Remove",         self._remove)
        self._b_open.hide()
        self._b_folder.hide()
        self._b_del.hide()

        r1.addWidget(lbl_icon)
        r1.addWidget(self._lbl_name)
        r1.addStretch()
        for b in (self._b_pause, self._b_cancel,
                  self._b_open, self._b_folder, self._b_del):
            r1.addWidget(b)

        # Row 2 — progress bar
        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setFixedHeight(5)
        self._bar.setTextVisible(False)
        self._set_bar_color(self._color)

        # Row 3 — size · speed · state
        r3 = QHBoxLayout()
        r3.setSpacing(0)

        self._lbl_size  = QLabel("Waiting…")
        self._lbl_size.setStyleSheet(
            "color: #9898a0; font-size: 11px; background: transparent;")

        self._lbl_speed = QLabel("")
        self._lbl_speed.setStyleSheet(
            "color: #9898a0; font-size: 11px; background: transparent;")
        self._lbl_speed.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._lbl_speed.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        self._lbl_state = QLabel("")
        self._lbl_state.setStyleSheet(
            "color: #9898a0; font-size: 11px; font-weight: 600; background: transparent;")

        r3.addWidget(self._lbl_size)
        r3.addWidget(self._lbl_speed)
        r3.addSpacing(12)
        r3.addWidget(self._lbl_state)

        root.addLayout(r1)
        root.addWidget(self._bar)
        root.addLayout(r3)

    def _mk_btn(self, text: str, tip: str, slot) -> QPushButton:
        b = QPushButton(text)
        b.setFixedSize(26, 26)
        b.setToolTip(tip)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setStyleSheet("""
            QPushButton {
                background: transparent; border: none; border-radius: 5px;
                color: #9898a0; font-size: 13px; padding: 0;
            }
            QPushButton:hover  { background: #46464c; color: #f0f0f5; }
            QPushButton:pressed { background: #55555c; }
        """)
        b.clicked.connect(slot)
        return b

    def _set_bar_color(self, color: str):
        self._bar.setStyleSheet(f"""
            QProgressBar {{
                background: #3a3a3e; border: none; border-radius: 2px;
            }}
            QProgressBar::chunk {{
                background: {color}; border-radius: 2px;
            }}
        """)

    # ── Slots ──────────────────────────────────────────────────────────────

    def _on_progress(self, received: int, total: int):
        self._samples.append((time.monotonic(), received))
        tb = total if total > 0 else self._dl.totalBytes()
        if tb > 0:
            self._bar.setRange(0, 100)
            self._bar.setValue(int(received * 100 / tb))
            self._lbl_size.setText(f"{_fmt_size(received)}  /  {_fmt_size(tb)}")
        else:
            self._bar.setRange(0, 0)   # indeterminate spinner
            self._lbl_size.setText(_fmt_size(received))

    def _tick_speed(self):
        if self._done:
            self._timer.stop()
            return
        if len(self._samples) < 2:
            return
        ot, ob = self._samples[0]
        nt, nb = self._samples[-1]
        dt = nt - ot
        if dt <= 0:
            return
        spd   = (nb - ob) / dt
        total = self._dl.totalBytes()
        recv  = self._dl.receivedBytes()
        parts = []
        sp = _fmt_speed(spd)
        if sp:
            parts.append(sp)
        if total > 0 and spd > 0:
            eta = _fmt_eta((total - recv) / spd)
            if eta:
                parts.append(eta)
        self._lbl_speed.setText("  ".join(parts))

    def _on_state(self, state):
        S = QWebEngineDownloadRequest.DownloadState
        if state == S.DownloadCompletedState:
            self._finish("Complete", "#30d158", open_ok=True)
        elif state == S.DownloadCancelledState:
            self._finish("Cancelled", "#9898a0", open_ok=False)
        elif state == S.DownloadInterruptedState:
            reason = ""
            try:
                reason = self._dl.interruptReasonString()
            except Exception:
                pass
            self._finish(
                f"Failed{': ' + reason if reason else ''}", "#ff453a", open_ok=False)

    def _finish(self, label: str, color: str, open_ok: bool):
        self._done = True
        self._set_bar_color(color if open_ok else "#3a3a3e")
        self._bar.setRange(0, 100)
        if open_ok:
            self._bar.setValue(100)
        self._lbl_state.setText(label)
        self._lbl_state.setStyleSheet(
            f"color: {color}; font-size: 11px; font-weight: 600; background: transparent;")
        self._lbl_speed.setText("")
        self._b_pause.hide()
        self._b_cancel.hide()
        if open_ok:
            self._b_open.show()
            self._b_folder.show()
        self._b_del.show()

    def _toggle_pause(self):
        if self._dl.isPaused():
            self._dl.resume()
            self._b_pause.setText("⏸")
            self._b_pause.setToolTip("Pause")
            self._lbl_state.setText("")
        else:
            self._dl.pause()
            self._b_pause.setText("▶")
            self._b_pause.setToolTip("Resume")
            self._lbl_state.setText("Paused")
            self._lbl_speed.setText("")

    def _cancel(self):
        self._dl.cancel()

    def _open_file(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(
            os.path.join(self._dl.downloadDirectory(), self._dl.downloadFileName())))

    def _open_folder(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(self._dl.downloadDirectory()))

    def _remove(self):
        self.removed.emit(self)


# ── Download panel (dock widget) ──────────────────────────────────────────

class DownloadPanel(QDockWidget):
    active_count_changed = pyqtSignal(int)   # → toolbar badge

    def __init__(self, parent=None):
        super().__init__("Downloads", parent)
        self._items: list[DownloadItem] = []
        self._build()
        os.makedirs(_DOWNLOADS_DIR, exist_ok=True)

    def _build(self):
        self.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetClosable   |
            QDockWidget.DockWidgetFeature.DockWidgetMovable    |
            QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        self.setMinimumHeight(150)

        wrap = QWidget()
        self.setWidget(wrap)
        root = QVBoxLayout(wrap)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header bar
        hdr = QWidget()
        hdr.setFixedHeight(38)
        hdr.setStyleSheet("background: #28282c; border-bottom: 1px solid #3a3a3e;")
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(12, 0, 8, 0)
        hl.setSpacing(6)

        ttl = QLabel("Downloads")
        ttl.setStyleSheet(
            "color: #f0f0f5; font-size: 12px; font-weight: 600; "
            "letter-spacing: 0.04em; background: transparent;")

        self._lbl_dir = QLabel(f"→ {_DOWNLOADS_DIR}")
        self._lbl_dir.setStyleSheet(
            "color: #9898a0; font-size: 11px; background: transparent;")

        hl.addWidget(ttl)
        hl.addSpacing(8)
        hl.addWidget(self._lbl_dir)
        hl.addStretch()
        hl.addWidget(self._hdr_btn("📂  Open folder",
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(_DOWNLOADS_DIR))))
        hl.addWidget(self._hdr_btn("Clear completed", self._clear_done))

        # Scroll area
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setStyleSheet("QScrollArea { background: #1e1e21; border: none; }")

        lw = QWidget()
        lw.setStyleSheet("background: #1e1e21;")
        self._ll = QVBoxLayout(lw)
        self._ll.setContentsMargins(0, 6, 0, 6)
        self._ll.setSpacing(4)
        self._ll.addStretch()
        self._scroll.setWidget(lw)

        # Empty state
        self._empty = QLabel(
            "No downloads yet  —  files you download will appear here")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setStyleSheet(
            "color: #9898a0; font-size: 13px; padding: 24px; background: #1e1e21;")

        root.addWidget(hdr)
        root.addWidget(self._empty)
        root.addWidget(self._scroll)
        self._sync_empty()

    def _hdr_btn(self, text: str, slot) -> QPushButton:
        b = QPushButton(text)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setStyleSheet("""
            QPushButton {
                background: transparent; border: none; color: #9898a0;
                font-size: 11px; padding: 4px 8px; border-radius: 4px;
            }
            QPushButton:hover { background: #3a3a3e; color: #f0f0f5; }
        """)
        b.clicked.connect(slot)
        return b

    # ── Public API ─────────────────────────────────────────────────────────

    def add_download(self, dl: QWebEngineDownloadRequest):
        os.makedirs(_DOWNLOADS_DIR, exist_ok=True)
        dl.setDownloadDirectory(_DOWNLOADS_DIR)
        dl.accept()   # MUST be called synchronously

        item = DownloadItem(dl)
        item.removed.connect(self._del_item)
        self._ll.insertWidget(self._ll.count() - 1, item)
        self._items.append(item)
        self._sync_empty()

        dl.stateChanged.connect(lambda _: self._emit_count())
        self._emit_count()

        self.show()
        self.raise_()
        QTimer.singleShot(60, lambda: self._scroll.ensureWidgetVisible(item))

    # ── Internals ──────────────────────────────────────────────────────────

    def _del_item(self, item: DownloadItem):
        if item in self._items:
            self._items.remove(item)
        self._ll.removeWidget(item)
        item.deleteLater()
        self._sync_empty()
        self._emit_count()

    def _clear_done(self):
        S = QWebEngineDownloadRequest.DownloadState
        done = {S.DownloadCompletedState,
                S.DownloadCancelledState,
                S.DownloadInterruptedState}
        for item in list(self._items):
            if item._dl.state() in done:
                self._del_item(item)

    def _sync_empty(self):
        has = bool(self._items)
        self._empty.setVisible(not has)
        self._scroll.setVisible(has)

    def _emit_count(self):
        S = QWebEngineDownloadRequest.DownloadState
        n = sum(1 for i in self._items
                if i._dl.state() == S.DownloadInProgressState)
        self.active_count_changed.emit(n)
