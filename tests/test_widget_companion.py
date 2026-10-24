"""Settings ▸ "Notch island (macOS)" card: shown on Mac renderers only, driven by
`hermes quota monitor companion --json` (synthetic payloads), every language complete.

Run from the repo root:  python -m unittest tests.test_widget_companion
"""
import json
import unittest

from test_widget_i18n import LOCALES, strings
from test_widget_monitor import find, run

MAC = {'platform': 'MacIntel', 'userAgent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'}
LINUX = {'platform': 'Linux x86_64', 'userAgent': 'Mozilla/5.0 (X11; Linux x86_64)'}
CMD = "'/synthetic/plugins/quota/macos/install.sh'"


def payload(**over):
    p = {'monitor_version': 1, 'generated_at': 1.0, 'platform': 'darwin', 'supported': True, 'installed': False,
         'app_path': None, 'running': False, 'notch': None, 'status': None, 'install_script': CMD.strip("'"),
         'install_command': CMD, 'uninstall_command': None, 'repo_url': 'https://github.com/rarf/hermes-quota-plugin'}
    p.update(over)
    return p


def settings(**options):
    return run(component='settings', **options)


def card(tree):
    found = find(tree, lambda n: isinstance(n, dict) and (n.get('props') or {}).get('data-settings-card') in
                 {'Notch island (macOS)', 'Çentik adası (macOS)'})
    return found[0] if found else None


def text(tree):
    return ' '.join(strings(tree))


class NotchCardTest(unittest.TestCase):
    def test_hidden_without_mac_renderer_and_never_queried(self):
        for nav in (None, LINUX):
            with self.subTest(nav=nav):
                out = settings(navigator=nav, companion=payload()) if nav else settings(companion=payload())
                self.assertIsNone(card(out['tree']))
                q = [x for x in out['queries'] if x['key'][:3] == ['quota', 'monitor', 'companion']]
                self.assertTrue(all(not x['enabled'] for x in q), q)

    def test_not_installed_shows_install_command_and_copies_it(self):
        out = settings(navigator=MAC, companion=payload())
        c = card(out['tree'])
        self.assertIsNotNone(c)
        self.assertIn('Not installed', text(c))
        self.assertIn('Start it once', text(c))
        code = find(c, lambda n: isinstance(n, dict) and (n.get('props') or {}).get('data-notch-install'))
        self.assertEqual(code[0]['props']['data-notch-install'], CMD)
        self.assertFalse(find(c, lambda n: isinstance(n, dict) and (n.get('props') or {}).get('data-notch-open')),
                         'no Open button before install')
        q = [x for x in out['queries'] if x['key'][:3] == ['quota', 'monitor', 'companion']]
        self.assertTrue(q and q[0]['enabled'])
        clicked = run(expr='''(async () => { await copyCommand(%s); return true; })()''' % json.dumps(CMD),
                      navigator=MAC)
        self.assertEqual(clicked['clipboard'], [CMD])

    def test_running_with_and_without_notch(self):
        on = card(settings(navigator=MAC, companion=payload(installed=True, running=True, notch=True,
                                                            app_path='/synthetic/Applications/Quota Notch.app'))['tree'])
        self.assertIn('Running', text(on))
        self.assertIn('has a notch', text(on))
        self.assertTrue(find(on, lambda n: isinstance(n, dict) and (n.get('props') or {}).get('data-notch-open')))
        self.assertFalse(find(on, lambda n: isinstance(n, dict) and (n.get('props') or {}).get('data-notch-install')))
        off = card(settings(navigator=MAC, companion=payload(installed=True, running=False, notch=False))['tree'])
        self.assertIn('Installed, not running', text(off))
        self.assertIn('runs in the menu bar instead', text(off))

    def test_remote_backend_and_old_backend(self):
        remote = card(settings(navigator=MAC, companion=payload(platform='linux', supported=False))['tree'])
        self.assertIn("backend runs on linux", text(remote))
        old = card(settings(navigator=MAC, companion={'unsupported': True})['tree'])
        self.assertIn('Update the quota plugin backend', text(old))

    def test_open_button_runs_the_fixed_cli_argv(self):
        result = {'code': 0, 'output': json.dumps(payload(installed=True, running=True, opened=True))}
        out = run(expr='openCompanion(host.state.focusedSessionOwner.get(), null)', navigator=MAC, cliResult=result)
        self.assertTrue(out['value'])
        self.assertEqual(out['cli'][-1]['argv'], ['--profile', 'work', 'quota', 'monitor', 'companion', '--open', '--json'])
        failed = run(expr='openCompanion(host.state.focusedSessionOwner.get(), null)', navigator=MAC,
                     cliResult={'code': 1, 'output': json.dumps({'monitor_version': 1, 'error': 'x', 'error_code': 'companion-not-installed'})})
        self.assertFalse(failed['value'])
        self.assertEqual(failed['toasts'][-1]['kind'], 'warning')

    def test_every_language_renders_the_card_without_raw_keys(self):
        for locale in LOCALES:
            with self.subTest(locale=locale):
                for p in (payload(), payload(installed=True, running=True, notch=False), payload(platform='linux', supported=False)):
                    out = settings(navigator=MAC, companion=p, locale=locale)
                    tree = out['tree']
                    cards = find(tree, lambda n: isinstance(n, dict) and 'data-settings-card' in (n.get('props') or {}))
                    notch = [c for c in cards if any(isinstance(s, str) and 'notch' in s.lower() for s in strings(c))
                             or c['props']['data-settings-card'].endswith('(macOS)') or c['props']['data-settings-card'].endswith('（macOS）')]
                    self.assertTrue(notch, 'card missing in %s' % locale)
                    body = text(notch[-1])
                    self.assertNotRegex(body, r'\bnotch[A-Z]\w+', 'raw i18n key rendered in %s' % locale)


if __name__ == '__main__':
    unittest.main()
