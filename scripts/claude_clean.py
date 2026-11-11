#!/usr/bin/env python3
"""claude-mac-clean — audit, back up, and narrowly clean local Claude data on macOS.

Stdlib only; compatible with Python 3.9 (the version shipped with macOS
Command Line Tools). Read-only by default. Cleaning requires a typed
confirmation in a real terminal, verifies a backup first, and only touches
the narrow target lists below. It never reads or writes the macOS Keychain,
never modifies browsers, and cannot unban an account.
"""

import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

TOOL = 'claude-mac-clean'
VERSION = '1.0.0'

DESKTOP_DIR = 'Library/Application Support/Claude'

# Narrow target lists. Anything not listed here is never touched: chat
# history, projects, sessions, skills, hooks, plugins, MCP config,
# settings.json and friends are deliberately out of scope.
CACHE_TARGETS = (
    '.claude/cache',
    '.claude/stats-cache.json',
    '.claude/telemetry',
    'Library/Caches/claude-cli-nodejs',
    'Library/Caches/com.anthropic.claudefordesktop',
    'Library/Caches/com.anthropic.claudefordesktop.ShipIt',
    'Library/Logs/Claude',
    DESKTOP_DIR + '/Cache',
    DESKTOP_DIR + '/Code Cache',
    DESKTOP_DIR + '/GPUCache',
    DESKTOP_DIR + '/DawnGraphiteCache',
    DESKTOP_DIR + '/DawnWebGPUCache',
    DESKTOP_DIR + '/GPUPersistentCache',
    DESKTOP_DIR + '/GraphiteDawnCache',
    DESKTOP_DIR + '/Crashpad',
    DESKTOP_DIR + '/sentry',
    DESKTOP_DIR + '/window-state.json',
    DESKTOP_DIR + '/vm-support-probe.json',
)
CRASH_DIRS = ('Library/Logs/DiagnosticReports', 'Library/Application Support/CrashReporter')
ACCOUNT_KEYS = ('oauthAccount', 'accountUuid', 'oauth:tokenCache',
                'oauth:tokenCacheV2', 'lastKnownAccountUuid')
ACCOUNT_REMOVALS = ('.claude/.credentials.json', DESKTOP_DIR + '/config.json.journal')
ACCOUNT_JSONS = ('.claude.json', DESKTOP_DIR + '/config.json')
ACCOUNT_JSON_BACKUPS = '.claude/backups'
DEVICE_TARGETS = (DESKTOP_DIR + '/ant-did', DESKTOP_DIR + '/ant-device-registry.json')
SCOPES = ('cache', 'account', 'device')

SCOPE_TITLES = {
    'cache': '缓存与日志（不影响登录）',
    'account': '账号字段（影响登录状态）',
    'device': '设备记录（ant-did / 登记文件）',
}

BACKUP_DIRNAME = 'ClaudeCleanBackups'
RESERVE_BYTES = 100 * 1024 * 1024

# Crash logs are named after the crashing process, e.g.
#   claude-2026-11-10-101010_Host.ips | Claude_2024-01-02-030405_Host.crash
#   Claude Helper (GPU)-2026-11-10-101010_Host.ips | ChatGPT_8C33....plist
# Only accept reports whose process name is unambiguously Claude's.
REPORT_NAME_RE = re.compile(
    r'^(?P<proc>.+?)[-_]'
    r'(?:\d{4}-\d{2}-\d{2}|[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}'
    r'-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})')
CLAUDE_PROC_NAME_RE = re.compile(
    r'^(?:claude(?:\.exe|-code)?|claude helper)(?:[ .(].*)?$', re.IGNORECASE)

CLAUDE_EXE_NAMES = ('claude', 'claude.exe', 'claude-code')
NODE_LIKE_EXE_NAMES = ('node', 'bun', 'deno', 'npm', 'npx')

