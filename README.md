# Index Browser

> A PyQt6 WebEngine browser with explicit clear-web and Tor routing, automatic `.onion` handling, and a local privacy-aware proxy.

Index Browser is a desktop browser prototype built for controlled browsing across the clearnet and the Tor network. It gives users a normal browser surface while routing every request through an internal proxy that decides, per destination, whether traffic should go directly or through Tor.

The important behavior is automatic: `.onion` destinations are always routed through Tor, even when the UI is in clear mode, and onion HTTPS/WebSocket URLs are normalized to the schemes onion services commonly expose.

## Highlights

- **Two browsing modes**: Clear mode for normal browsing and Tor mode for traffic routed through Tor's SOCKS5 endpoint.
- **Automatic onion routing**: `.onion` URLs and Tor verification URLs are forced through Tor regardless of the selected mode.
- **Local HTTP/CONNECT proxy**: Chromium is configured to use a random local proxy port started by the app at launch.
- **DNS-leak-resistant onion transport**: onion hostnames are sent to Tor via SOCKS5 domain-name requests instead of local DNS.
- **Onion compatibility fixes**: `https://*.onion` and `wss://*.onion` are rewritten to `http://` and `ws://`.
- **Onion subresource hardening**: onion pages can be prevented from loading clearnet or private-network subresources.
- **Tor identity rotation**: the UI can request a new Tor circuit through the Tor control port.
- **Tabbed PyQt6 UI**: address/search bar, mode selector, status indicator, progress display, and diagnostic log panel.
- **Focused test suite**: unit tests cover URL classification, routing, proxy behavior, onion policy, response rewriting, and JavaScript normalizer coverage.

## Screens

Index Browser opens directly into the browser experience:

- Address bar accepts URLs or search queries.
- Mode selector switches between `Clear` and `Tor`.
- `.onion` navigation switches to Tor automatically.
- `Ctrl+T` opens a new tab.
- `Ctrl+W` closes the current tab.
- `Ctrl+Shift+J` toggles the browser log panel.

## Project Layout

```text
.
+-- main.py                    # Application entrypoint and WebEngine profile setup
+-- run.sh                     # Convenience launcher for the local virtualenv
+-- proxy_server.py            # Local HTTP/CONNECT proxy and Tor/direct transport logic
+-- tor_client.py              # Tor process, SOCKS endpoint, control-port, and identity handling
+-- router.py                  # Clear/Tor mode resolution
+-- browser_state.py           # Shared mode state
+-- search.py                  # Search query detection and search URL generation
+-- url_utils.py               # URL normalization and onion detection helpers
+-- config/
|   +-- settings.py            # Environment-backed runtime configuration
|   +-- .env.example           # Example configuration file
+-- ui/
|   +-- main_window.py         # Main browser window, tabs, shortcuts, and status UI
|   +-- browser_tab.py         # QWebEngineView wrapper
|   +-- toolbar.py             # Navigation controls and mode selector
|   +-- request_interceptor.py # Onion URL rewriting and subresource blocking
|   +-- web_page.py            # Web page hooks and normalizer script
|   +-- log_panel.py           # Diagnostics panel
|   +-- home.html              # New-tab page
+-- tests/
    +-- test_core_logic.py     # Core unit tests
```

## Requirements

- Python 3.12 or another compatible Python 3 release
- Tor installed and available as `tor`
- System libraries required by PyQt6 WebEngine / QtWebEngine

On Debian or Ubuntu based systems:

```bash
sudo apt update
sudo apt install tor
```

Depending on your desktop environment, PyQt6 WebEngine may also require common Qt, OpenGL, fontconfig, X11, Wayland, or sandbox-related system packages.

## Setup

Create a virtual environment:

```bash
python3 -m venv .venv
```

Install Python dependencies:

```bash
.venv/bin/pip install -r requirements.txt
```

Optional: create a local environment file from the example:

```bash
cp config/.env.example .env
```

The app loads `.env` automatically through `python-dotenv`.

## Run

Start the browser:

```bash
./run.sh
```

Or run the entrypoint directly:

```bash
.venv/bin/python main.py
```

At startup, Index Browser:

1. Starts the local proxy on a random loopback port.
2. Configures QtWebEngine/Chromium to use that proxy.
3. Creates the shared WebEngine profile and cache paths.
4. Installs the onion URL normalizer script.
5. Starts the PyQt6 window.
6. Connects to an existing Tor SOCKS endpoint or tries to launch `tor`.

## Configuration

Configuration is read from environment variables and `.env`.

