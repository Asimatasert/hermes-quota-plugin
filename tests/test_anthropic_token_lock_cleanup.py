"""Per-token request locks are removed by their holder on every path.

Regression: ``_request_anthropic_usage`` kept one
``quota_anthropic_shared.json.<digest>.lock`` per access token forever. OAuth
access tokens rotate every few hours, so the Hermes root collected one stale
lock file per rotation. The holder now unlinks its own lock while holding it,
and a waiter that wins the lock on an unlinked inode re-opens the path.
All synthetic: no network, no real Hermes home.
"""
import concurrent.futures
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from quota_providers import builtin as b

PAYLOAD = {'five_hour': {'utilization': 12, 'resets_at': '2026-10-14T12:00:00+00:00'}}


class Response:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, limit):
        return json.dumps(PAYLOAD).encode()


def token_locks(directory):
    return sorted(p.name for p in Path(directory).iterdir() if b._TOKEN_LOCK_RE.match(p.name))


class TokenLockCleanupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name
        patcher = mock.patch.object(b, '_backoff_state_path', return_value=str(Path(self.dir) / 'backoff.json'))
        patcher.start()
        self.addCleanup(patcher.stop)
        b._token_locks_pruned = True  # prune is tested on its own below

    def test_success_removes_the_token_lock(self):
        with mock.patch.object(b, 'urlopen_no_redirect', return_value=Response()):
            self.assertEqual(b._request_anthropic_usage('synthetic-a'), (PAYLOAD, None))
        self.assertEqual(token_locks(self.dir), [])

    def test_http_error_and_exception_paths_remove_the_token_lock(self):
        import urllib.error
        for exc in (urllib.error.HTTPError('u', 401, 'x', {}, None), urllib.error.HTTPError('u', 429, 'x', {}, None),
                    RuntimeError('boom')):
            with mock.patch.object(b, 'urlopen_no_redirect', side_effect=exc):
                payload, reason = b._request_anthropic_usage('synthetic-%s' % type(exc).__name__)
            self.assertIsNone(payload)
            self.assertTrue(reason)
        self.assertEqual(token_locks(self.dir), [])

    def test_unexpected_raise_inside_the_lock_still_removes_it(self):
        with mock.patch.object(b, '_request_anthropic_usage_locked', side_effect=KeyError('x')):
            with self.assertRaises(KeyError):
                b._request_anthropic_usage('synthetic-raise')
        self.assertEqual(token_locks(self.dir), [])

    def test_cached_and_cooldown_answers_remove_the_token_lock(self):
        b._store_shared_payload('synthetic-c', PAYLOAD)
        self.assertEqual(b._request_anthropic_usage('synthetic-c'), (PAYLOAD, None))
        b._open_anthropic_cooldown('synthetic-d')
        self.assertEqual(b._request_anthropic_usage('synthetic-d'), (None, 'rate-limited'))
        self.assertEqual(token_locks(self.dir), [])

    def test_rotating_tokens_leave_no_lock_files(self):
        with mock.patch.object(b, 'urlopen_no_redirect', return_value=Response()):
            for i in range(6):
                b._request_anthropic_usage('synthetic-rotated-%d' % i)
        self.assertEqual(token_locks(self.dir), [])
        # The two persistent coordination locks are fixed names, not per token.
        names = {p.name for p in Path(self.dir).iterdir() if p.name.endswith('.lock')}
        self.assertLessEqual(names, {b._ANTHROPIC_SHARED_FILENAME + '.lock', 'backoff.json.lock'})

    def test_contended_waiter_times_out_without_removing_the_holders_lock(self):
        import fcntl
        path = b._shared_payload_path() + '.' + b._backoff_key('synthetic-held') + '.lock'
        with open(path, 'w') as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            with mock.patch.object(b, 'urlopen_no_redirect') as network:
                self.assertEqual(b._request_anthropic_usage('synthetic-held', timeout=.1), (None, 'timeout'))
            network.assert_not_called()
            self.assertTrue(os.path.exists(path), 'a waiter must not unlink a lock it does not hold')

    def test_same_token_threads_stay_serialized_across_unlinks(self):
        inside = []
        overlap = []
        lock = threading.Lock()

        def locked(token, *, timeout):
            with lock:
                inside.append(1)
                if len(inside) > 1:
                    overlap.append(True)
            time.sleep(.03)
            with lock:
                inside.pop()
            return PAYLOAD, None

        gate = threading.Barrier(6)

        def run(_):
            gate.wait(5)
            return b._request_anthropic_usage('synthetic-same', timeout=5)

        with mock.patch.object(b, '_request_anthropic_usage_locked', side_effect=locked):
            with concurrent.futures.ThreadPoolExecutor(6) as pool:
                values = list(pool.map(run, range(6)))
        self.assertEqual(values, [(PAYLOAD, None)] * 6)
        self.assertEqual(overlap, [], 'two holders of one token lock ran concurrently')
        self.assertEqual(token_locks(self.dir), [])


class StaleLockPruneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name
        patcher = mock.patch.object(b, '_backoff_state_path', return_value=str(Path(self.dir) / 'backoff.json'))
        patcher.start()
        self.addCleanup(patcher.stop)
        b._token_locks_pruned = False
        self.addCleanup(setattr, b, '_token_locks_pruned', True)

    def _lock(self, digest, age_s):
        path = Path(self.dir) / ('quota_anthropic_shared.json.%s.lock' % digest)
        path.write_bytes(b'\0')
        stamp = time.time() - age_s
        os.utime(path, (stamp, stamp))
        return path

    def test_prunes_only_old_unheld_token_locks(self):
        import fcntl
        old = self._lock('a' * 24, 3 * 86400)
        fresh = self._lock('b' * 24, 60)
        held = self._lock('c' * 24, 3 * 86400)
        fixed = Path(self.dir) / (b._ANTHROPIC_SHARED_FILENAME + '.lock')
        fixed.write_bytes(b'\0')
        os.utime(fixed, (1, 1))
        other = Path(self.dir) / 'quota_anthropic_shared.json.not-a-digest.lock'
        other.write_bytes(b'\0')
        os.utime(other, (1, 1))
        with open(held, 'r+') as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            self.assertEqual(b._prune_stale_token_locks(), 1)
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists() and held.exists() and fixed.exists() and other.exists())

    def test_prune_runs_once_per_process(self):
        self._lock('d' * 24, 3 * 86400)
        self.assertEqual(b._prune_stale_token_locks(), 1)
        self._lock('e' * 24, 3 * 86400)
        self.assertEqual(b._prune_stale_token_locks(), 0)

    def test_prune_ignores_symlinks(self):
        target = Path(self.dir) / 'target'
        target.write_bytes(b'\0')
        link = Path(self.dir) / ('quota_anthropic_shared.json.%s.lock' % ('f' * 24))
        link.symlink_to(target)
        os.utime(link, (1, 1), follow_symlinks=False)
        self.assertEqual(b._prune_stale_token_locks(), 0)
        self.assertTrue(link.is_symlink() and target.exists())


if __name__ == '__main__':
    unittest.main()
