"""Safety tests for claude_clean.py — everything runs in temporary directories."""
import importlib.util
import json
import os
import sqlite3
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    'claude_clean', Path(__file__).parents[1] / 'scripts' / 'claude_clean.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)

NO_GUARD = lambda: None  # noqa: E731


def make_home():
    return Path(tempfile.mkdtemp(prefix='claude-mac-clean-test-'))


class CacheTests(unittest.TestCase):
    def test_clean_backs_up_verifies_then_moves_to_trash(self):
        home = make_home()
        data = home / '.claude' / 'cache'
        data.mkdir(parents=True)
        (data / 'blob.bin').write_bytes(b'claude-cache-payload')
        original_stat = (data / 'blob.bin').stat()

        report = c.run_clean(home, {'cache'}, check=NO_GUARD)

        self.assertEqual(report['status'], 'complete')
        self.assertIn('.claude/cache', report['moved'])
        self.assertFalse(data.exists())
        kept = Path(report['backup']) / '.claude' / 'cache' / 'blob.bin'
        trashed = Path(report['trash']) / '.claude' / 'cache' / 'blob.bin'
        self.assertEqual(kept.read_bytes(), b'claude-cache-payload')
        self.assertEqual(trashed.read_bytes(), b'claude-cache-payload')
        self.assertEqual(kept.stat().st_size, original_stat.st_size)
        self.assertEqual(stat.S_IMODE(kept.stat().st_mode),
                         stat.S_IMODE(original_stat.st_mode))
        # every directory the tool created is private
        for root in (Path(report['backup']), Path(report['trash']), Path(report['trash']).parent):
            self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700, str(root))
        saved = json.loads((Path(report['backup']) / 'report.json').read_text())
        self.assertEqual(saved['status'], 'complete')

    def test_backup_failure_leaves_original_untouched(self):
        home = make_home()
        target = home / '.claude' / 'stats-cache.json'
        target.parent.mkdir(parents=True)
        target.write_text('{"ok": true}')
        with patch.object(c.shutil, 'copy2', side_effect=OSError('disk hiccup')):
            with self.assertRaises(OSError):
                c.run_clean(home, {'cache'}, check=NO_GUARD)
        self.assertEqual(target.read_text(), '{"ok": true}')
        self.assertFalse((home / '.Trash').exists())

    def test_audit_is_read_only(self):
        home = make_home()
        config = home / '.claude.json'
        config.write_text('{"oauthAccount": {"x": 1}, "projects": {}}')
        cache = home / '.claude' / 'cache'
        cache.mkdir(parents=True)
        (cache / 'f').write_text('x')
        (home / '.claude' / 'stats-cache.json').write_text('{}')
        before = c.fingerprint(home)
        plans = c.build_plans(home, set(c.SCOPES))
        existing = [p for p in plans['cache']['moves'] if p.exists()]
        self.assertEqual(len(existing), 2)
        self.assertEqual(plans['account']['edits'][0][1], ['oauthAccount'])
        self.assertEqual(c.fingerprint(home), before)

    def test_symlinked_component_refused(self):
        outer = make_home()
        home = outer / 'home'
        home.mkdir()
        real = outer / 'elsewhere'
        real.mkdir()
        (home / '.claude').symlink_to(real)
        (real / 'cache').mkdir()
        with self.assertRaises(c.CleanError):
            c.build_plans(home, {'cache'})
        self.assertTrue((real / 'cache').exists())


