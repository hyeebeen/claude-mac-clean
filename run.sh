#!/bin/bash
# claude-mac-clean — one-line entry for a fresh Mac:
#
#   curl -fsSL https://raw.githubusercontent.com/hyeebeen/claude-mac-clean/main/run.sh | bash
#
# Downloads scripts/claude_clean.py over HTTPS into a temp dir and runs it.
# Nothing is installed, no daemon, no cron; cleaning still requires typing
# CONFIRM in a real terminal. Extra args are passed through, e.g.:
#   curl -fsSL .../run.sh | bash -s -- --audit-only

set -eu

REPO='hyeebeen/claude-mac-clean'
REF="${CLAUDE_MAC_CLEAN_REF:-main}"
SCRIPT_URL="https://raw.githubusercontent.com/$REPO/$REF/scripts/claude_clean.py"

find_python() {
    # Skip /usr/bin/python3 when Command Line Tools are missing: the stub
    # would pop a GUI install dialog instead of failing cleanly.
    local candidate
    for candidate in "$(command -v python3 2>/dev/null || true)" \
        /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
        [ -n "$candidate" ] && [ -x "$candidate" ] || continue
        if [ "$candidate" = '/usr/bin/python3' ] && ! xcode-select -p >/dev/null 2>&1; then
            continue
        fi
        if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

main() {
    if [ "$(uname -s)" != 'Darwin' ]; then
        echo 'claude-mac-clean: 仅支持 macOS。' >&2
        exit 1
    fi

    local invoked_as="${BASH_SOURCE[0]:-$0}"
    local src='' py='' tmp=''
    # When run from a clone (./run.sh), use the adjacent script; otherwise download.
    if [ -f "$invoked_as" ] && [ -f "$(dirname "$invoked_as")/scripts/claude_clean.py" ]; then
        src="$(dirname "$invoked_as")/scripts/claude_clean.py"
    fi

    if [ -z "$src" ]; then
        tmp="$(mktemp -d "${TMPDIR:-/tmp}/claude-mac-clean.XXXXXX")"
        trap 'rm -rf "$tmp"' EXIT
        curl --proto '=https' --tlsv1.2 -fsSL "$SCRIPT_URL" -o "$tmp/claude_clean.py"
        src="$tmp/claude_clean.py"
    fi

    if ! py="$(find_python)"; then
        cat >&2 <<'EOF'
未找到可用的 Python 3（需要 3.9+）。
macOS 自带的 Python 包含在"命令行工具"里；运行下面的命令并按弹窗完成安装，
然后重新执行本命令：

  xcode-select --install

EOF
        exit 1
    fi

    echo "claude-mac-clean — Python: $py"
    if { : </dev/tty; } 2>/dev/null; then
        "$py" -I "$src" "$@" </dev/tty
    else
        "$py" -I "$src" "$@"
    fi
}

main "$@"