| Variable | Default | Description |
| --- | --- | --- |
| `TOR_SOCKS_HOST` | `127.0.0.1` | Host for Tor's SOCKS5 endpoint. |
| `TOR_SOCKS_PORT` | `9050` | Port for Tor's SOCKS5 endpoint. |
| `TOR_CONTROL_PORT` | `9051` | Tor control port used for identity rotation. |
| `TOR_CONTROL_PASSWORD` | empty | Optional Tor control password. |
| `DEFAULT_SEARCH_ENGINE_CLEAR` | Google search URL | Search template used in clear mode. Must contain `{}`. |
| `DEFAULT_SEARCH_ENGINE_TOR` | DuckDuckGo onion search URL | Search template used in Tor mode. Must contain `{}`. |
| `DEFAULT_HOME_URL_CLEAR` | `index://newtab/` | Home URL in clear mode. |
| `DEFAULT_HOME_URL_TOR` | `index://newtab/` | Home URL in Tor mode. |
| `WEB_CACHE_DIR` | `.index_cache/web` | QtWebEngine disk cache directory. |
| `WEB_STORAGE_DIR` | `.index_cache/storage` | QtWebEngine persistent storage directory. |
| `WEB_CACHE_SIZE_MB` | `512` | Maximum HTTP cache size. |
| `BLOCK_CLEARNET_SUBRESOURCES_ON_ONION` | `true` | Blocks clearnet/private-network subresources on onion pages. |
| `LOG_SLOW_ONION_RESOURCES` | `true` | Enables slow onion resource diagnostics. |
| `SLOW_ONION_RESOURCE_MS` | `900` | Slow-resource threshold in milliseconds. |
| `IGNORE_CERTIFICATE_ERRORS` | `false` | Passes Chromium's ignore-certificate-errors flag. |
| `DISABLE_WEB_SECURITY` | `false` | Passes Chromium's disable-web-security flag. |

Security-sensitive flags should stay disabled unless you are debugging a controlled local environment:

```env
IGNORE_CERTIFICATE_ERRORS=false
DISABLE_WEB_SECURITY=false
```

## How Routing Works

Index Browser uses a single local proxy as the WebEngine network boundary.

```text
PyQt6 WebEngine
      |
      v
Local proxy on 127.0.0.1:<random-port>
      |
      +-- Clear mode / clearnet host --> direct TCP connection
      |
      +-- Tor mode or .onion host ----> Tor SOCKS5 endpoint
```

Routing decisions are centralized in `router.py`, `url_utils.py`, and `proxy_server.py`.

Key rules:

- In clear mode, normal clearnet hosts use direct TCP connections.
- In Tor mode, all destinations are sent through Tor.
- `.onion` hosts always use Tor, even if the selected mode is clear.
- `check.torproject.org` is forced through Tor so Tor verification is meaningful.
- Onion hostnames are handed to Tor as SOCKS5 domain names, avoiding local DNS resolution.

## Onion Compatibility

Many onion services do not expose TLS on port 443. Because Tor already encrypts traffic inside the circuit, Index Browser normalizes common onion URL forms:

- `https://example.onion/...` -> `http://example.onion/...`
- `wss://example.onion/...` -> `ws://example.onion/...`

This happens in two layers:

- `ui/request_interceptor.py` redirects WebEngine requests before they are sent.
- `ui/web_page.py` injects a JavaScript normalizer for fetch, Request, URL, anchors, and attributes.

For onion HTTP responses, `proxy_server.py` can also rewrite textual response bodies and remove only CSP directives that break onion HTTP subresources, while preserving the rest of the policy.

## Privacy Model

Index Browser aims to reduce accidental routing mistakes in a local desktop browser prototype. It is not a replacement for Tor Browser.

What it does:

- Forces `.onion` traffic through Tor.
- Avoids local DNS for onion hosts.
- Allows a Tor Browser-like user agent in Tor mode.
- Blocks clearnet and private-network subresources from onion first-party pages when enabled.
- Keeps clear and Tor mode selection explicit in the UI.

What it does not guarantee:

- It does not provide Tor Browser's full fingerprinting defenses.
- It does not isolate all browser state per site or per mode.
- It does not audit third-party WebEngine behavior.
- It does not make unsafe browser flags safe.

For high-risk anonymity use cases, use Tor Browser.

## Tests

Run the unit test suite:

```bash
.venv/bin/python -m unittest discover -s tests
```

The current tests cover:

- Onion URL and host classification
- Search query detection and search URL generation
- Clear/Tor mode resolution
- Forced Tor routing for onion hosts
- Proxy status payload behavior
- Onion subresource blocking policy
- Onion response rewriting and CSP handling
- JavaScript URL normalizer coverage

## Troubleshooting

### `Missing .venv`

Create and populate the virtual environment:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### Tor shows as unavailable

Verify Tor is installed:

```bash
tor --version
```

Then check whether the SOCKS port is reachable:

```bash
nc -vz 127.0.0.1 9050
```

If your Tor SOCKS endpoint uses a different host or port, set `TOR_SOCKS_HOST` and `TOR_SOCKS_PORT` in `.env`.

### New identity fails

Tor identity rotation requires the control port to be enabled and accessible. Check:

- `TOR_CONTROL_PORT`
- `TOR_CONTROL_PASSWORD`
- Your local `torrc` control-port configuration

Tor also rate-limits `NEWNYM`; this project enforces a 10-second cooldown.

### QtWebEngine fails to launch

Install the missing system libraries for your platform. Common causes are missing OpenGL, X11/Wayland, fontconfig, or QtWebEngine runtime dependencies.

### Onion pages miss assets

Some onion sites publish `https://` asset URLs even when they only serve HTTP over Tor. Index Browser rewrites common cases, but heavily scripted pages can still fail if they construct URLs in unusual ways.

## Development Notes

- Keep routing and URL policy changes covered by `tests/test_core_logic.py`.
- Do not pass `.onion` hostnames to local DNS.
- Keep `IGNORE_CERTIFICATE_ERRORS` and `DISABLE_WEB_SECURITY` disabled by default.
- Treat `proxy_server.py` as the network boundary; changes there affect privacy and routing semantics.
- Keep onion compatibility rewrites narrow and testable.

## License

No license file is currently included. Add a license before distributing or accepting external contributions.