class AccountTests(unittest.TestCase):
    def test_edits_whitelist_keys_and_keep_everything_else(self):
        home = make_home()
        config = home / '.claude.json'
        original = {
            'oauthAccount': {'emailAddress': 'synthetic@example.invalid'},
            'accountUuid': '00000000-0000-0000-0000-000000000000',
            'projects': {'/tmp/demo': {'history': ['kept']}},
            'mcpServers': {'demo': {'command': 'kept'}},
            'userID': 'stable-id-kept',
            'unknownTopLevel': {'kept': True},
        }
        config.write_text(json.dumps(original), encoding='utf-8')

        report = c.run_clean(home, {'account'}, check=NO_GUARD)

        self.assertEqual(report['status'], 'complete')
        after = json.loads(config.read_text(encoding='utf-8'))
        self.assertNotIn('oauthAccount', after)
        self.assertNotIn('accountUuid', after)
        self.assertEqual(after['projects'], original['projects'])
        self.assertEqual(after['mcpServers'], original['mcpServers'])
        self.assertEqual(after['userID'], original['userID'])
        self.assertEqual(after['unknownTopLevel'], original['unknownTopLevel'])
        restored = json.loads(
            (Path(report['backup']) / '.claude.json').read_text(encoding='utf-8'))
        self.assertEqual(restored, original)

    def test_rolling_backups_are_edited_too(self):
        home = make_home()
        backups = home / '.claude' / 'backups'
        backups.mkdir(parents=True)
        (backups / '.claude.json.backup.123').write_text('{"oauthAccount": {}}')
        (backups / '.claude.json.backup.456').write_text('{"oauthAccount": {}, "note": 1}')
        report = c.run_clean(home, {'account'}, check=NO_GUARD)
        edited = {item['path'] for item in report['edited']}
        self.assertEqual(len(edited), 2)
        self.assertEqual(
            json.loads((backups / '.claude.json.backup.456').read_text()), {'note': 1})

    def test_json_with_bom_still_parses_and_rewrites_utf8(self):
        home = make_home()
        config = home / '.claude.json'
        config.write_bytes('\ufeff'.encode('utf-8') + b'{"oauthAccount": {}, "keep": 1}')
        report = c.run_clean(home, {'account'}, check=NO_GUARD)
        self.assertEqual(report['status'], 'complete')
        after = json.loads(config.read_text(encoding='utf-8'))
        self.assertEqual(after, {'keep': 1})

    def test_broken_json_stops_without_writing(self):
        home = make_home()
        config = home / '.claude.json'
        config.write_text('{not json', encoding='utf-8')
        with self.assertRaises(c.CleanError):
            c.build_plans(home, {'account'})
        self.assertEqual(config.read_text(encoding='utf-8'), '{not json')

    def test_device_records_removed_when_chosen(self):
        home = make_home()
        did = home / 'Library' / 'Application Support' / 'Claude' / 'ant-did'
        did.parent.mkdir(parents=True)
        did.write_text('device-id')
        report = c.run_clean(home, {'device'}, check=NO_GUARD)
        self.assertEqual(report['moved'], [str(did.relative_to(home))])
        self.assertFalse(did.exists())


class CrashReportNameTests(unittest.TestCase):
    def test_accepts_claude_process_reports(self):
        for name in (
            'claude-2026-11-10-101010_Host.ips',
            'Claude_2024-01-02-030405_Host.crash',
            'Claude Helper (GPU)-2026-11-10-101010_Host.ips',
            'claude-code-2026-11-10-101010_Host.ips',
        ):
            self.assertTrue(c.is_claude_report(name), name)

    def test_rejects_other_processes(self):
        for name in (
            'ClaudeBar-2026-11-10-101010_Host.ips',
            'SFA-CloudServices.json_2026-10-01-121212_Unknown.diag',
            'ChatGPT_8C339A2B-1234-5678-90AB-CDEF12345678.plist',
            'Claudia_2026-11-10-101010_Host.ips',
            'claudehelper-2026-11-10-101010_Host.ips',
        ):
            self.assertFalse(c.is_claude_report(name), name)


class ProcessGuardTests(unittest.TestCase):
    def test_recognises_claude_processes(self):
        cases = [
            ('/Applications/Claude.app/Contents/MacOS/Claude', ''),
            ('claude', 'claude --version'),
            ('/Users/x/.npm-global/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe',
             'claude'),
            ('/usr/local/bin/node',
             'node /usr/local/lib/node_modules/@anthropic-ai/claude-code/cli.js'),
            ('/usr/bin/node', 'node /Users/x/.local/bin/claude'),
            ('/Users/x/.local/share/claude/versions/2.1.5',
             '/Users/x/.local/share/claude/versions/2.1.5'),
        ]
        for comm, args in cases:
            self.assertTrue(c.is_claude_invocation(comm, args), '%s | %s' % (comm, args))

    def test_ignores_unrelated_processes(self):
        cases = [
            ('/usr/bin/python3', '/usr/bin/python3 /opt/tool/claude_clean.py --audit-only'),
            ('/bin/zsh', 'vim /Users/x/.claude.json'),
            ('/usr/bin/grep', 'grep -ri claude /tmp/notes'),
            ('/Applications/ZCode.app/Contents/MacOS/zcode', ''),
            ('/usr/sbin/cfprefsd', ''),
        ]
        for comm, args in cases:
            self.assertFalse(c.is_claude_invocation(comm, args), '%s | %s' % (comm, args))