CLAUDE_DOMAINS = ('claude.ai', 'claude.com', 'anthropic.com')
CLAUDE_EXTENSION_ID = 'fcoeoabgfenejglbffodgkkbkcdhcgfn'  # known example; verify in chrome://extensions
CHROMIUM_BROWSERS = (
    ('Google Chrome', 'Library/Application Support/Google/Chrome', '/Google Chrome.app/'),
    ('Microsoft Edge', 'Library/Application Support/Microsoft Edge', '/Microsoft Edge.app/'),
    ('Brave', 'Library/Application Support/BraveSoftware/Brave-Browser', '/Brave Browser.app/'),
    ('Arc', 'Library/Application Support/Arc/User Data', '/Arc.app/'),
    ('Chromium', 'Library/Application Support/Chromium', '/Chromium.app/'),
)
CHROMIUM_PROFILE_RE = re.compile(r'^(?:Default|Profile \d+)$')
ORIGIN_DIR_RE = re.compile(r'^(?:https?)_(?P<host>[^_]+)_\d+\.indexeddb\.(?:leveldb|blob)$')


class CleanError(Exception):
    """An expected stop condition; the message is shown to the user as-is."""


# ---------------------------------------------------------------- safeguards

def guard_path(home, path):
    """Refuse anything outside home or reached through a symlink component."""
    try:
        rel = path.relative_to(home)
    except ValueError:
        raise CleanError('拒绝主目录之外的路径: %s' % path)
    if '..' in rel.parts:
        raise CleanError('拒绝包含 .. 的路径: %s' % path)
    current = home
    for part in rel.parts:
        current = current / part
        if current.is_symlink():
            raise CleanError('拒绝符号链接路径: %s' % current)
    return path


