# Changelog

## 0.2.0-beta (2026-11-10)

### Renamed: the Windows side is now the "winuse host"

It used to be called "the agent", which was easy to read as an AI running on the PC. "Agent" now
only ever means the AI agent that uses winuse.

- `host/winuse_host.py` (was `agent/winuse_agent.py`), installed to `C:\winuse\host`.
- `winuse host status|start|stop|restart|log` (`winuse agent ...` still works).
- Log file `C:\winuse\logs\host.log`; the installer prints `Host token`.
- **Upgrading from 0.1:** re-run the installer. It registers the logon task at the new path and
  removes `C:\winuse\agent`. Until you do, an existing 0.1 task keeps working: the installer and
  `scripts/deploy.sh` leave a small shim at `C:\winuse\agent\winuse_agent.py` that starts the host.

### Added

- **Script mode:** `winuse py` (MCP `run_python`) runs a Python script inside the host on the PC,
  in a persistent per-session namespace, with helpers such as `find`, `click`, `fill`, `wait_for`,
  `shot` and `run`. One round trip instead of one per action; only printed output comes back.
- **Displays and DPI:** per-monitor DPI awareness is verified and reported; `winuse status` lists
  each display with its scale; `winuse shot --display N|all` (multi-monitor default: the display
  with the active window).
- **Non-admin mode:** `WINUSE_NOADMIN=1` / `-NoAdmin` runs the host without administrator rights.
  Input to windows of elevated programs is refused with an explanation instead of silently dropped.
- **Install one-liners** for Windows (`irm ... | iex`, plus a `curl.exe` form for remote and AI-agent
  installs) and macOS/Linux (`curl ... | sh`); options `WINUSE_DIR`, `WINUSE_NOTASK`,
  `WINUSE_SOURCE_ZIP` (offline), `WINUSE_UNINSTALL`. Python is detected or installed.
- `winuse wake` learns the PC's MAC address by itself.
- Windows CI: unit tests, a real install, the one-line bootstrap offline, and the 0.1 upgrade path.

### Fixed

- Remote installs over SSH could hang forever: PowerShell 5.1's web client can stall indefinitely
  on some networks, even with `-TimeoutSec`. Downloads now use `curl.exe` with time limits and
  retries, and the docs give a `curl.exe` one-liner for remote installs.
- Windows PowerShell 5.1 turned any stderr line of a native program into a fatal error inside the
  installer (and in CI checks).
- The logon task no longer assumes `C:\winuse` when installed elsewhere.
- `winuse shot --window X` of a window that isn't in front now shows that window (it is rendered
  directly) instead of whatever covers it; it falls back to the screen copy if the render is black.

## 0.1.0-beta (2026-11-10)

First public beta: accessibility-tree snapshots with element refs and background actions,
screenshots (including covered windows), real mouse and keyboard input with a user-present guard,
window management, launch, run, clipboard, Wake-on-LAN; a CLI, a skill and a stdio MCP adapter.
