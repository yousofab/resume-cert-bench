"""Exercise independent claim cutpoints and provider-qualified recovery evidence."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest

from resumecert import open_tail as tail, provider_failover as routing, retention, revocable_delivery as delivery
from resumecert.archive_transfer import connect
from resumecert.effect_delivery import DeliveryConflict, Notice, digest, encode


def message(sequence=1):
    return retention.StreamNotice("test-source", sequence,
                                  Notice(digest(encode(["archive-notice/v1", "test-source", sequence])),
                                         "Synthetic authorized handoff"))


class OpenTailTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.provider_path = self.root / "provider.sqlite3"
        self.guard_path = self.root / "guard.sqlite3"
        self.provider = self.enterContext(closing(connect(self.provider_path)))
        self.guard = self.enterContext(closing(connect(self.guard_path)))
        tail.create_guard(self.guard)
        tail.create_provider(self.provider, "primary")
        retention.enroll_stream(self.provider, self.guard, "test-source")
        self.message = message()
        self.dispatch = retention.register(self.provider, self.guard, self.message)

    def backup(self):
        path = self.root / "before-effect.sqlite3"
        with closing(connect(path)) as saved:
            self.provider.backup(saved)
        return path

    def child_exit(self, mode):
        child_code = """
import os
import sys
from resumecert import open_tail as tail, retention, provider_failover as routing
from resumecert.archive_transfer import connect
from resumecert.effect_delivery import Notice
p, g = map(connect, sys.argv[1:3])
key, payload, mode = sys.argv[3:]
m = retention.StreamNotice('test-source', 1, Notice(key, payload))
tail.configure(p, g)
if mode == 'before_commit':
    p.create_function('fixture_exit', 0, lambda: os._exit(72))
    p.executescript('''CREATE TRIGGER fixture_exit_before_commit AFTER INSERT ON handoff_effects
    BEGIN SELECT fixture_exit(); END;''')
