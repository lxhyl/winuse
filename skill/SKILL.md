---
name: winuse
description: See and operate a Windows PC - screenshots, the accessibility tree, clicking, typing, window control, launching apps, running commands, changing Windows settings. Use for any task that needs the Windows desktop or a GUI program on the configured Windows machine. For plain shell commands on that PC, SSH is enough.
---

# winuse

`winuse <command>` drives a Windows desktop. It opens its own SSH tunnel and starts the agent on the
PC if needed, so just run it. The target host comes from `winuse config` / `WINUSE_SSH_HOST`.
`winuse --help` lists every command. (If `winuse` is not on PATH, call it by its path, e.g.
`./bin/winuse`.)

## The loop

1. `winuse status` - is the PC unlocked, what is in front, how long the user has been idle.
2. Look: `winuse snap --window <title or process>` prints the window's controls as text, each with a
   ref such as `@e12`. This is fast and cheap; prefer it to screenshots. Add `--interactive` for a
   shorter list. The window does not need to be in front.
3. Act on a ref: `winuse click @e12`, `winuse set @e5 "text"`, `winuse el @e7 expand|collapse|toggle|select|invoke|focus|show|text|info`.
4. Check: `snap` again. Refs are only valid until the next `snap`.

## Background first

Ref actions go through the accessibility interface: the mouse pointer does not move and focus does not
change, so they work while someone uses the PC and while the window is covered. If a control has no
such interface, winuse borrows the real mouse and keyboard, then puts the pointer and focus back; the
output says so. Add `--bg` to refuse that instead.

When the tree is empty or useless (games, canvases, custom-drawn apps), fall back to pixels:

- `winuse shot` saves a PNG and prints its path; read it. `--window X --bg` captures one window even
  if it is covered. `--region X,Y,W,H` zooms into the last screenshot. With several monitors it shows
  the one with the active window; `--display 2` or `--display all` picks another (`status` lists them).
- `winuse click 640,360` clicks that point of the LAST screenshot with the real mouse. Also
  `type "text" [--enter]`, `key ctrl+s`, `scroll down 3 --at X,Y`, `drag X1,Y1 X2,Y2`.
- Real input needs the target window in front: `winuse window <name> focus` first.
- Real input is refused while the user is active (input in the last 3 s): exit code 4. Wait and
  retry, or add `--force`.

## Other commands

- `winuse windows`, `winuse window <name> focus|back|min|max|restore|close|move X Y|resize W H`
- `winuse launch <program|document|URL> [args]` (`--no-activate` keeps focus where it is);
  `ms-settings:<page>` opens a Settings page, e.g. `ms-settings:colors`
- `winuse run "<PowerShell>"` runs inside the desktop session, elevated (`--cmd` for cmd.exe)
- `winuse clip get|set "text"`, `winuse wait "<title>" [--gone]`
- `winuse wake` sends a wake-on-LAN packet and waits for the PC (needs `mac` configured)
- `winuse agent status|restart|log` when something is off

## Installing or repairing winuse on a PC

If exit code 3 persists and `winuse agent status` shows the agent is missing, (re)install it over SSH.
Use curl.exe, not `irm`: PowerShell's web client can hang forever on some networks.

    ssh <host> 'powershell -NoProfile -Command "iex ((curl.exe -fsSL --retry 5 --retry-all-errors --connect-timeout 10 --max-time 60 https://winuse.ozlab.xyz/install.ps1) -join [char]10)"'

It prints `[winuse Ns] ...` progress lines and finally `Agent token : ...`. A fresh install creates a
new token; put it in the CLI's `.token` file. Set options in cmd first, e.g.
`ssh <host> 'set WINUSE_NOTASK=1&& set WINUSE_DIR=C:\test&& powershell ...'`. Uninstall: `set WINUSE_UNINSTALL=1&& ...`.
Registering the logon task is a persistent change to the PC: get the user's OK first.

## Things to know

- Exit code 2 = the PC is locked or a UAC prompt is up: nothing on screen can be operated. Shell
  work over SSH still works. Exit code 3 = cannot reach the PC or the agent.
- Exit code 5 = the PC is on but nobody is signed in (after a restart). There is no desktop until
  someone signs in; tell the user, and do not keep retrying. The agent starts by itself after that.
- Prefer sleep over shutdown: a sleeping PC keeps its session, so `winuse wake` brings it straight
  back with no sign-in screen.
- `type` sends Unicode directly, so any language works whatever the input method is.
- Classic drop-down lists: `winuse set @eN "item name"` chooses the item (may borrow the mouse).
- Verify changes by reading state back (`snap`, or the registry/file via `run`), not by assuming.
- Do not operate games or anti-cheat-protected software (input injection can get accounts banned).
  Change system settings only when the user asks.
