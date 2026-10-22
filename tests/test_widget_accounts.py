"""Accounts tab of the desktop widget, rendered offline.

Runs the real `desktop/plugin.js` through `tests/widget_monitor_renderer.cjs` against a SYNTHETIC
`hermes quota monitor accounts` payload (labels work / home / spare, never real pool data) and drives
the tab's controller the way the UI does: drag -> drop -> confirm -> the CLI argv the gateway runs,
rollback when the write fails, keyboard reorder, strategy changes, paused writes on stale data, the
not-signed-in actions, the hide toggle and the en/tr bundles.
"""
import json
import re
import time
import unittest
from pathlib import Path

from test_widget_monitor import run, quota_payload, snapshot  # noqa: E402
from widget_harness import nodes, text  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
NOW = time.time()
KEY = 'anthropic@default'


def accounts_payload(**over):
    rows = [
        {'id': 'a1a1a1', 'label': 'work', 'auth_type': 'oauth', 'source': 'manual', 'priority': 0, 'position': 0,
         'status': 'active', 'status_until': None, 'cooldown_models': [], 'tried_first': True,
         'windows': [{'label': '5h', 'used': 40, 'reset': NOW + 3600}], 'quota_match': 'label'},
        {'id': 'b2b2b2', 'label': 'home', 'auth_type': 'oauth', 'source': 'manual', 'priority': 1, 'position': 1,
         'status': 'exhausted', 'status_until': NOW + 1800, 'cooldown_models': [], 'tried_first': False,
         'windows': [], 'quota_match': None},
        {'id': 'c3c3c3', 'label': 'spare', 'auth_type': 'api_key', 'source': 'env:SYNTH', 'priority': 2,
         'position': 2, 'status': 'cooldown', 'status_until': NOW + 600, 'cooldown_models': ['claude-test-large'],
         'tried_first': False, 'windows': [], 'quota_match': None},
    ]
    data = {
        'monitor_version': 1, 'generated_at': NOW, 'active_profile': 'work', 'root_profile': 'default',
        'strategies': ['fill_first', 'round_robin', 'least_used', 'random'], 'default_strategy': 'fill_first',
        'pools': [
            {'provider': 'anthropic', 'owner': 'default', 'shared': True, 'used_by': ['default', 'work', 'home'],
             'strategies': {'default': 'fill_first', 'work': 'round_robin', 'home': 'fill_first'},
             'explicit_strategy': {'default': False, 'work': True, 'home': False}, 'rows': rows,
             'quota': {'windows': [], 'plan': None, 'unavailable': None, 'stale': False}, 'writable': True},
            {'provider': 'anthropic', 'owner': 'solo', 'shared': False, 'used_by': ['solo'],
             'strategies': {'solo': 'least_used'}, 'explicit_strategy': {'solo': True},
             'rows': [dict(rows[0], id='d4d4d4', label='solo-acc')], 'quota': None, 'writable': False},
        ],
        'profiles': [
            {'profile': 'default', 'is_root': True, 'model': {'provider': 'anthropic', 'model': 'claude-test-large'},
             'fallbacks': [{'provider': 'openrouter', 'model': 'test/other-model', 'custom_route': False}],
             'fallback_editable': False, 'fallback_command': 'hermes --profile default fallback',
             'strategies': {}, 'pools': {'anthropic': 'own'}},
            {'profile': 'work', 'is_root': False, 'model': {'provider': 'anthropic', 'model': 'claude-test-small'},
             'fallbacks': [], 'fallback_editable': False, 'fallback_command': 'hermes --profile work fallback',
             'strategies': {'anthropic': 'round_robin'}, 'pools': {'anthropic': 'shared'}},
            {'profile': 'home', 'is_root': False, 'model': {'provider': None, 'model': None}, 'fallbacks': [],
             'fallback_editable': False, 'fallback_command': 'hermes --profile home fallback',
             'strategies': {}, 'pools': {'anthropic': 'shared'}},
            {'profile': 'solo', 'is_root': False, 'model': {'provider': 'anthropic', 'model': 'claude-test-large'},
             'fallbacks': [], 'fallback_editable': False, 'fallback_command': 'hermes --profile solo fallback',
             'strategies': {'anthropic': 'least_used'}, 'pools': {'anthropic': 'own'}},
        ],
        'unavailable': [
            {'provider': 'nous', 'base': 'nous', 'reason': 'not-logged-in', 'kind': 'signin', 'retry_at': None,
             'settings_path': '/settings?tab=providers&pview=accounts', 'command': 'hermes auth add nous',
             'external_tool': None, 'optin_key': None, 'env': None},
            {'provider': 'openrouter', 'base': 'openrouter', 'reason': 'no-credentials', 'kind': 'signin',
             'retry_at': None, 'settings_path': '/settings?tab=providers&pview=keys',
             'command': 'hermes auth add openrouter', 'external_tool': None, 'optin_key': None, 'env': None},
            {'provider': 'copilot', 'base': 'copilot', 'reason': 'no-credentials', 'kind': 'signin',
             'retry_at': None, 'settings_path': None, 'command': 'gh auth login', 'external_tool': 'GitHub CLI',
             'optin_key': None, 'env': None},
            {'provider': 'grok', 'base': 'grok', 'reason': 'opt-in-disabled', 'kind': 'optin', 'retry_at': None,
             'settings_path': None, 'command': 'hermes config set plugins.entries.quota.settings.grokEnabled true',
             'external_tool': 'browser', 'optin_key': 'plugins.entries.quota.settings.grokEnabled', 'env': None},
            {'provider': 'gemini', 'base': 'gemini', 'reason': 'rate-limited', 'kind': 'rate_limited',
             'retry_at': NOW + 1500, 'settings_path': None, 'command': None, 'external_tool': 'Gemini CLI',
             'optin_key': None, 'env': None},
        ],
        'settings_paths': {}, 'capabilities': {'set_priority': True, 'set_strategy': True,
                                               'reorder_fallbacks': False, 'assign_account_to_profile': False},
        'errors': {}, 'recent_changes': [],
    }
    data.update(over)
    return data