class BrowserInventoryTests(unittest.TestCase):
    def _make_profile(self, home, browser_root, profile):
        base = home / browser_root / profile
        (base / 'IndexedDB' / 'https_claude.ai_0.indexeddb.leveldb').mkdir(parents=True)
        (base / 'IndexedDB' / 'https_claude.ai_0.indexeddb.blob').mkdir(parents=True)
        (base / 'IndexedDB' / 'https_chatgpt.com_0.indexeddb.leveldb').mkdir(parents=True)
        (base / 'IndexedDB' / 'https_github.com_0.indexeddb.leveldb').mkdir(parents=True)
        (base / 'Extensions' / c.CLAUDE_EXTENSION_ID / '1.0_0').mkdir(parents=True)
        db = base / 'Cookies'
        conn = sqlite3.connect(str(db))
        conn.execute('CREATE TABLE cookies (host_key TEXT, name TEXT)')
        for host in ('claude.ai', '.claude.ai', 'statsig.anthropic.com',
                     'chatgpt.com', 'example.com'):
            conn.execute('INSERT INTO cookies VALUES (?, ?)', (host, 'n'))
        conn.commit()
        conn.close()

    def test_inventory_reports_only_claude_origins(self):
        home = make_home()
        self._make_profile(home, 'Library/Application Support/Google/Chrome', 'Default')
        inspected = c.inspect_chromium('Google Chrome',
                                       home / 'Library/Application Support/Google/Chrome',
                                       False)
        profile = inspected['profiles'][0]
        self.assertEqual(sorted(set(profile['indexeddb'])), ['claude.ai'])
        self.assertTrue(profile['extension'])
        self.assertEqual(profile['cookies'],
                         {'.claude.ai': 1, 'claude.ai': 1, 'statsig.anthropic.com': 1})

    def test_installed_browser_is_listed_even_when_running(self):
        home = make_home()
        self._make_profile(home, 'Library/Application Support/Google/Chrome', 'Default')
        results = c.browser_inventory(home)
        chrome = [r for r in results if r['name'] == 'Google Chrome']
        self.assertEqual(len(chrome), 1)
        self.assertEqual(len(chrome[0]['profiles']), 1)
        self.assertEqual(sorted(set(chrome[0]['profiles'][0]['indexeddb'])), ['claude.ai'])

    def test_running_browser_skips_cookie_read(self):
        home = make_home()
        self._make_profile(home, 'Library/Application Support/Google/Chrome', 'Default')
        inspected = c.inspect_chromium('Google Chrome',
                                       home / 'Library/Application Support/Google/Chrome',
                                       True)
        self.assertIn('不读数据库', inspected['profiles'][0]['cookie_note'])

    def test_missing_browsers_are_skipped(self):
        home = make_home()
        self.assertEqual(c.browser_inventory(home), [])


class HumanSizeTests(unittest.TestCase):
    def test_units(self):
        self.assertEqual(c.human_size(0), '0 B')
        self.assertEqual(c.human_size(512), '512 B')
        self.assertEqual(c.human_size(136 * 1024 * 1024), '136.0 MB')


class VMDetectionTests(unittest.TestCase):
    def test_detects_cage_and_agent_vm_markers(self):
        home = make_home()
        self.assertEqual(c.vm_environment_note(home), [])
        (home / '.lima').mkdir()
        (home / '.agent-vm').mkdir()
        self.assertEqual(c.vm_environment_note(home), ['.lima', '.agent-vm'])

    def test_files_are_not_markers(self):
        home = make_home()
        (home / '.lima').write_text('a file, not a dir')
        self.assertEqual(c.vm_environment_note(home), [])


if __name__ == '__main__':
    unittest.main()