retention.apply(p, g, m, routing.Dispatch('primary', m.notice, 1))
os._exit(71)
"""
        child = subprocess.run([sys.executable, "-c", child_code, str(self.provider_path), str(self.guard_path),
                                self.message.notice.operation_key, self.message.notice.payload, mode],
                               cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=10)
        self.assertEqual(child.returncode, 72 if mode == "before_commit" else 71, child.stderr.decode())

    def test_sealed_result_survives_provider_restore_without_another_insert(self):
        old = self.backup()
        first = tail.apply(self.provider, self.guard, self.message, self.dispatch)
        restored_path = self.root / "restored.sqlite3"
        shutil.copyfile(old, restored_path)
        with closing(connect(restored_path)) as restored:
            self.assertEqual(tail.apply(restored, self.guard, self.message, self.dispatch), first)
            self.assertEqual(tail.recover(restored, self.guard, self.message), first)
            self.assertEqual(restored.execute("SELECT count(*) FROM handoff_effects").fetchone(), (0,))

    def test_real_exit_after_commit_recovers_from_the_retained_positive_receipt(self):
        self.child_exit("after_commit")
        self.assertEqual(self.guard.execute("SELECT effect_id FROM open_tail_claims").fetchone(), (None,))
        recovered = tail.recover(self.provider, self.guard, self.message)
        self.assertEqual(recovered.status, "recorded")
        self.assertEqual(self.provider.execute("SELECT count(*) FROM handoff_effects").fetchone(), (1,))
        self.assertEqual(self.guard.execute("SELECT effect_id FROM open_tail_claims").fetchone(), (1,))

    def test_real_exit_after_commit_then_restore_leaves_an_unknown_effect(self):
        old = self.backup()
        self.child_exit("after_commit")
        restored_path = self.root / "restored.sqlite3"
        shutil.copyfile(old, restored_path)
        with closing(connect(restored_path)) as restored:
            self.assertEqual(tail.apply(restored, self.guard, self.message, self.dispatch).status, "unknown")
            self.assertEqual(tail.recover(restored, self.guard, self.message).status, "unknown")
            self.assertEqual(restored.execute("SELECT count(*) FROM handoff_effects").fetchone(), (0,))
        # The independent claim cannot establish that this committed effect was absent.
        self.assertEqual(self.provider.execute("SELECT count(*) FROM handoff_effects").fetchone(), (1,))

    def test_real_exit_before_commit_leaves_an_unknown_without_an_effect(self):
        self.child_exit("before_commit")
        self.provider.execute("DROP TRIGGER fixture_exit_before_commit")
        self.assertEqual(self.provider.execute("SELECT count(*) FROM handoff_effects").fetchone(), (0,))
        self.assertEqual(tail.apply(self.provider, self.guard, self.message, self.dispatch).status, "unknown")
        self.assertEqual(self.guard.execute("SELECT effect_id FROM open_tail_claims").fetchone(), (None,))
        next_message = message(2)
        attempt = retention.register(self.provider, self.guard, next_message)
        self.assertEqual(tail.apply(self.provider, self.guard, next_message, attempt).status, "recorded")

    def test_two_restored_images_racing_share_only_one_new_effect_claim(self):
        old = self.backup()
        clone = self.root / "clone.sqlite3"
        shutil.copyfile(old, clone)
        ready = Barrier(2)

        def attempt(path):
            with closing(sqlite3.connect(path, autocommit=True, timeout=5)) as provider, closing(
                    sqlite3.connect(self.guard_path, autocommit=True, timeout=5)) as guard:
                ready.wait(timeout=5)
                return tail.apply(provider, guard, self.message, self.dispatch)

        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(attempt, path) for path in (self.provider_path, clone)]
            outcomes = [job.result(timeout=10) for job in jobs]
        self.assertTrue(all(outcome.status in ("recorded", "unknown") for outcome in outcomes))
        with closing(connect(clone)) as second:
            total = sum(p.execute("SELECT count(*) FROM handoff_effects").fetchone()[0]
                        for p in (self.provider, second))
        self.assertEqual(total, 1)
        self.assertEqual(self.guard.execute("SELECT count(*) FROM open_tail_claims").fetchone(), (1,))

    def test_raw_old_adapter_cannot_replay_an_effect_after_restore(self):
        old = self.backup()
        tail.apply(self.provider, self.guard, self.message, self.dispatch)
        clone = self.root / "clone.sqlite3"
        shutil.copyfile(old, clone)
        with closing(connect(clone)) as restored:
            with self.assertRaises(sqlite3.OperationalError):
                delivery.apply(restored, self.message.notice, 1)
            tail.configure(restored, self.guard)
            with self.assertRaisesRegex(sqlite3.IntegrityError, "claim already exists"):
                delivery.apply(restored, self.message.notice, 1)
            self.assertEqual(restored.execute("SELECT count(*) FROM handoff_effects").fetchone(), (0,))

    def test_failed_sealing_does_not_erase_a_committed_target_or_its_claim(self):
        self.guard.executescript("""
            CREATE TRIGGER fixture_fail_seal BEFORE UPDATE ON open_tail_claims
            BEGIN SELECT RAISE(ABORT, 'seal failed'); END;
        """)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "seal failed"):
            tail.apply(self.provider, self.guard, self.message, self.dispatch)
        self.assertEqual(self.provider.execute("SELECT count(*) FROM handoff_effects").fetchone(), (1,))
        self.assertEqual(self.guard.execute("SELECT effect_id FROM open_tail_claims").fetchone(), (None,))
        self.guard.execute("DROP TRIGGER fixture_fail_seal")
        self.assertEqual(tail.recover(self.provider, self.guard, self.message).status, "recorded")

    def test_failed_independent_claim_write_cannot_create_a_target_effect(self):
        self.guard.executescript("""
            CREATE TRIGGER fixture_fail_claim BEFORE INSERT ON open_tail_claims
            BEGIN SELECT RAISE(ABORT, 'claim storage failed'); END;
        """)
        with self.assertRaises(sqlite3.OperationalError):
            tail.apply(self.provider, self.guard, self.message, self.dispatch)
        self.assertEqual(self.provider.execute("SELECT count(*) FROM handoff_effects").fetchone(), (0,))
        self.assertEqual(self.guard.execute("SELECT count(*) FROM open_tail_claims").fetchone(), (0,))
        self.assertEqual(tail.recover(self.provider, self.guard, self.message).status, "unattempted")
        self.guard.execute("DROP TRIGGER fixture_fail_claim")
        self.assertEqual(tail.apply(self.provider, self.guard, self.message, self.dispatch).status, "recorded")

    def test_claim_and_cached_receipt_bind_exact_payload_provider_and_sequence(self):
        tail.apply(self.provider, self.guard, self.message, self.dispatch)
        changed = replace(self.message, notice=replace(self.message.notice, payload="substituted"))
        with self.assertRaises(DeliveryConflict):
            tail.recover(self.provider, self.guard, changed)
        with self.assertRaises(DeliveryConflict):
            tail.apply(self.provider, self.guard, self.message, replace(self.dispatch, provider_id="secondary"))
        with self.assertRaises(DeliveryConflict):
            replace(self.message, sequence=2)
        with self.assertRaises(sqlite3.IntegrityError):
            self.guard.execute("UPDATE open_tail_claims SET effect_id = 9")
        with self.assertRaises(sqlite3.IntegrityError):
            self.guard.execute("DELETE FROM open_tail_claims")
        with self.assertRaises(retention.GuardUnavailable):
            tail.apply(self.provider, None, self.message, self.dispatch)

    def test_closed_prefix_can_reclaim_claims_without_resurrecting_old_requests(self):
        old = self.backup()
        tail.apply(self.provider, self.guard, self.message, self.dispatch)
        next_message = message(2)
        next_attempt = retention.register(self.provider, self.guard, next_message)
        tail.apply(self.provider, self.guard, next_message, next_attempt)
        self.assertEqual(tail.close_prefix(self.provider, self.guard, "test-source", 1), 4)
        self.assertEqual(self.guard.execute("SELECT sequence FROM open_tail_claims").fetchall(), [(2,)])
        self.assertEqual(tail.recover(self.provider, self.guard, self.message).status, "forgotten")
        restored_path = self.root / "restored.sqlite3"
        shutil.copyfile(old, restored_path)
        with closing(connect(restored_path)) as restored:
            with self.assertRaises(retention.PrefixClosed):
                tail.apply(restored, self.guard, self.message, self.dispatch)

    def test_old_implicit_wal_snapshot_cannot_hide_a_recorded_independent_claim(self):
        old = self.backup()
        self.guard.execute("PRAGMA journal_mode = WAL")
        retention.enroll_stream(self.provider, self.guard, "other-source")
        pinned = self.guard.execute("SELECT * FROM retention_anchors ORDER BY stream_id")
        pinned.fetchone()
        self.assertFalse(self.guard.in_transaction)
        with closing(connect(self.guard_path)) as writer:
            known = tail.apply(self.provider, writer, self.message, self.dispatch)
        self.assertIsNone(self.guard.execute("SELECT effect_id FROM open_tail_claims").fetchone())
        restored_path = self.root / "restored.sqlite3"
        shutil.copyfile(old, restored_path)
        with closing(connect(restored_path)) as restored:
            self.assertEqual(tail.recover(restored, self.guard, self.message), known)
            self.assertEqual(restored.execute("SELECT count(*) FROM handoff_effects").fetchone(), (0,))
        pinned.close()


if __name__ == "__main__":
    unittest.main()