def ok_write(state=None):
    return {'code': 0, 'output': json.dumps({'monitor_version': 1, 'changed': True, 'state': state or accounts_payload()})}


def failed_write(message, code='borrowed-pool'):
    return {'code': 1, 'output': json.dumps({'monitor_version': 1, 'error': message, 'error_code': code})}


SETUP = '''(() => {
  const saved = [];
  const ctl = createAccountsController();
  ctl.bind({data: DATA, scope: {connectionId: 'local', profile: 'work'}, flags: FLAGS, onSaved: (res, change) => saved.push(change.kind)});
  return {ctl, saved};
})()'''


def drive(steps, accounts=None, flags=None, **options):
    """Run `steps` (JS statements using `ctl`, may await) against a fresh controller; returns its outputs."""
    expr = ('(async () => { const {ctl, saved} = %s; let result = null, error = null;\n'
            'try { %s } catch (e) { error = String(e && e.message || e); }\n'
            'return {state: ctl.state, ids: ctl.ids(%r), saved, result, error}; })()'
            % (SETUP.replace('DATA', json.dumps(accounts or accounts_payload()))
               .replace('FLAGS', json.dumps(flags or {})), steps, KEY))
    return run(expr=expr, **options)


def find(tree, pred):
    return [n for n in nodes(tree) if pred(n)]


