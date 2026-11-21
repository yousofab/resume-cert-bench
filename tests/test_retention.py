"""Check retention cutpoints, independent restore protection, and honest unknowns."""

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

from resumecert import provider_failover as routing, retention, revocable_delivery as delivery
from resumecert.archive_transfer import connect
from resumecert.effect_delivery import DeliveryConflict, Notice, digest, encode


def message(sequence=1, stream="test-source", payload="Synthetic authorized packet"):
    return retention.StreamNotice(stream, sequence,
                                  Notice(digest(encode(["archive-notice/v1", stream, sequence])), payload))


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.provider_path = self.root / "provider.sqlite3"
        self.guard_path = self.root / "guard.sqlite3"
        self.provider = self.enterContext(closing(connect(self.provider_path)))
        self.guard = self.enterContext(closing(connect(self.guard_path)))
        retention.create_guard(self.guard)
        retention.create_provider(self.provider, "primary")
        retention.enroll_stream(self.provider, self.guard, "test-source")
        self.message = message()
        self.dispatch = retention.register(self.provider, self.guard, self.message)

    def test_prefix_reclaims_rows_and_never_asserts_no_effect(self):
        effect = retention.apply(self.provider, self.guard, self.message, self.dispatch)
        self.assertEqual(retention.lookup(self.provider, self.guard, self.message).effect, effect)
        self.assertEqual(retention.close_prefix(self.provider, self.guard, "test-source", 1), 3)
        self.assertEqual(retention.lookup(self.provider, self.guard, self.message), retention.Resolution("forgotten"))
        with self.assertRaises(retention.PrefixClosed):
            retention.register(self.provider, self.guard, self.message)
        with self.assertRaises(retention.PrefixClosed):
            retention.apply(self.provider, self.guard, self.message, self.dispatch)
        with self.assertRaises(ValueError):
            retention.close_prefix(self.provider, self.guard, "test-source", 0)
        self.assertEqual(retention.close_prefix(self.provider, self.guard, "test-source", 1), 0)
        # An unseen old operation is also closed, with an unknown historical result.
        retention.close_prefix(self.provider, self.guard, "test-source", 3)
        self.assertEqual(retention.lookup(self.provider, self.guard, message(2)).status, "forgotten")
        with self.assertRaises(retention.PrefixClosed):
            retention.register(self.provider, self.guard, message(2))

    def test_prefix_does_not_close_a_neighbour_or_another_stream(self):
        retention.enroll_stream(self.provider, self.guard, "other-source")
        for item in (message(3), message(2), message(1, "other-source")):
            attempt = retention.register(self.provider, self.guard, item)
            retention.apply(self.provider, self.guard, item, attempt)
        retention.close_prefix(self.provider, self.guard, "test-source", 1)
        for item in (message(3), message(2), message(1, "other-source")):
            self.assertEqual(retention.lookup(self.provider, self.guard, item).status, "recorded")
        with self.assertRaises(DeliveryConflict):
            replace(self.message, sequence=2)
        with self.assertRaises(DeliveryConflict):
            replace(self.message, stream_id="other-source")
        original = message(2)
        changed = replace(original, notice=replace(original.notice, payload="substituted"))
        # The old open authority continues binding the exact payload.
        with self.assertRaises(DeliveryConflict):
            retention.register(self.provider, self.guard, changed)

    def test_guard_loss_and_old_read_snapshot_stop_admission(self):
        with self.assertRaises(retention.GuardUnavailable):
            retention.apply(self.provider, None, self.message, self.dispatch)
        self.guard.execute("BEGIN")
        self.guard.execute("SELECT * FROM retention_anchors").fetchall()
        with self.assertRaises(retention.GuardUnavailable):
            retention.apply(self.provider, self.guard, self.message, self.dispatch)
        self.guard.execute("ROLLBACK")
        # A same-schema replacement with no anchor is unavailable, not a zero floor.
        with closing(sqlite3.connect(":memory:", autocommit=True)) as empty_guard:
            retention.create_guard(empty_guard)
            with self.assertRaises(retention.GuardUnavailable):
                retention.register(self.provider, empty_guard, self.message)
            with self.assertRaises(retention.GuardUnavailable):
                retention.enroll_stream(self.provider, empty_guard, "test-source")
        self.assertEqual(self.provider.execute("SELECT count(*) FROM handoff_effects").fetchone(), (0,))

    def test_sql_guards_cover_old_entry_points_and_require_configuration(self):
        with closing(connect(self.provider_path)) as bare:
            with self.assertRaisesRegex(sqlite3.OperationalError, "retention_guard_floor"):
                delivery.apply(bare, self.message.notice, self.dispatch.epoch)
        retention.close_prefix(self.provider, self.guard, "test-source", 1)
        with self.assertRaises(sqlite3.IntegrityError):
            delivery.register(self.provider, self.message.notice)
        with self.assertRaises(sqlite3.IntegrityError):
            self.guard.execute("UPDATE retention_anchors SET closed_through = 0")
        with self.assertRaises(sqlite3.IntegrityError):
            self.guard.execute("DELETE FROM retention_anchors")
        with self.assertRaises(sqlite3.IntegrityError):
            self.provider.execute("UPDATE retention_streams SET closed_through = 0")

    def test_implicit_wal_read_snapshot_cannot_hide_a_new_closure(self):
        self.guard.execute("PRAGMA journal_mode = WAL")
        retention.enroll_stream(self.provider, self.guard, "other-source")
        pinned = self.guard.execute("SELECT * FROM retention_anchors ORDER BY stream_id")
        pinned.fetchone()  # Leave another row pending, holding an implicit read snapshot.
        self.assertFalse(self.guard.in_transaction)
        with closing(connect(self.guard_path)) as writer:
            retention.close_prefix(self.provider, writer, "test-source", 1)
        with self.assertRaises(retention.PrefixClosed):
            retention.apply(self.provider, self.guard, self.message, self.dispatch)
        pinned.close()

    def test_real_exit_after_anchor_commit_preserves_closure_without_local_cleanup(self):
        retention.apply(self.provider, self.guard, self.message, self.dispatch)
        child_code = """
import os
import sys
from resumecert.archive_transfer import connect
from resumecert.retention import close_prefix
p, g = map(connect, sys.argv[1:])
p.create_function('fixture_exit', 0, lambda: os._exit(31))
p.executescript('''CREATE TRIGGER fixture_exit_mid_cleanup BEFORE UPDATE ON retention_streams
BEGIN SELECT fixture_exit(); END;''')
close_prefix(p, g, 'test-source', 1)
"""
        child = subprocess.run([sys.executable, "-c", child_code, str(self.provider_path), str(self.guard_path)],
                               cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=10)
        self.assertEqual(child.returncode, 31, child.stderr.decode())
        self.assertEqual(self.provider.execute("SELECT closed_through FROM retention_streams").fetchone(), (0,))
        self.assertEqual(self.provider.execute("SELECT count(*) FROM handoff_effects").fetchone(), (1,))
        self.assertEqual(retention.closed_through(self.provider, self.guard, "test-source"), 1)
        with self.assertRaises(retention.PrefixClosed):
            retention.apply(self.provider, self.guard, self.message, self.dispatch)
        # Even the original API must read the independently committed closure at INSERT.
        self.provider.execute("DELETE FROM handoff_effects")
        with self.assertRaises(sqlite3.IntegrityError):
            delivery.apply(self.provider, self.message.notice, self.dispatch.epoch)
        self.provider.execute("DROP TRIGGER fixture_exit_mid_cleanup")
        self.assertEqual(retention.close_prefix(self.provider, self.guard, "test-source", 1), 2)

    def test_restoring_old_provider_image_cannot_restore_closed_authority(self):
        backup_path = self.root / "old.sqlite3"
        with closing(connect(backup_path)) as old:
            self.provider.backup(old)
        routing.retire(self.provider, self.message.notice, self.dispatch.epoch)
        retention.close_prefix(self.provider, self.guard, "test-source", 1)
        # Restore offline to another path. Only this image serves requests afterwards.
        restored_path = self.root / "restored.sqlite3"
        shutil.copyfile(backup_path, restored_path)
        with closing(connect(restored_path)) as restored:
            self.assertEqual(delivery.authority(restored, self.message.notice), (1, True))
            with self.assertRaises(retention.PrefixClosed):
                retention.apply(restored, self.guard, self.message, self.dispatch)
            self.assertEqual(retention.lookup(restored, self.guard, self.message).status, "forgotten")

    def test_apply_racing_closure_never_inserts_after_closure(self):
        ready = Barrier(2)

        def worker(close):
            with closing(sqlite3.connect(self.provider_path, autocommit=True, timeout=5)) as p, closing(
                    sqlite3.connect(self.guard_path, autocommit=True, timeout=5)) as g:
                ready.wait(timeout=5)
                if close:
                    return retention.close_prefix(p, g, "test-source", 1)
                try:
                    return retention.apply(p, g, self.message, self.dispatch)
                except retention.PrefixClosed:
                    return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            apply_future = pool.submit(worker, False)
            close_future = pool.submit(worker, True)
            applied = apply_future.result(timeout=10)
            removed = close_future.result(timeout=10)
        self.assertEqual(removed, 3 if applied else 2)
        self.assertEqual(self.provider.execute("SELECT count(*) FROM handoff_effects").fetchone(), (0,))
        with self.assertRaises(retention.PrefixClosed):
            retention.apply(self.provider, self.guard, self.message, self.dispatch)

    def test_many_operation_rows_compact_to_one_boundary_per_stream(self):
        for sequence in range(1, 257):
            item = message(sequence)
            attempt = retention.register(self.provider, self.guard, item)
            retention.apply(self.provider, self.guard, item, attempt)
        self.assertEqual(retention.close_prefix(self.provider, self.guard, "test-source", 256), 768)
        self.assertEqual(self.provider.execute("SELECT count(*) FROM retention_bindings").fetchone(), (0,))
        self.assertEqual(self.provider.execute("SELECT * FROM retention_streams").fetchone(), ("test-source", 256))
        self.assertEqual(self.guard.execute("SELECT * FROM retention_anchors").fetchone(), ("primary", "test-source", 256))


if __name__ == "__main__":
    unittest.main()
