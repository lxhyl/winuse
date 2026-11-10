# Security model

winuse gives an AI agent full control of a Windows desktop. Treat it with the same care as remote
access to the machine itself.

## What it can do

The winuse host runs **elevated** in the signed-in user's session. Through it, a caller can see the screen,
move the mouse and keyboard, manage windows, launch programs, and run arbitrary PowerShell (`run`).
There is no sandbox. Anyone who can make authenticated calls can do anything the signed-in user can.

Programs started with `launch` run as the desktop user, without administrator rights: Explorer
starts them, as if the user had clicked them. Only `launch --admin` and `run` use the host's
elevated rights.

### Script mode

`winuse py` (MCP `run_python`) runs Python code inside the winuse host process, with helpers for the same
actions as the single commands. This is the same trust level as `run`, which already executes any
PowerShell elevated: whoever can call the host can already do anything on the PC. Script mode adds no
new capability; it only saves round trips. Every action a script takes goes through the same checks
(lock screen, user present, privilege level) and is written to the action log like a single command.
The script source itself is not logged, only its length.

## Boundaries

1. **Network.** The winuse host binds `127.0.0.1` only — it is not reachable from the LAN. The sole remote
   path is an SSH tunnel you open to the PC. **Your SSH key is the real security boundary.** Protect
   it; anyone with it already has a shell on the machine.
2. **Token.** Every request carries a bearer token (`C:\winuse\token.txt`, readable by Administrators
   and SYSTEM only). It stops other local processes on the PC from using the host to escalate.
3. **User-present guard.** Real mouse/keyboard input is refused while someone is actively using the PC
   (input within the last few seconds), unless `--force` is passed. Background (accessibility) actions
   are not gated this way, since they do not move the pointer or take focus.
4. **Lock screen.** When the session is locked or a UAC secure-desktop prompt is up, the desktop is
   not interactive; winuse reports it and refuses input rather than acting blindly.

## What it does not do

- **No telemetry.** Nothing leaves the machine. There is no analytics, no phone-home.
- **Typed text is never stored.** The action log records method names and lengths, not content, and
  password fields are masked in the accessibility tree.
- **No stored credentials.** winuse does not store your Windows password or PIN anywhere.

## Reducing risk

- Keep the SSH key passphrase-protected and off shared machines.
- Restrict what your AI agent is asked to do; review the action log in `C:\winuse\logs`.
- If you expose the port beyond localhost (not recommended), require the token and an IP allowlist,
  and use TLS.

## Reporting

This is early software. If you find a vulnerability, open a private report to the repository owner
rather than a public issue.