class AccountsControllerTests(unittest.TestCase):
    def test_drag_reorder_confirm_runs_the_official_write(self):
        out = drive('''
            ctl.dragStart(%r, 'c3c3c3');
            ctl.dragOver(%r, 0, false);
            result = ctl.drop(%r);
            if (JSON.stringify(ctl.ids(%r)) !== JSON.stringify(['c3c3c3','a1a1a1','b2b2b2'])) throw new Error('not optimistic');
            await ctl.confirm();''' % (KEY, KEY, KEY, KEY), cliResult=ok_write())
        self.assertIsNone(out['value']['error'])
        change = out['value']['result']
        self.assertEqual((change['id'], change['from'], change['to']), ('c3c3c3', 2, 0))
        self.assertEqual(change['affected'], ['default', 'work', 'home'])
        self.assertTrue(change['shared'])
        self.assertEqual(out['cli'], [{'route': 'work', 'method': 'cli.exec', 'argv': [
            '--profile', 'work', 'quota', 'monitor', 'set-priority', '--provider', 'anthropic',
            '--target', 'c3c3c3', '--priority', '0', '--profile', 'default', '--json']}])
        self.assertIsNone(out['value']['state']['confirm'])
        self.assertEqual(out['value']['saved'], ['priority'])
        self.assertEqual(out['value']['state']['orders'], {})  # the server state replaces the optimistic one

    def test_drop_after_last_row(self):
        out = drive('''ctl.dragStart(%r, 'a1a1a1'); ctl.dragOver(%r, 2, true); result = ctl.drop(%r);''' % (KEY, KEY, KEY))
        self.assertEqual(out['value']['result']['to'], 2)
        self.assertEqual(out['value']['ids'], ['b2b2b2', 'c3c3c3', 'a1a1a1'])
        self.assertEqual(out['value']['state']['confirm']['argv'][7:9], ['--priority', '2'])

    def test_drop_in_place_is_a_noop(self):
        out = drive('''ctl.dragStart(%r, 'b2b2b2'); ctl.dragOver(%r, 1, false); result = ctl.drop(%r);''' % (KEY, KEY, KEY))
        self.assertIsNone(out['value']['result'])
        self.assertIsNone(out['value']['state']['confirm'])
        self.assertEqual(out['cli'], [])

    def test_failed_write_rolls_back_and_keeps_the_error(self):
        out = drive('''ctl.move(%r, 'b2b2b2', -1); await ctl.confirm();''' % KEY,
                    cliResult=failed_write("profile 'work' borrows the pool of 'default'"))
        v = out['value']
        self.assertIn("borrows the pool of 'default'", v['error'])
        self.assertEqual(v['ids'], ['a1a1a1', 'b2b2b2', 'c3c3c3'])  # rolled back
        self.assertEqual(v['state']['orders'], {})
        self.assertIsNotNone(v['state']['confirm'])  # dialog stays open with the error
        self.assertIn("borrows the pool", v['state']['error'])
        self.assertFalse(v['state']['busy'])
        self.assertEqual(v['saved'], [])

    def test_retry_after_failure_then_cancel(self):
        out = drive('''ctl.move(%r, 'b2b2b2', -1);
            try { await ctl.confirm(); } catch (e) {}
            await ctl.confirm();''' % KEY, cliResults=[failed_write('boom', 'cli-failed'), ok_write()])
        self.assertIsNone(out['value']['error'])
        self.assertEqual(len(out['cli']), 2)
        self.assertEqual(out['cli'][0]['argv'], out['cli'][1]['argv'])
        out = drive('''ctl.move(%r, 'b2b2b2', -1); ctl.cancel();''' % KEY)
        self.assertEqual(out['value']['ids'], ['a1a1a1', 'b2b2b2', 'c3c3c3'])
        self.assertIsNone(out['value']['state']['confirm'])
        self.assertEqual(out['cli'], [])

    def test_keyboard_reorder(self):
        out = drive('''result = [ctl.move(%r, 'a1a1a1', -1), ctl.move(%r, 'c3c3c3', 1)];
            const second = ctl.move(%r, 'b2b2b2', -1);''' % (KEY, KEY, KEY))
        self.assertEqual(out['value']['result'], [None, None])  # out of range both ways
        self.assertEqual(out['value']['state']['confirm']['to'], 0)
        # the row's Alt+ArrowDown handler moves it through the same controller
        expr = '''(async () => {
          const {ctl} = %s;
          const pool = ctl.pool(%r);
          const tree = AccountRow({ctl, pool, row: pool.rows[0], index: 0, count: 3, firstActive: true, nowSec: Date.now() / 1000, page: false});
          let prevented = false;
          tree.props.onKeyDown({altKey: true, key: 'ArrowDown', preventDefault: () => { prevented = true; }});
          return {prevented, confirm: ctl.state.confirm, draggable: tree.props.draggable};
        })()''' % (SETUP.replace('DATA', json.dumps(accounts_payload())).replace('FLAGS', '{}'), KEY)
        out = run(expr=expr)
        self.assertTrue(out['value']['prevented'])
        self.assertTrue(out['value']['draggable'])
        self.assertEqual((out['value']['confirm']['id'], out['value']['confirm']['to']), ('a1a1a1', 1))

    def test_writes_paused_while_loading_or_stale(self):
        for flags, data in (({'isFetching': True}, accounts_payload()),
                            ({}, accounts_payload(generated_at=NOW - 3600))):
            with self.subTest(flags=flags):
                out = drive('''result = [ctl.dragStart(%r, 'a1a1a1'), ctl.move(%r, 'b2b2b2', -1),
                    ctl.proposeStrategy(ctl.pool(%r), 'home', 'random'), ctl.blocked()];''' % (KEY, KEY, KEY),
                    accounts=data, flags=flags)
                self.assertEqual(out['value']['result'][:3], [False, None, None])
                self.assertIn(out['value']['result'][3], ('loading', 'stale'))
                self.assertEqual(out['cli'], [])

    def test_unwritable_pool_cannot_be_dragged(self):
        out = drive('''result = ctl.dragStart('anthropic@solo', 'd4d4d4');''')
        self.assertFalse(out['value']['result'])

    def test_strategy_change_goes_to_one_profile(self):
        out = drive('''result = ctl.proposeStrategy(ctl.pool(%r), 'home', 'round_robin');
            if (ctl.strategyFor(ctl.pool(%r), 'home') !== 'round_robin') throw new Error('not optimistic');
            await ctl.confirm();''' % (KEY, KEY), cliResult=ok_write())
        self.assertIsNone(out['value']['error'])
        self.assertEqual(out['value']['result']['affected'], ['home'])
        self.assertEqual(out['cli'][0]['argv'], ['--profile', 'work', 'quota', 'monitor', 'set-strategy',
                                                 '--provider', 'anthropic', '--strategy', 'round_robin',
                                                 '--profile', 'home', '--json'])
        out = drive('''ctl.proposeStrategy(ctl.pool(%r), 'home', 'random'); await ctl.confirm();''' % KEY,
                    cliResult=failed_write('nope', 'cli-failed'))
        self.assertEqual(out['value']['state']['strategies'], {})
        # unknown strategy / unchanged value never opens a dialog
        out = drive('''result = [ctl.proposeStrategy(ctl.pool(%r), 'home', 'fastest'),
            ctl.proposeStrategy(ctl.pool(%r), 'work', 'round_robin')];''' % (KEY, KEY))
        self.assertEqual(out['value']['result'], [None, None])

    def test_official_command_shown_in_the_dialog(self):
        out = run(expr='''[accountsOfficialCommand({kind: 'priority', owner: 'default', provider: 'anthropic', id: 'c3c3c3', to: 0}),
                           accountsOfficialCommand({kind: 'strategy', profile: 'home', provider: 'custom:a.b', to: 'random'})]''')
        self.assertEqual(out['value'], ['hermes --profile default auth priority anthropic c3c3c3 0',
                                        'hermes --profile home config set credential_pool_strategies.custom:a\\.b random'])

    def test_old_backend_write_error(self):
        out = drive('''ctl.move(%r, 'b2b2b2', -1); await ctl.confirm();''' % KEY,
                    cliResult={'code': 2, 'output': "hermes quota monitor: error: argument monitor_command: invalid choice: 'set-priority'"})
        self.assertIn('older', out['value']['error'])


