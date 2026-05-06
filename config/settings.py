import os
from dotenv import load_dotenv

load_dotenv()

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}

TOR_SOCKS_HOST = os.getenv("TOR_SOCKS_HOST", "127.0.0.1")
TOR_SOCKS_PORT = int(os.getenv("TOR_SOCKS_PORT", "9050"))
TOR_CONTROL_PORT = int(os.getenv("TOR_CONTROL_PORT", "9051"))
TOR_CONTROL_PASSWORD = os.getenv("TOR_CONTROL_PASSWORD", "")

DEFAULT_SEARCH_ENGINE_CLEAR = os.getenv("DEFAULT_SEARCH_ENGINE_CLEAR", "https://www.google.com/search?q={}")
DEFAULT_SEARCH_ENGINE_TOR = os.getenv("DEFAULT_SEARCH_ENGINE_TOR", "https://duckduckgogg42xjoc72x3sjasowoarfbgcmvfimaftt6twagswzczad.onion/?q={}")
DEFAULT_HOME_URL_CLEAR = os.getenv("DEFAULT_HOME_URL_CLEAR", "index://newtab/")
DEFAULT_HOME_URL_TOR  = os.getenv("DEFAULT_HOME_URL_TOR",   "index://newtab/")

IGNORE_CERTIFICATE_ERRORS = _env_bool("IGNORE_CERTIFICATE_ERRORS", False)
DISABLE_WEB_SECURITY = _env_bool("DISABLE_WEB_SECURITY", False)
WEB_CACHE_DIR = os.getenv("WEB_CACHE_DIR", os.path.join(_PROJECT_ROOT, ".index_cache", "web"))
WEB_STORAGE_DIR = os.getenv("WEB_STORAGE_DIR", os.path.join(_PROJECT_ROOT, ".index_cache", "storage"))
WEB_CACHE_SIZE_MB = int(os.getenv("WEB_CACHE_SIZE_MB", "512"))
BLOCK_CLEARNET_SUBRESOURCES_ON_ONION = _env_bool("BLOCK_CLEARNET_SUBRESOURCES_ON_ONION", True)
LOG_SLOW_ONION_RESOURCES = _env_bool("LOG_SLOW_ONION_RESOURCES", True)
SLOW_ONION_RESOURCE_MS = int(os.getenv("SLOW_ONION_RESOURCE_MS", "900"))

APP_NAME = "Index Browser"
APP_VERSION = "1.0.0"
WINDOW_WIDTH = 1280
WINDOW_HEIGHT = 800
