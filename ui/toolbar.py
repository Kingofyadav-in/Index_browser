from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QLineEdit, QPushButton, QLabel, QComboBox
)
from PyQt6.QtCore import pyqtSignal, Qt
from router import BrowsingMode


class Toolbar(QWidget):
    navigate_requested = pyqtSignal(str)
    mode_changed = pyqtSignal(BrowsingMode)
    new_identity_requested = pyqtSignal()
    back_requested = pyqtSignal()
    forward_requested = pyqtSignal()
    reload_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        self._back_btn = QPushButton("←")
        self._back_btn.setFixedWidth(32)
        self._back_btn.clicked.connect(self.back_requested)

        self._forward_btn = QPushButton("→")
        self._forward_btn.setFixedWidth(32)
        self._forward_btn.clicked.connect(self.forward_requested)

        self._reload_btn = QPushButton("↺")
        self._reload_btn.setFixedWidth(32)
        self._reload_btn.clicked.connect(self.reload_requested)

        self._url_bar = QLineEdit()
        self._url_bar.setPlaceholderText("Enter URL or search…")
        self._url_bar.returnPressed.connect(self._on_navigate)

        self._go_btn = QPushButton("Go")
        self._go_btn.setFixedWidth(40)
        self._go_btn.clicked.connect(self._on_navigate)

        self._mode_selector = QComboBox()
        self._mode_selector.addItem("🌐 Clear", BrowsingMode.CLEAR)
        self._mode_selector.addItem("🧅 Tor", BrowsingMode.TOR)
        self._mode_selector.currentIndexChanged.connect(self._on_mode_change)

        self._new_id_btn = QPushButton("New ID")
        self._new_id_btn.setFixedWidth(60)
        self._new_id_btn.setToolTip("Request new Tor identity")
        self._new_id_btn.clicked.connect(self.new_identity_requested)
        self._new_id_btn.setVisible(False)

        self._status_label = QLabel("●")
        self._status_label.setFixedWidth(16)
        self._set_status_color("gray")

        layout.addWidget(self._back_btn)
        layout.addWidget(self._forward_btn)
        layout.addWidget(self._reload_btn)
        layout.addWidget(self._url_bar)
        layout.addWidget(self._go_btn)
        layout.addWidget(self._mode_selector)
        layout.addWidget(self._new_id_btn)
        layout.addWidget(self._status_label)

    def _on_navigate(self):
        text = self._url_bar.text().strip()
        if text:
            self.navigate_requested.emit(text)

    def _on_mode_change(self, index):
        mode = self._mode_selector.itemData(index)
        self._new_id_btn.setVisible(mode == BrowsingMode.TOR)
        self.mode_changed.emit(mode)

    def set_mode_silent(self, mode: BrowsingMode):
        """Update the mode combobox without emitting mode_changed."""
        self.blockSignals(True)
        self._mode_selector.setCurrentIndex(0 if mode == BrowsingMode.CLEAR else 1)
        self.blockSignals(False)
        self._new_id_btn.setVisible(mode == BrowsingMode.TOR)

    def set_url(self, url: str):
        self._url_bar.setText(url)

    def set_tor_status(self, connected: bool):
        self._set_status_color("green" if connected else "red")

    def _set_status_color(self, color: str):
        self._status_label.setStyleSheet(f"color: {color};")
