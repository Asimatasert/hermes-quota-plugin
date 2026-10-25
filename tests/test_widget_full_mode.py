"""Widget side of ``fullMode``: account writes and budget sync open ONLY for a backend payload that carries
``full_mode: true``; capability flags alone (the upstream trial contract) keep everything read-only.

Synthetic payloads (labels work / home / spare) through the offline renderer; the CLI is the harness's fake.
"""
import json
import unittest

from test_widget_accounts import KEY, accounts_payload, drive, find  # noqa: E402
from test_widget_monitor import run  # noqa: E402
from widget_harness import text  # noqa: E402


def full_payload(**over):
    return accounts_payload(full_mode=True, **over)


def ok_write():
    return {'code': 0, 'output': json.dumps({'monitor_version': 1, 'changed': True, 'state': full_payload()})}


def failed_write(message, code='borrowed-pool'):
    return {'code': 1, 'output': json.dumps({'monitor_version': 1, 'error': message, 'error_code': code})}


class FullModeControllerTests(unittest.TestCase):
    def test_drag_reorder_confirm_runs_the_official_write(self):
        out = drive('''
            ctl.dragStart(%r, 'c3c3c3');
            ctl.dragOver(%r, 0, false);
            result = ctl.drop(%r);
            if (JSON.stringify(ctl.ids(%r)) !== JSON.stringify(['c3c3c3','a1a1a1','b2b2b2'])) throw new Error('not optimistic');
            await ctl.confirm();''' % (KEY, KEY, KEY, KEY), accounts=full_payload(), cliResult=ok_write())
        self.assertIsNone(out['value']['error'])
        change = out['value']['result']
        self.assertEqual((change['id'], change['from'], change['to']), ('c3c3c3', 2, 0))
        self.assertEqual(out['cli'], [{'route': 'work', 'method': 'cli.exec', 'argv': [
            '--profile', 'work', 'quota', 'monitor', 'set-priority', '--provider', 'anthropic',
            '--target', 'c3c3c3', '--priority', '0', '--profile', 'default', '--json']}])
        self.assertEqual(out['value']['saved'], ['priority'])
        self.assertIsNone(out['value']['state']['confirm'])

    def test_failed_write_rolls_back_and_keeps_the_dialog(self):
        out = drive('''ctl.move(%r, 'b2b2b2', -1); await ctl.confirm();''' % KEY, accounts=full_payload(),
                    cliResult=failed_write("profile 'work' borrows the pool of 'default'"))
        v = out['value']
        self.assertIn('borrows the pool', v['error'])
        self.assertEqual(v['ids'], ['a1a1a1', 'b2b2b2', 'c3c3c3'])
        self.assertIsNotNone(v['state']['confirm'])
        self.assertEqual(v['saved'], [])

    def test_strategy_change_goes_to_one_profile(self):
        out = drive('''result = ctl.proposeStrategy(ctl.pool(%r), 'home', 'round_robin'); await ctl.confirm();''' % KEY,
                    accounts=full_payload(), cliResult=ok_write())
        self.assertIsNone(out['value']['error'])
        self.assertEqual(out['value']['result']['affected'], ['home'])
        self.assertEqual(out['cli'][0]['argv'], ['--profile', 'work', 'quota', 'monitor', 'set-strategy',
                                                 '--provider', 'anthropic', '--strategy', 'round_robin',
                                                 '--profile', 'home', '--json'])

    def test_writes_still_pause_on_stale_data_and_unwritable_pools(self):
        out = drive('''result = [ctl.dragStart(%r, 'a1a1a1'), ctl.blocked()];''' % KEY,
                    accounts=full_payload(generated_at=0))
        self.assertEqual(out['value']['result'], [False, 'stale'])
        out = drive('''result = ctl.dragStart('anthropic@solo', 'd4d4d4');''', accounts=full_payload())
        self.assertFalse(out['value']['result'])
        self.assertEqual(out['cli'], [])

    def test_full_mode_needs_the_capability_flag_too(self):
        data = full_payload(capabilities={'set_priority': False, 'set_strategy': False})
        out = drive('''result = [ctl.canReorder(%r), ctl.proposeStrategy(ctl.pool(%r), 'home', 'random')];''' % (KEY, KEY),
                    accounts=data)
        self.assertEqual(out['value']['result'], [False, None])

    def test_non_literal_full_mode_stays_read_only(self):
        for value in ('true', 1, None):
            with self.subTest(value=value):
                out = drive('''result = ctl.canReorder(%r);''' % KEY, accounts=accounts_payload(full_mode=value))
                self.assertFalse(out['value']['result'])


class FullModeViewTests(unittest.TestCase):
    def view(self, data):
        expr = '''AccountsView({page: false, initialState: null, accountsOverride: {data: __DATA__, scope: {connectionId: 'local', profile: 'work'}, isFetching: false, isLoading: false}})'''
        return run(expr=expr.replace('__DATA__', json.dumps(data)))['value']

    def test_rows_are_draggable_with_the_edit_hints(self):
        tree = self.view(full_payload())
        rows = find(tree, lambda n: 'data-account-row' in n.get('props', {}))
        self.assertEqual([r['props']['draggable'] for r in rows], [True, True, True, False])
        s = text(tree)
        self.assertIn('Drag a row, or use ▲▼ / Alt+↑↓, to change which account is tried first.', s)
        self.assertIn("Changes run Hermes' own CLI after you confirm.", s)
        self.assertNotIn('Read-only: account order cannot be changed', s)

    def test_without_full_mode_the_view_is_the_upstream_read_only_one(self):
        s = text(self.view(accounts_payload()))
        self.assertIn('Read-only: account order cannot be changed from this widget.', s)
        self.assertNotIn('Drag a row', s)


class FullModeBudgetSyncTests(unittest.TestCase):
    def sync(self, payload):
        expr = '''(async () => {
            const scope = normalizeQuotaScope(host.state.focusedSessionOwner.get());
            if (__PAYLOAD__) noteBackendMode(scope, __PAYLOAD__);
            return await syncBudgetToBackend();
        })()'''.replace('__PAYLOAD__', json.dumps(payload))
        return run(expr=expr, storage={'budgetDaily': 5, 'budgetMonthly': 50})

    def test_budget_syncs_only_to_a_full_mode_backend(self):
        off = self.sync(None)
        self.assertFalse(off['value'])
        self.assertEqual(off['cli'], [])
        trial = self.sync({'monitor_version': 1})
        self.assertFalse(trial['value'])
        self.assertEqual(trial['cli'], [])
        on = self.sync({'monitor_version': 1, 'full_mode': True})
        self.assertTrue(on['value'])
        self.assertEqual(on['cli'][0]['argv'][-8:], ['quota', 'monitor', 'set-budget', '--daily', '5',
                                                     '--monthly', '50', '--json'])


if __name__ == '__main__':
    unittest.main()
