#!/bin/sh
# winuse one-line installer for the machine you control FROM (macOS / Linux).
#
#     curl -fsSL https://winuse.ozlab.xyz/install.sh | sh
#
# Installs the winuse CLI + MCP into ~/.local, then asks for the Windows PC's SSH host and token.
# Flags (pass after `| sh -s --`, or run the downloaded file directly):
#     --skill            also install the Claude Code skill into ~/.claude/skills/winuse
#     --host NAME        SSH host of the Windows PC (skip the prompt)
#     --token VALUE      agent token printed by the Windows installer (skip the prompt)
#     --uninstall        remove winuse
#
# winuse's CLI is pure Python standard library — no pip packages are installed.
set -eu

REPO="https://github.com/lxhyl/winuse"
PREFIX="${WINUSE_PREFIX:-$HOME/.local}"
SHARE="$PREFIX/share/winuse"
BIN="$PREFIX/bin"
SKILL_DIR="$HOME/.claude/skills/winuse"
DO_SKILL=0; UNINSTALL=0; HOST=""; TOKEN=""

while [ $# -gt 0 ]; do
    case "$1" in
        --skill) DO_SKILL=1 ;;
        --uninstall) UNINSTALL=1 ;;
        --host) HOST="${2:-}"; shift ;;
        --token) TOKEN="${2:-}"; shift ;;
        --host=*) HOST="${1#--host=}" ;;
        --token=*) TOKEN="${1#--token=}" ;;
        *) echo "unknown option: $1" >&2 ;;
    esac
    shift
done

say() { printf '%s\n' "$*"; }

if [ "$UNINSTALL" = 1 ]; then
    rm -f "$BIN/winuse" "$BIN/winuse-mcp"
    rm -rf "$SHARE"
    [ -d "$SKILL_DIR" ] && rm -rf "$SKILL_DIR" || true
    say "winuse removed from $SHARE (your config/token there are gone too)."
    exit 0
fi

# --- detect Python 3.10+ ------------------------------------------------------
PY=""
for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1; then
        if "$c" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3,10) else 1)' 2>/dev/null; then
            PY="$c"; break
        fi
    fi
done
if [ -z "$PY" ]; then
    say "winuse needs Python 3.10+ on PATH. Install it (macOS: brew install python; Linux: your package manager) and re-run." >&2
    exit 1
fi
say "Using $("$PY" --version 2>&1) ($(command -v "$PY"))"
command -v uv >/dev/null 2>&1 && say "(uv detected — not required; winuse uses only the standard library.)" || true

# --- fetch the repo into ~/.local/share/winuse --------------------------------
mkdir -p "$PREFIX/share" "$BIN"
if command -v git >/dev/null 2>&1; then
    if [ -d "$SHARE/.git" ]; then
        say "Updating winuse in $SHARE ..."; git -C "$SHARE" pull --quiet --ff-only || true
    else
        rm -rf "$SHARE"; say "Cloning winuse into $SHARE ..."; git clone --depth 1 "$REPO.git" "$SHARE" >/dev/null 2>&1
    fi
else
    say "Downloading winuse into $SHARE ..."
    tmp="$(mktemp -d)"; curl -fsSL "$REPO/archive/refs/heads/main.tar.gz" | tar -xz -C "$tmp"
    rm -rf "$SHARE"; mv "$tmp/winuse-main" "$SHARE"; rm -rf "$tmp"
fi
[ -f "$SHARE/bin/winuse" ] || { say "download looks incomplete: $SHARE" >&2; exit 1; }

# --- symlink the commands -----------------------------------------------------
ln -sf "$SHARE/bin/winuse" "$BIN/winuse"
ln -sf "$SHARE/bin/winuse-mcp" "$BIN/winuse-mcp"
chmod +x "$SHARE/bin/winuse" "$SHARE/bin/winuse-mcp"

# --- interactive config (host + token) ----------------------------------------
TTY=/dev/tty
if [ -z "$HOST" ] && [ -r "$TTY" ]; then
    printf 'SSH host of the Windows PC (a name in your ~/.ssh/config): ' > "$TTY"
    IFS= read -r HOST < "$TTY" || true
fi
if [ -z "$TOKEN" ] && [ -r "$TTY" ]; then
    printf 'Agent token (printed by the Windows installer): ' > "$TTY"
    IFS= read -r TOKEN < "$TTY" || true
fi
[ -n "$HOST" ] && "$SHARE/bin/winuse" config set host "$HOST" >/dev/null || true
if [ -n "$TOKEN" ]; then
    printf '%s' "$TOKEN" > "$SHARE/.token"; chmod 600 "$SHARE/.token"
fi

# --- optional Claude skill ----------------------------------------------------
if [ "$DO_SKILL" = 1 ]; then
    mkdir -p "$SKILL_DIR"; cp "$SHARE/skill/SKILL.md" "$SKILL_DIR/SKILL.md"
    say "Installed the Claude Code skill to $SKILL_DIR"
fi

say ""
say "=== winuse CLI installed ==="
case ":$PATH:" in
    *":$BIN:"*) ;;
    *) say "NOTE: $BIN is not on your PATH. Add it:  export PATH=\"$BIN:\$PATH\"" ;;
esac
if [ -n "$HOST" ] && [ -n "$TOKEN" ]; then
    say "Try it:  winuse status"
else
    say "Finish setup:  winuse config set host <ssh-host>   and put the token in $SHARE/.token"
fi
