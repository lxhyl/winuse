# winuse

[![CI](https://github.com/lxhyl/winuse/actions/workflows/ci.yml/badge.svg)](https://github.com/lxhyl/winuse/actions/workflows/ci.yml) ![beta](https://img.shields.io/badge/status-beta%20内测版-d6451f) ![version](https://img.shields.io/badge/version-0.1.0--beta-2f6f6a) ![license](https://img.shields.io/badge/license-MIT-444)

> **Beta / 内测版.** Verified on a single Windows 11 machine so far. It works and is usable day-to-day,
> but expect rough edges — see [Limits](#limits) for exactly what is and isn't tested. No PyPI package
> yet; install from this repo.

**Let an AI agent see and operate a Windows desktop — the way iPhone-use and android-use do for phones.**

winuse puts a small agent on a Windows PC and gives any AI agent (Claude Code, Codex, Cursor, Gemini
CLI, …) a clean way to drive it: read the screen as a text **accessibility tree** or as screenshots,
then click, type, scroll, manage windows, launch apps and run commands. It talks over an SSH tunnel on
localhost, so you can control a Windows machine from a Mac, Linux box, or another Windows PC.

It is not a model and not an agent loop — it is the hands and eyes. Your agent does the thinking.

## Install

**On the Windows PC** you want to control — PowerShell:

```powershell
irm https://winuse.ozlab.xyz/install.ps1 | iex
```

It finds or installs Python, sets up winuse's own venv, generates a token, registers a logon task and
starts the agent. It prints the token at the end.

**Installing remotely** (an agent over SSH), or on a network where PowerShell's web client stalls —
`irm` can hang forever there, even with `-TimeoutSec` — use `curl.exe`, which ships with Windows 10/11
and gives every fetch a time limit and retries:

```bat
powershell -NoProfile -Command "iex ((curl.exe -fsSL --retry 5 --retry-all-errors --connect-timeout 10 --max-time 60 https://winuse.ozlab.xyz/install.ps1) -join [char]10)"
```

Options are environment variables set first: `WINUSE_DIR` (default `C:\winuse`), `WINUSE_NOADMIN=1`
(run the agent without administrator rights, see [Limits](#limits)), `WINUSE_NOTASK=1` (files only, for
testing), `WINUSE_SOURCE_ZIP` (install from a local zip, offline), `WINUSE_UNINSTALL=1`.

**On the machine you control from** — macOS / Linux:

```bash
curl -fsSL https://winuse.ozlab.xyz/install.sh | sh          # add -s -- --skill for the Claude skill
```

It installs the `winuse` CLI into `~/.local` and asks for the PC's SSH host name and that token. Then:

```bash
winuse status
```

Both installers are idempotent; uninstall with `… | iex` after `$env:WINUSE_UNINSTALL=1`, or
`install.sh … --uninstall`. You need an SSH server on Windows (OpenSSH, built in) with key login.
The [manual steps](#quickstart-from-a-clone) below do the same thing from a git clone.


## Why

Agents are good at reasoning and bad at reaching native desktop apps. Browser tools cover the web;
shell covers scripts; but GUI-only Windows software, settings panels and installers have no API.
winuse fills that gap, and does it **in the background where it can** — acting through the Windows
accessibility layer so it does **not** move your mouse or steal focus while you work.

## How it works

```
  your machine (Mac/Linux/Win)                Windows PC
  ┌───────────────────────────┐   SSH    ┌──────────────────────────────┐
  │ AI agent                  │  tunnel  │ winuse agent (logon task)    │
  │   ├─ winuse  (CLI)        │ ───────► │   listens on 127.0.0.1 only  │
  │   └─ winuse-mcp (MCP)     │          │   ├─ screenshot (GDI/PrintWindow)
  │                           │          │   ├─ accessibility tree (UIA)
  │ token auth, localhost     │          │   ├─ input (SendInput)
  └───────────────────────────┘          │   └─ windows / launch / run  │
                                         └──────────────────────────────┘
```

- The **agent** runs in the signed-in user's desktop session (a logon Scheduled Task), so it can see
  and touch the real desktop. It binds to `127.0.0.1` and checks a shared token.
- Your machine reaches it through an **SSH tunnel** the CLI opens on demand — no new firewall ports.
- **Background-first:** actions on an element reference (from the accessibility tree) go through UI
  Automation — no pointer movement, no focus change, and they work even when the window is covered.
  When a control has no such interface, winuse briefly borrows the real mouse/keyboard and restores
  the pointer and focus afterwards. Real input is refused while you are actively using the PC.

## Quickstart (from a clone)

Prefer the [one-line installers](#install) above. These manual steps do the same thing.

### 1. On the Windows PC (one-time)

Clone the repo on the PC and run the installer:

```powershell
git clone https://github.com/lxhyl/winuse.git
cd winuse
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

It creates `C:\winuse`, a Python virtual environment, installs dependencies, generates an auth token,
and registers a `winuse` logon task that starts the agent in your desktop session. You also need an
SSH server on Windows (OpenSSH, built in) with key login.

### 2. On your machine

```bash
git clone https://github.com/lxhyl/winuse.git && cd winuse
bin/winuse config set host my-pc        # an entry in your ~/.ssh/config that reaches the Windows PC
cp <token from the PC> .token           # the installer prints where to find it
bin/winuse status
```

### 3. Connect your agent

- **CLI (recommended):** add `bin/winuse` to PATH. Any shell-capable agent can call it. Point your
  agent at [`skill/SKILL.md`](skill/SKILL.md) for usage.
- **Claude Code:** copy `skill/` to `~/.claude/skills/winuse/` so sessions load it on demand.
- **MCP (Codex, Cursor, Gemini CLI, or continuous-look tasks):**
  ```bash
  claude mcp add winuse -- /abs/path/to/bin/winuse-mcp     # or the client's MCP config
  ```

## The loop

```
winuse status                         # unlocked? what's in front? is the user idle?
winuse snap --window chrome           # the window's controls as text, each with a ref like @e12
winuse click @e12                     # act on a ref in the background (no mouse, no focus change)
winuse set @e5 "hello"                # type into a field in the background
winuse snap --window chrome           # check (refs expire at the next snapshot)
```

When the steps are known in advance, send them as one Python script that runs inside the agent on
the PC (`winuse py`, MCP `run_python`): one round trip instead of one per action, and only what the
script prints comes back. Variables persist between calls.

```
winuse py -c 'b = find1("Save", type="Button", window="Notepad"); print(b.click())'
```

Pixel fallback when the tree is not enough (games, canvases):

```
winuse shot                           # saves a PNG; read it. --window X --bg captures a covered window
winuse click 640,360                  # real click on a point of the last screenshot
winuse type "text" --enter            # Unicode input — any language, bypasses the IME
winuse key ctrl+s
```

Full command list: `winuse --help`. Tool reference for agents: [`skill/SKILL.md`](skill/SKILL.md).

## Security model

- **Localhost only.** The agent binds `127.0.0.1`; nothing listens on the LAN. Remote access goes
  through your existing SSH, authenticated by your SSH key.
- **Token.** Every RPC carries a bearer token, stored readable only by Administrators and SYSTEM.
- **Full desktop control = full trust.** The agent runs elevated in your session and can do anything
  you can, including `run` (PowerShell). Treat access to it like access to the machine. The SSH key
  that reaches the PC is the real boundary — protect it.
- **No telemetry.** Nothing is sent anywhere. Typed text is never logged (only its length).
- An action log (`C:\winuse\logs`) records what was done, for your own audit.

See [`SECURITY.md`](SECURITY.md).

## Limits

This is beta. Verified so far on **one Windows 11 machine, single monitor at 100% scale**, with the
agent run as administrator. The one-line installer is verified there end-to-end, including run
remotely over SSH.

- **Lock screen / sign-in screen:** winuse **cannot work while the session is locked** — the desktop
  isn't interactive, so it reports `locked` and refuses input (this guard is implemented, but behaviour
  on the lock screen itself is untested). Prefer **sleep over shutdown**: a sleeping PC keeps its
  session, and `winuse wake` (Wake-on-LAN) brings it back with no sign-in screen.
- **Classic (Win32) drop-down lists** have no background route; winuse falls back to the **real
  cursor** (it briefly moves the pointer, then restores it).
- **Multi-monitor and display scaling** (designed for, not yet tested on real hardware): the agent is
  per-monitor DPI aware (v2), so screenshots, clicks and element rectangles are all in physical pixels
  on every monitor, whatever its scaling. `winuse status` lists each display with its scale. On a
  multi-monitor PC a screenshot shows the display with the active window by default; use
  `--display 2` or `--display all`. Mixed layouts (a monitor left of the primary, so negative
  coordinates) are covered by unit tests.
- **Non-admin mode** (`WINUSE_NOADMIN=1`, not yet tested on real hardware): the agent runs without
  administrator rights. Ordinary apps work as usual. What stops working: windows of programs that run
  as administrator (Task Manager, regedit, elevated consoles and installers) — Windows blocks input
  from lower-privilege programs (UIPI), so winuse refuses with a clear message instead of failing
  silently, and `shot --bg` of those windows may fail; `winuse run` commands run without admin
  rights. The agent token is then also readable by your user account.
- **No PyPI package yet** — install from this repo.
- Windows 10 / 11. Python 3.10+ on the PC.

## 中文说明

winuse 让 AI agent（Claude Code、Codex、Cursor 等）能看到并操作一台 Windows 电脑：把屏幕读成**无障碍树**
文本或截图，然后点击、输入、滚动、管理窗口、启动程序、执行命令。它在 Windows 上放一个常驻小服务，只监听
本机回环端口，通过 SSH 隧道从你的 Mac / Linux / 另一台 Windows 控制，不开放任何局域网端口。

**后台优先**：对无障碍树里的元素操作时走 UI Automation——不移动鼠标、不抢焦点，窗口被遮挡也能用；只有当
控件没有这种接口时才临时借用真实鼠标键盘，用完把指针和焦点还原。你在用电脑时，真实输入会被暂缓。

安装、连接和工具用法见上面的 Quickstart 与 [`skill/SKILL.md`](skill/SKILL.md)。它只是"手和眼"，思考仍由你的
agent 完成。

## License

MIT. See [LICENSE](LICENSE).