class AccountsViewTests(unittest.TestCase):
    def view(self, state=None, locale='en', data=None, page=False):
        init = 'null'
        if state:
            init = '(() => { const c = createAccountsController(); c.bind({data: __DATA__, flags: {}}); %s; return c.state; })()' % state
        expr = '''AccountsView({page: %s, initialState: %s, accountsOverride: {data: __DATA__, scope: {connectionId: 'local', profile: 'work'}, isFetching: false, isLoading: false}})''' % (
            'true' if page else 'false', init)
        return run(expr=expr.replace('__DATA__', json.dumps(data or accounts_payload())), locale=locale)

    def test_pools_rows_badges_and_shared_warning(self):
        out = self.view()
        tree = out['value']
        s = text(tree)
        for expected in ('Credential pools', 'shared · 3 profiles', 'own pool · solo', 'tried first', 'OAuth', 'API key',
                         'Shared pool of “default”: reordering also changes work, home.', 'Not signed in'):
            self.assertIn(expected, s)
        statuses = [n['props']['data-account-status'] for n in find(tree, lambda n: 'data-account-status' in n.get('props', {}))]
        self.assertEqual(statuses[:3], ['active', 'exhausted', 'cooldown'])
        rows = find(tree, lambda n: 'data-account-row' in n.get('props', {}))
        self.assertEqual([r['props']['data-account-row'] for r in rows], ['a1a1a1', 'b2b2b2', 'c3c3c3', 'd4d4d4'])
        self.assertEqual([r['props']['draggable'] for r in rows], [True, True, True, False])
        self.assertEqual(len(find(tree, lambda n: n.get('props', {}).get('data-tried-first'))), 2)
        dialog = find(tree, lambda n: n.get('type') == 'ConfirmDialog')[0]
        self.assertFalse(dialog['props']['open'])

    def test_mid_drag_shows_the_drop_line(self):
        tree = self.view(state="c.dragStart('%s', 'c3c3c3'); c.dragOver('%s', 0, false)" % (KEY, KEY))['value']
        lines = {n['props']['data-account-row']: n['props'].get('data-drop-line')
                 for n in find(tree, lambda n: 'data-account-row' in n.get('props', {}))}
        self.assertEqual(lines['a1a1a1'], 'before')
        dragged = find(tree, lambda n: n.get('props', {}).get('data-account-row') == 'c3c3c3')[0]
        self.assertEqual(dragged['props']['style']['opacity'], 0.45)

    def test_confirm_dialog_states_what_changes_and_for_whom(self):
        tree = self.view(state="c.propose('%s', 'c3c3c3', 0)" % KEY)['value']
        dialog = find(tree, lambda n: n.get('type') == 'ConfirmDialog')[0]
        self.assertTrue(dialog['props']['open'])
        self.assertEqual(dialog['props']['title'], 'Change the Anthropic account order?')
        self.assertIn('applies to all 3 profiles', dialog['props']['description'])
        body = text(dialog['props']['children'])
        for expected in ('1. work', '1. spare', 'Tried first under fill_first: spare.', 'Affects: default, work, home',
                         'hermes --profile default auth priority anthropic c3c3c3 0'):
            self.assertIn(expected, body)
        # optimistic order in the list behind the dialog
        rows = [n['props']['data-account-row'] for n in find(tree, lambda n: 'data-account-row' in n.get('props', {}))]
        self.assertEqual(rows[:3], ['c3c3c3', 'a1a1a1', 'b2b2b2'])

    def test_profile_matrix_and_fallbacks_are_read_only(self):
        tree = self.view(page=True)['value']
        table = find(tree, lambda n: n.get('props', {}).get('data-profile-matrix'))
        self.assertTrue(table)
        s = text(tree)
        self.assertIn('Fallback order is read-only here', s)
        self.assertIn('test/other-model', s)
        cmds = [n['props']['data-fallback-command'] for n in find(tree, lambda n: 'data-fallback-command' in n.get('props', {}))]
        self.assertEqual(cmds, ['hermes --profile default fallback', 'hermes --profile work fallback',
                                'hermes --profile home fallback', 'hermes --profile solo fallback'])
        self.assertIn('no per-profile account assignment', s)

    def test_not_signed_in_actions(self):
        expr = '''(async () => {
          const out = {};
          for (const item of __DATA__.unavailable) {
            const tree = UnavailableRow({item, nowSec: Date.now() / 1000});
            const flat = [];
            const walk = (n) => { if (Array.isArray(n)) n.forEach(walk); else if (n && typeof n === 'object') { flat.push(n); walk(n.props && n.props.children); } };
            walk(tree);
            const signin = flat.find(n => n.props && n.props['data-signin']);
            const copy = flat.find(n => n.props && n.props['data-copy-command']);
            if (signin) signin.props.onClick();
            if (copy) copy.props.onClick();
            out[item.provider] = {signin: signin ? signin.props.children : null, copy: copy ? copy.props['data-copy-command'] : null};
          }
          return out;
        })()'''.replace('__DATA__', json.dumps(accounts_payload()))
        out = run(expr=expr)
        v = out['value']
        self.assertEqual(v['nous'], {'signin': 'Sign in', 'copy': 'hermes auth add nous'})
        self.assertEqual(v['openrouter'], {'signin': 'Add API key', 'copy': 'hermes auth add openrouter'})
        self.assertEqual(v['copilot'], {'signin': None, 'copy': 'gh auth login'})
        self.assertEqual(v['grok'], {'signin': None, 'copy': 'hermes config set plugins.entries.quota.settings.grokEnabled true'})
        self.assertEqual(v['gemini'], {'signin': None, 'copy': None})  # rate-limited: wait, no sign-in
        self.assertEqual(out['navigated'], ['/settings?tab=providers&pview=accounts', '/settings?tab=providers&pview=keys'])
        self.assertEqual(out['clipboard'], ['hermes auth add nous', 'hermes auth add openrouter', 'gh auth login',
                                            'hermes config set plugins.entries.quota.settings.grokEnabled true'])
        s = text(self.view()['value'])
        self.assertRegex(s, r'rate-limiting quota reads\. No sign-in needed\. · retry in (24|25)m')
        self.assertIn('No API key found (no-credentials).', s)

    def test_turkish(self):
        s = text(self.view(locale='tr', state="c.propose('%s', 'c3c3c3', 0)" % KEY)['value'])
        for expected in ('Kimlik havuzları', 'paylaşılan · 3 profil', 'ilk denenen', 'Giriş yapılmamış', 'Giriş yap'):
            self.assertIn(expected, s)


