from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineScript
from PyQt6.QtCore import pyqtSignal

from config.settings import LOG_SLOW_ONION_RESOURCES, SLOW_ONION_RESOURCE_MS
from url_utils import forces_tor

# Noise from servers we can't control — suppress permanently
_SUPPRESS = [
    "Permissions-Policy header: Unrecognized feature:",
    "Error with Permissions-Policy",
    "was preloaded using link preload but not used",
    "Request Autofill.enable failed",
    "Request Autofill.setAddresses failed",
    "wasn't found",
]


def should_suppress_console_message(message: str) -> bool:
    if any(pattern in message for pattern in _SUPPRESS):
        return True
    return (
        "[Report Only]" in message and
        "Content Security Policy directive" in message
    )


class WebPage(QWebEnginePage):
    """Custom page that filters console noise and re-emits clean log entries."""
    log_entry = pyqtSignal(int, str, int, str)   # level, message, line, source
    onion_navigation_detected = pyqtSignal(str)  # url — fired before main-frame .onion load
    special_action = pyqtSignal(str, str)        # action, full index:// url string

    def __init__(self, profile=None, parent=None):
        if profile is not None:
            super().__init__(profile, parent)
        else:
            super().__init__(parent)
        if LOG_SLOW_ONION_RESOURCES:
            self._install_resource_timing_script()

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        if url.scheme() == "index":
            host = url.host()
            if host in ("retry", "try-tor", "archive", "search"):
                self.special_action.emit(host, url.toString())
            return False  # always block index:// navigations in the engine
        if is_main_frame and forces_tor(url.toString()):
            self.onion_navigation_detected.emit(url.toString())
        return True

    def javaScriptConsoleMessage(self, level, message, line_number, source_id):
        if should_suppress_console_message(message):
            return   # swallowed — never reaches terminal or log panel
        # Re-emit for the log panel; don't call super() to keep terminal clean
        self.log_entry.emit(
            level.value if hasattr(level, "value") else int(level),
            message,
            line_number,
            source_id or "",
        )

    def _install_resource_timing_script(self):
        script = QWebEngineScript()
        script.setName("IndexSlowOnionResourceLogger")
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setRunsOnSubFrames(False)
        script.setSourceCode(_resource_timing_script(SLOW_ONION_RESOURCE_MS))
        self.scripts().insert(script)