def sha256_of(path):
    digest = hashlib.sha256()
    with open(str(path), 'rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint(path):
    """Describe every entry under path; refuse symlinks and special files."""
    entries = {}

    def record(target, key):
        info = target.lstat()
        mode = info.st_mode
        if stat.S_ISLNK(mode):
            raise CleanError('拒绝符号链接: %s' % target)
        if stat.S_ISDIR(mode):
            entries[key] = {'type': 'dir'}
            return True
        if not stat.S_ISREG(mode):
            raise CleanError('拒绝非普通文件: %s' % target)
        entries[key] = {'type': 'file', 'size': info.st_size, 'sha256': sha256_of(target)}
        return False

    if record(path, '.'):
        for root, dirs, files in os.walk(str(path)):
            for name in sorted(dirs + files):
                record(Path(root) / name, str(Path(root).joinpath(name).relative_to(path)))
    return entries


def tree_size(path):
    total = 0
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    for root, _dirs, files in os.walk(str(path)):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total


# ------------------------------------------------------------- process guard

def ps_column(fmt):
    proc = subprocess.run(['/bin/ps', '-axo', 'pid=,' + fmt + '='],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    rows = {}
    for line in proc.stdout.decode('utf-8', 'replace').splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and parts[0].isdigit():
            rows[int(parts[0])] = parts[1]
    return rows


def is_claude_invocation(comm, args):
    """True when a ps row unambiguously belongs to Claude Code or Claude Desktop.

    Covers the native binary, the npm wrapper (process name may be
    claude.exe), the Electron app, and node-run installs — the cases a plain
    name match misses.
    """
    exe_name = os.path.basename(comm.strip()).lower()
    if exe_name in CLAUDE_EXE_NAMES or '/Claude.app/' in comm:
        return True
    if '@anthropic-ai/claude-code' in comm or '@anthropic-ai/claude-code' in args:
        return True
    tokens = args.split()
    if tokens:
        first = os.path.basename(tokens[0]).lower()
        if first in CLAUDE_EXE_NAMES or '/claude/versions/' in tokens[0]:
            return True
        if len(tokens) >= 2 and first in NODE_LIKE_EXE_NAMES \
                and os.path.basename(tokens[1]).lower() in ('claude', 'claude.exe'):
            return True
    return False


def claude_processes():
    found = []
    comms = ps_column('comm')
    argss = ps_column('args')
    for pid, comm in comms.items():
        args = argss.get(pid, '')
        if is_claude_invocation(comm, args):
            found.append((pid, os.path.basename(comm.strip()) or args[:48]))
    return found


def assert_not_running():
    running = claude_processes()
    if running:
        detail = ', '.join('pid %d %s' % (pid, name) for pid, name in running[:5])
        raise CleanError('Claude 相关进程仍在运行（%s）；请先正常退出再执行清理。' % detail)


# --------------------------------------------------------------------- plan

def load_json_object(path):
    try:
        data = json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise CleanError('无法解析 JSON，已停止: %s (%s)' % (path, type(exc).__name__))
    if not isinstance(data, dict):
        raise CleanError('JSON 顶层不是对象，已停止: %s' % path)
    return data


def is_claude_report(name):
    match = REPORT_NAME_RE.match(name)
    if not match:
        return False
    return bool(CLAUDE_PROC_NAME_RE.match(match.group('proc').strip()))


def _existing_guarded(home, path):
    """True when there is something to clean at path; refuse symlinked paths."""
    if not (path.exists() or path.is_symlink()):
        return False
    guard_path(home, path)
    return True


def scope_plan(home, scope):
    """Candidate moves and JSON edits for one scope; read-only."""
    moves, edits = [], []
    if scope == 'cache':
        for rel in CACHE_TARGETS:
            path = home / rel
            if _existing_guarded(home, path):
                moves.append(path)
        for rel in CRASH_DIRS:
            parent = guard_path(home, home / rel)
            if parent.is_dir():
                for child in sorted(parent.iterdir()):
                    if is_claude_report(child.name):
                        moves.append(child)
    elif scope == 'device':
        for rel in DEVICE_TARGETS:
            path = home / rel
            if _existing_guarded(home, path):
                moves.append(path)
    elif scope == 'account':
        for rel in ACCOUNT_REMOVALS:
            path = home / rel
            if _existing_guarded(home, path):
                moves.append(path)
        json_files = [home / rel for rel in ACCOUNT_JSONS]
        backup_dir = home / ACCOUNT_JSON_BACKUPS
        if _existing_guarded(home, backup_dir) and backup_dir.is_dir():
            json_files.extend(sorted(backup_dir.glob('.claude.json.backup.*')))
        for path in json_files:
            if path.exists():
                guard_path(home, path)
                keys = [k for k in ACCOUNT_KEYS if k in load_json_object(path)]
                if keys:
                    edits.append((path, keys))
    return {'moves': moves, 'edits': edits}


def build_plans(home, scopes):
    return {scope: scope_plan(home, scope) for scope in SCOPES if scope in scopes}


def plan_summary(home, plans):
    lines = []
    for scope in SCOPES:
        if scope not in plans:
            continue
        moves = [p for p in plans[scope]['moves'] if p.exists() or p.is_symlink()]
        edits = plans[scope]['edits']
        lines.append((scope, moves, edits))
    return lines


# -------------------------------------------------------------------- write

def write_json_atomic(path, data, mode=None):
    if mode is None:
        mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix='.' + TOOL + '-tmp-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# -------------------------------------------------------------------- clean

def make_private_dir(path):
    """Create path plus any missing parents with mode 0700.

    os.makedirs only applies `mode` to the leaf; intermediate directories
    would fall back to 0777&~umask, which is too open for backup holders.
    """
    path = Path(path)
    if path.is_dir():
        os.chmod(str(path), 0o700)
        return
    missing = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    for item in reversed(missing):
        os.mkdir(str(item), 0o700)
    os.chmod(str(path), 0o700)


def run_clean(home, scopes, check=assert_not_running):
    """Back up, verify, then apply the plan. Stops on any mismatch."""
    check()
    plans = build_plans(home, scopes)
    moves = sorted({p for plan in plans.values() for p in plan['moves']
                    if p.exists() or p.is_symlink()})
    edits = []
    seen = set()
    for plan in plans.values():
        for path, keys in plan['edits']:
            if path not in seen:
                seen.add(path)
                edits.append((path, keys))
    sources = moves + [path for path, _keys in edits]
    if not sources:
        return {'status': 'nothing-to-do', 'moved': [], 'edited': []}

    snapshots = {p: fingerprint(p) for p in sources}
    needed = sum(entry.get('size', 0) for snap in snapshots.values()
                 for entry in snap.values()) + RESERVE_BYTES
    if shutil.disk_usage(str(home)).free < needed:
        raise CleanError('磁盘可用空间不足，无法先完成备份。')

    now = dt.datetime.now()
    stamp = now.strftime('%Y%m%d-%H%M%S-%f')
    backup_root = guard_path(home, home / BACKUP_DIRNAME / ('claude-mac-clean-' + stamp))
    trash_root = guard_path(home, home / '.Trash' / (TOOL + '-' + stamp))
    make_private_dir(backup_root)

    report = {
        'tool': TOOL, 'version': VERSION, 'started': now.isoformat(),
        'status': 'backing-up', 'scopes': sorted(scopes),
        'backup': str(backup_root), 'trash': str(trash_root),
        'moved': [], 'edited': [],
    }
    report_path = backup_root / 'report.json'

    def save_report():
        try:
            write_json_atomic(report_path, report)
        except OSError:
            pass

    try:
        save_report()
        for path in sources:
            target = backup_root / path.relative_to(home)
            make_private_dir(target.parent)
            if path.is_dir():
                shutil.copytree(str(path), str(target), symlinks=True)
            else:
                shutil.copy2(str(path), str(target))
            if fingerprint(target) != snapshots[path]:
                raise CleanError('备份校验失败，已停止（原数据未动）: %s' % path)
        write_json_atomic(backup_root / 'hashes.json',
                          {str(p.relative_to(home)): snapshots[p] for p in sources})

        check()
        for path in sources:
            guard_path(home, path)
            if fingerprint(path) != snapshots[path]:
                raise CleanError('备份期间源数据发生变化，已停止: %s' % path)

        report['status'] = 'applying'
        save_report()
        make_private_dir(trash_root)
        for path in moves:
            destination = trash_root / path.relative_to(home)
            make_private_dir(destination.parent)
            os.rename(str(path), str(destination))
            report['moved'].append(str(path.relative_to(home)))
            save_report()
            if fingerprint(destination) != snapshots[path]:
                raise CleanError('废纸篓副本校验失败，已停止: %s' % path)

        for path, keys in edits:
            data = load_json_object(path)
            for key in keys:
                data.pop(key, None)
            write_json_atomic(path, data)
            report['edited'].append({'path': str(path.relative_to(home)), 'keys': keys})
            save_report()
            if json.loads(path.read_text(encoding='utf-8')) != data:
                raise CleanError('JSON 写入校验失败，已停止: %s' % path)

        report['status'] = 'complete'
    except BaseException as exc:
        report['status'] = 'stopped' if isinstance(exc, CleanError) else 'error'
        report['error'] = '%s: %s' % (type(exc).__name__, exc)
        raise
    finally:
        save_report()
    return report


# ----------------------------------------------------------------- browsers

def running_comm_lines():
    try:
        return list(ps_column('comm').values())
    except (OSError, subprocess.SubprocessError):
        return []


def host_is_claude(host):
    host = host.lstrip('.').lower()
    return any(host == d or host.endswith('.' + d) for d in CLAUDE_DOMAINS)


def cookie_host_counts(db_path):
    uri = 'file:' + quote(str(db_path)) + '?mode=ro'
    conn = sqlite3.connect(uri, uri=True, timeout=1)
    try:
        rows = conn.execute('SELECT host_key, COUNT(*) FROM cookies GROUP BY host_key').fetchall()
    finally:
        conn.close()
    return {host: count for host, count in rows if host_is_claude(host)}


def inspect_chromium(name, root, is_running):
    profiles = []
    for child in sorted(root.iterdir()):
        if not (child.is_dir() and CHROMIUM_PROFILE_RE.match(child.name)):
            continue
        info = {'profile': child.name, 'indexeddb': [], 'extension': False,
                'cookies': None, 'cookie_note': ''}
        indexeddb = child / 'IndexedDB'
        if indexeddb.is_dir():
            for entry in sorted(indexeddb.iterdir()):
                match = ORIGIN_DIR_RE.match(entry.name)
                if match and host_is_claude(match.group('host')):
                    info['indexeddb'].append(match.group('host').lower())
        if (child / 'Extensions' / CLAUDE_EXTENSION_ID).is_dir():
            info['extension'] = True
        for rel in ('Cookies', 'Network/Cookies'):
            db_path = child / rel
            if db_path.is_file():
                if is_running:
                    info['cookie_note'] = '浏览器正在运行，按约定不读数据库'
                else:
                    try:
                        info['cookies'] = cookie_host_counts(db_path)
                    except (sqlite3.Error, OSError) as exc:
                        info['cookie_note'] = '无法只读读取（%s）' % type(exc).__name__
                break
        profiles.append(info)
    return {'name': name, 'running': is_running, 'profiles': profiles}


def browser_inventory(home):
    """Read-only site-data inventory for installed Chromium browsers."""
    comms = running_comm_lines()
    results = []
    for name, rel, app_marker in CHROMIUM_BROWSERS:
        root = home / rel
        if not root.is_dir():
            continue
        results.append(inspect_chromium(
            name, guard_path(home, root), any(app_marker in line for line in comms)))
    return results


def format_cookies(counts):
    if counts is None:
        return ''
    return '；Cookies: ' + (', '.join('%s %d' % (host, count)
                                      for host, count in sorted(counts.items())) or '0 条')


def print_browser_report(home):
    print('[浏览器] 站点数据只读盘点（本工具不修改浏览器）')
    results = browser_inventory(home)
    for browser in results:
        state = '运行中' if browser['running'] else '未运行'
        print('  %s（%s）' % (browser['name'], state))
        for profile in browser['profiles']:
            bits = ['Profile ' + profile['profile'] if profile['profile'] != 'Default'
                    else 'Default']
            if profile['indexeddb']:
                bits.append('IndexedDB: ' + ', '.join(sorted(set(profile['indexeddb']))))
            bits.append(format_cookies(profile['cookies']))
            if profile['extension']:
                bits.append('Claude 扩展: 存在')
            if profile['cookie_note']:
                bits.append(profile['cookie_note'])
            print('    ' + '；'.join(bit for bit in bits if bit))
    if not results:
        print('  未发现已安装的 Chromium 浏览器')
    print('  未检查: Local/Session Storage、Service Worker、Safari。')
    print('  清理建议: 退出浏览器后，在其「设置 → 隐私 → 站点数据」里按')
    print('  claude.ai / claude.com / anthropic.com 逐站删除；不要按关键词整库删除')
    print('  Persona、Cloudflare、hCaptcha 等第三方分区（它们同时服务其他站点）。')


# -------------------------------------------------------------------- audit

def rel_display(path, home):
    return '~/' + str(path.relative_to(home))


def human_size(num):
    for unit in ('B', 'KB', 'MB', 'GB'):
        if num < 1024 or unit == 'GB':
            return ('%d %s' % (num, unit)) if unit == 'B' else ('%.1f %s' % (num, unit))
        num /= 1024.0


def print_audit(home, plans):
    running = claude_processes()
    print('claude-mac-clean %s — 只读审计，不修改任何文件' % VERSION)
    print('Claude 进程: ' + ('未检测到 ✓' if not running else
          '正在运行: ' + ', '.join('pid %d %s' % row for row in running[:5])))
    summary = []
    for scope, moves, edits in plan_summary(home, plans):
        total = sum(tree_size(p) for p in moves)
        summary.append((scope, moves, edits, total))
        print('[%s] %s — %d 个移除目标，%d 个待编辑文件，共 %s' %
              (scope, SCOPE_TITLES[scope], len(moves), len(edits), human_size(total)))
        for path in moves:
            print('    移除 %s (%s)' % (rel_display(path, home), human_size(tree_size(path))))
        for path, keys in edits:
            print('    编辑 %s: 移除字段 %s' % (rel_display(path, home), ', '.join(keys)))
        if scope == 'account' and edits:
            print('    说明: macOS 的登录令牌保存在系统钥匙串，本工具不读取也不修改钥匙串；')
            print('    若要真正退出登录，请先在 Claude Code 中执行 /logout。')
    if not summary:
        print('（所选范围内没有发现可清理的内容）')
    return {scope: {'moves': len(moves), 'edits': len(edits), 'size': total}
            for scope, moves, edits, total in summary}


# ----------------------------------------------------------------- controls

def ask_scopes(counts):
    offered = [s for s in SCOPES if s in counts and
               (counts[s]['moves'] or counts[s]['edits'])]
    if not offered:
        return None
    print('\n可清理内容如上。清理流程: 先完整备份并逐文件校验 SHA-256，')
    print('再把移除项移入废纸篓（不清空），最后按白名单编辑 JSON。')
    print('可选范围:')
    numbered = {}
    for index, scope in enumerate(offered, 1):
        numbered[str(index)] = scope
        print('  %d) %-7s %s（%d 个目标）' %
              (index, scope, SCOPE_TITLES[scope],
               counts[scope]['moves'] + counts[scope]['edits']))
    raw = input('输入要清理的编号（空格分隔，如 1 2；直接回车=取消）: ').strip()
    chosen = []
    for token in raw.split():
        if token not in numbered or numbered[token] in chosen:
            print('无法识别的选择: %s' % token)
            return None
        chosen.append(numbered[token])
    if not chosen:
        print('已取消，未修改任何数据。')
        return None
    print('\n即将清理: ' + '、'.join(chosen))
    print('旧数据会保留在备份目录和废纸篓中，确认无误后需自行删除。')
    answer = input("确认执行请输入 CONFIRM（输入其他内容=取消）: ").strip()
    if answer != 'CONFIRM':
        print('已取消，未修改任何数据。')
        return None
    return chosen


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(
        prog=TOOL, description='审计、备份并定向清理 macOS 上 Claude 的本地数据。')
    parser.add_argument('--scope', action='append', choices=SCOPES,
                        help='限定范围（默认全部）；可重复传入')
    parser.add_argument('--audit-only', action='store_true',
                        help='只读审计；不进入清理确认')
    parser.add_argument('--version', action='version', version=TOOL + ' ' + VERSION)
    args = parser.parse_args(argv)
    if sys.platform != 'darwin':
        parser.error('本工具仅支持 macOS。')

    home = Path.home().resolve()
    scopes = set(args.scope) if args.scope else set(SCOPES)
    plans = build_plans(home, scopes)
    counts = print_audit(home, plans)
    print()
    print_browser_report(home)

    if args.audit_only or not sys.stdin.isatty():
        if not args.audit_only:
            print('\n当前不是交互终端；如需清理，请在终端中重新运行本命令。')
        return 0
    chosen = ask_scopes(counts)
    if not chosen:
        return 0
    report = run_clean(home, set(chosen))
    if report['status'] == 'complete':
        print('\n清理完成。备份: %s' % report['backup'])
        print('移除项在: %s（未清空废纸篓）' % report['trash'])
        print('报告: %s/report.json（含 SHA-256 校验表 hashes.json，可用于恢复核对）' % report['backup'])
        print('确认一切正常后，再自行删除该备份目录并清空废纸篓。')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except CleanError as exc:
        print('%s: 已停止: %s' % (TOOL, exc), file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print('\n已中断；已完成步骤见最近一次备份目录中的 report.json。', file=sys.stderr)
        sys.exit(130)