class AccountsTabWiringTests(unittest.TestCase):
    def test_tab_routes_to_accounts_and_can_be_hidden(self):
        out = run(component='pane', data=quota_payload(), monitor=snapshot(), accounts=accounts_payload(),
                  storage={'paneView': 'accounts'})
        self.assertTrue(find(out['tree'], lambda n: n.get('props', {}).get('data-monitor-view') == 'accounts'))
        self.assertTrue(any(q['key'][2] == 'accounts' and q['enabled'] for q in out['queries']))
        hidden = run(component='pane', data=quota_payload(), monitor=snapshot(), accounts=accounts_payload(),
                     storage={'paneView': 'accounts', 'accountsTab': False})
        tabs = [n['props']['data-tab'] for n in find(hidden['tree'], lambda n: 'data-tab' in n.get('props', {}))]
        self.assertNotIn('accounts', tabs)
        self.assertFalse(find(hidden['tree'], lambda n: n.get('props', {}).get('data-monitor-view') == 'accounts'))
        settings = text(run(component='settings', data=quota_payload())['tree'])
        self.assertIn('Accounts tab', settings)

    def test_bundles_have_every_accounts_key(self):
        src = (ROOT / 'desktop' / 'plugin.js').read_text()
        en = src[src.index('const I18N_EN = {'):src.index('const I18N_TR = {')]
        tr = src[src.index('const I18N_TR = {'):]
        keys = set(re.findall(r'\n\t((?:accounts|strategy)[A-Za-z_]*|tab_accounts):', en))
        self.assertGreater(len(keys), 80)
        missing = [k for k in keys if not re.search(r'\n\t%s:' % re.escape(k), tr)]
        self.assertEqual(missing, [])


if __name__ == '__main__':
    unittest.main()