def _onion_url_normalizer_script() -> str:
    return """
(function() {
  const NativeURL = window.URL;

  function isOnionHost(host) {
    return !!host && host.toLowerCase().endsWith('.onion');
  }

  function isSameOnionUrl(url) {
    return isOnionHost(location.hostname) &&
      isOnionHost(url.hostname) &&
      url.hostname.toLowerCase() === location.hostname.toLowerCase();
  }

  function normalizeUrlObject(url) {
    if (isSameOnionUrl(url) && url.protocol === 'https:') {
      url.protocol = 'http:';
    }
    return url;
  }

  function normalize(value, base) {
    if (!isOnionHost(location.hostname)) return value;
    if (value instanceof NativeURL) {
      return normalizeUrlObject(new NativeURL(value.href)).href;
    }
    if (typeof value !== 'string') return value;
    try {
      const url = base === undefined ? new NativeURL(value, location.href) : new NativeURL(value, base);
      const next = normalizeUrlObject(url).href;
      return next;
    } catch (_) {
      return value;
    }
  }

  if (NativeURL) {
    const WrappedURL = function(url, base) {
      const parsed = base === undefined ? new NativeURL(url) : new NativeURL(url, base);
      return normalizeUrlObject(parsed);
    };
    WrappedURL.prototype = NativeURL.prototype;
    Object.setPrototypeOf(WrappedURL, NativeURL);
    if (NativeURL.createObjectURL) {
      WrappedURL.createObjectURL = NativeURL.createObjectURL.bind(NativeURL);
    }
    if (NativeURL.revokeObjectURL) {
      WrappedURL.revokeObjectURL = NativeURL.revokeObjectURL.bind(NativeURL);
    }
    window.URL = WrappedURL;
  }

  const NativeRequest = window.Request;
  if (NativeRequest) {
    window.Request = function(input, init) {
      if (typeof input === 'string') {
        input = normalize(input);
      } else if (input instanceof URL) {
        input = normalize(input.href);
      } else if (input && typeof input.url === 'string') {
        const nextUrl = normalize(input.url);
        if (nextUrl !== input.url) {
          input = nextUrl;
        }
      }
      return new NativeRequest(input, init);
    };
    window.Request.prototype = NativeRequest.prototype;
    Object.setPrototypeOf(window.Request, NativeRequest);
  }

  const nativeFetch = window.fetch;
  if (nativeFetch) {
    window.fetch = function(input, init) {
      if (typeof input === 'string') {
        input = normalize(input);
      } else if (input instanceof URL) {
        input = normalize(input.href);
      } else if (input && typeof input.url === 'string') {
        const nextUrl = normalize(input.url);
        if (nextUrl !== input.url) {
          input = new Request(nextUrl, input);
        }
      }
      return nativeFetch.call(this, input, init);
    };
  }

  const nativeOpen = XMLHttpRequest && XMLHttpRequest.prototype.open;
  if (nativeOpen) {
    XMLHttpRequest.prototype.open = function(method, url) {
      arguments[1] = normalize(url);
      return nativeOpen.apply(this, arguments);
    };
  }

  const nativeSetAttribute = Element && Element.prototype.setAttribute;
  if (nativeSetAttribute) {
    Element.prototype.setAttribute = function(name, value) {
      const attr = String(name || '').toLowerCase();
      if (attr === 'src' || attr === 'href') {
        value = normalize(value);
      }
      return nativeSetAttribute.call(this, name, value);
    };
  }

  function patchUrlProperty(proto, prop) {
    const desc = Object.getOwnPropertyDescriptor(proto, prop);
    if (!desc || !desc.set || !desc.get) return;
    Object.defineProperty(proto, prop, {
      configurable: true,
      enumerable: desc.enumerable,
      get: desc.get,
      set: function(value) {
        return desc.set.call(this, normalize(value));
      }
    });
  }

  try {
    patchUrlProperty(HTMLScriptElement.prototype, 'src');
    patchUrlProperty(HTMLImageElement.prototype, 'src');
    patchUrlProperty(HTMLLinkElement.prototype, 'href');
    patchUrlProperty(HTMLAnchorElement.prototype, 'href');
    patchUrlProperty(HTMLSourceElement.prototype, 'src');
  } catch (_) {}
})();
"""


def _resource_timing_script(threshold_ms: int) -> str:
    return f"""
(function() {{
  const THRESHOLD = {int(threshold_ms)};
  const TYPES = new Set(['script', 'css', 'link', 'fetch', 'xmlhttprequest']);

  function isOnionPage() {{
    return location.hostname && location.hostname.toLowerCase().endsWith('.onion');
  }}

  function resourceKind(entry) {{
    const type = (entry.initiatorType || 'resource').toLowerCase();
    if (type === 'link' && !/\\.css($|[?#])/i.test(entry.name || '')) return null;
    return TYPES.has(type) ? type : null;
  }}

  function report(entry) {{
    if (!isOnionPage()) return;
    if (!entry || entry.duration < THRESHOLD) return;
    const kind = resourceKind(entry);
    if (!kind) return;
    let target = entry.name || '';
    try {{
      const url = new URL(target);
      target = url.hostname + url.pathname;
    }} catch (_) {{}}
    console.info('[IndexNet] slow ' + kind + ' ' + Math.round(entry.duration) + 'ms ' + target);
  }}

  try {{
    performance.getEntriesByType('resource').forEach(report);
    new PerformanceObserver(function(list) {{
      list.getEntries().forEach(report);
    }}).observe({{ type: 'resource', buffered: true }});
  }} catch (_) {{}}
}})();
"""
