import subprocess
import time
import socket
import requests
from stem import Signal
from stem.control import Controller
from config.settings import (
    TOR_SOCKS_HOST, TOR_SOCKS_PORT,
    TOR_CONTROL_PORT, TOR_CONTROL_PASSWORD
)


class TorClient:
    _NEWNYM_COOLDOWN = 10  # Tor spec: NEWNYM must not be sent more than once per 10s

    def __init__(self):
        self._process = None
        self._connected = False
        self._last_identity_time = 0.0

    def start(self):
        """Start Tor if needed and verify the local SOCKS endpoint is reachable."""
        if self._is_socks_available():
            self._connected = True
            return True
        try:
            self._process = subprocess.Popen(
                ["tor"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            for _ in range(30):
                time.sleep(1)
                if self._is_socks_available():
                    self._connected = True
                    return True
        except FileNotFoundError:
            pass
        self._connected = False
        return False

    def stop(self):
        if self._process:
            self._process.terminate()
            self._process = None
        self._connected = False

    def new_identity(self):
        """Request a new Tor circuit (new IP).

        Returns False immediately if called within 10 seconds of the last request —
        spamming NEWNYM exhausts Tor's circuit pool and degrades anonymity.
        """
        now = time.monotonic()
        if now - self._last_identity_time < self._NEWNYM_COOLDOWN:
            return False
        try:
            with Controller.from_port(port=TOR_CONTROL_PORT) as ctrl:
                if TOR_CONTROL_PASSWORD:
                    ctrl.authenticate(password=TOR_CONTROL_PASSWORD)
                else:
                    ctrl.authenticate()
                ctrl.signal(Signal.NEWNYM)
            self._last_identity_time = time.monotonic()
            return True
        except Exception:
            return False

    def get_proxy_dict(self):
        proxy = f"socks5h://{TOR_SOCKS_HOST}:{TOR_SOCKS_PORT}"
        return {"http": proxy, "https": proxy}

    def get_current_ip(self):
        try:
            r = requests.get(
                "https://api.ipify.org",
                proxies=self.get_proxy_dict(),
                timeout=15
            )
            return r.text.strip()
        except Exception:
            return "Unknown"

    def is_connected(self):
        return self._connected and self._is_socks_available()

    def verify_tor_network(self):
        """Return True only when external Tor Project verification succeeds."""
        try:
            r = requests.get(
                "https://check.torproject.org/api/ip",
                proxies=self.get_proxy_dict(),
                timeout=10
            )
            return r.json().get("IsTor", False)
        except Exception:
            return False

    def _is_socks_available(self):
        try:
            with socket.create_connection((TOR_SOCKS_HOST, TOR_SOCKS_PORT), timeout=3):
                return True
        except OSError:
            return False
