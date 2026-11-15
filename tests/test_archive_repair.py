import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from resumecert import archive_repair as repair
from resumecert.archive_transfer import FIELDS, change, connect, seed
from resumecert.records import Decision


class ArchiveRepairTests(unittest.TestCase):
    def setUp(self):
        scratch = self.enterContext(TemporaryDirectory())
        self.path = Path(scratch) / "archive.sqlite3"
        self.actor = self.enterContext(closing(connect(self.path)))
        repair.create_repair_schema(self.actor)

    def prepare(self, request_id, before):
        seed(self.actor, request_id, zip(FIELDS, before))
        cert = repair.checkpoint(self.actor, request_id, 4)
        self.assertIs(cert.decision, Decision.ALLOW)

    def test_durable_checkpoint_recovers_alternate_permission_path(self):
        for request_id in (1, 2):
            self.prepare(request_id, (1, 0, 1, 1, 0))
        self.actor.close()
        with closing(connect(self.path)) as writer:
            for request_id in (1, 2):
                change(writer, request_id, ("curator_approved", "restricted_lane_open"))
        with closing(connect(self.path)) as resumed:
            self.assertEqual(repair.resume(resumed, 1, "reuse_only", 1).status, "defer")
            result = repair.resume(resumed, 2, "reuse_exact", 1)
            self.assertEqual(result.status, "committed")
            self.assertEqual(result.certificate.spent, 1)
            self.assertEqual(result.trace[0].query, "curator_approved")
            self.assertEqual(repair.resume(resumed, 2, "reuse_exact", 1).status, "already_committed")

    def test_unchanged_current_value_does_not_hide_a_committed_intermediate_change(self):
        self.prepare(1, (1, 1, 0, 0, 0))
        with closing(connect(self.path)) as writer:
            change(writer, 1, ("curator_approved",))
            change(writer, 1, ("curator_approved",))
        result = repair.resume(self.actor, 1, "reuse_only", 0)
        self.assertEqual(result.status, "defer")
        self.assertIn(("curator_approved", 0, 2), result.checked_epochs)
        self.assertNotIn(("curator_approved", 1), result.certificate.initial_facts)
        self.assertEqual(repair.resume(self.actor, 1, "reuse_exact", 1).status, "committed")

    def test_writer_excluded_between_fresh_read_and_receipt(self):
        self.prepare(1, (1, 1, 0, 0, 0))
        busy = []
        with closing(connect(self.path)) as writer:
            change(writer, 1, ("curator_approved",))
            change(writer, 1, ("curator_approved",))
            actual_read = repair.read_fields

            def intervene(*args):
                facts = actual_read(*args)
                try:
                    change(writer, 1, ("manifest_verified",))
                except sqlite3.OperationalError as error:
                    busy.append(error.sqlite_errorcode)
                return facts

            with patch.object(repair, "read_fields", side_effect=intervene):
                result = repair.resume(self.actor, 1, "reuse_exact", 1)
            self.assertEqual(result.status, "committed")
            self.assertEqual(busy, [sqlite3.SQLITE_BUSY])
            self.assertEqual(writer.execute("SELECT count(*) FROM archive_receipts").fetchone(), (1,))

    def test_failed_insert_leaves_no_receipt_and_retry_can_commit(self):
        self.prepare(1, (1, 1, 0, 0, 0))
        self.actor.executescript("""
            CREATE TRIGGER fixture_fail_receipt AFTER INSERT ON archive_receipts
            BEGIN SELECT RAISE(ABORT, 'fixture interruption'); END;
        """)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "fixture interruption"):
            repair.resume(self.actor, 1, "reuse_only", 0)
        self.assertFalse(self.actor.in_transaction)
        self.assertEqual(self.actor.execute("SELECT count(*) FROM archive_receipts").fetchone(), (0,))
        self.actor.execute("DROP TRIGGER fixture_fail_receipt")
        self.assertEqual(repair.resume(self.actor, 1, "reuse_only", 0).status, "committed")

    def test_missing_or_reused_dataset_identity_cannot_apply_old_checkpoint(self):
        self.prepare(1, (1, 1, 0, 0, 0))
        self.actor.execute("DELETE FROM datasets WHERE id = 1")
        self.assertEqual(repair.resume(self.actor, 1, "reuse_only", 0).status, "dataset_missing")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "identity cannot be reused"):
            seed(self.actor, 1, zip(FIELDS, (1, 1, 0, 0, 0)))
