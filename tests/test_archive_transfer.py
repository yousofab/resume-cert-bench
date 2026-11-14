import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from resumecert import archive_transfer as transfer


class ArchiveTransferTests(unittest.TestCase):
    def test_atomic_guard_holds_writer_lock_through_receipt_commit(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / "archive.sqlite3"
            with closing(transfer.connect(path)) as actor, closing(transfer.connect(path)) as writer:
                transfer.create_schema(actor)
                transfer.seed(actor, 1, zip(transfer.FIELDS, (1, 1, 0, 0, 0)))
                grant = transfer.authorize(actor, 1, 4)
                reader = transfer.read_fields
                lock_errors = []

                def intervene(*args):
                    observed = reader(*args)
                    try:
                        transfer.change(writer, 1, ("curator_approved",))
                    except sqlite3.OperationalError as error:
                        lock_errors.append(error.sqlite_errorcode)
                    return observed

                with patch.object(transfer, "read_fields", side_effect=intervene):
                    result = transfer.archive(actor, 1, grant, "sufficient_evidence")
                self.assertEqual(result.status, "committed")
                self.assertEqual(lock_errors, [sqlite3.SQLITE_BUSY])
                transfer.change(writer, 1, ("curator_approved",))
                self.assertEqual(transfer.archive(actor, 1, grant, "sufficient_evidence").status,
                                 "already_committed")
                self.assertEqual(writer.execute("SELECT request_id FROM archive_receipts").fetchall(),
                                 [(1,)])

    def test_guard_is_scoped_and_can_reject_without_writing(self):
        with TemporaryDirectory() as temp, closing(transfer.connect(Path(temp) / "archive.sqlite3")) as actor:
            transfer.create_schema(actor)
            for request_id in (1, 2):
                transfer.seed(actor, request_id, zip(transfer.FIELDS, (1, 1, 0, 0, 0)))
            grant = transfer.authorize(actor, 1, 4)
            forged = replace(grant, certificate=replace(grant.certificate, spent=99))
            self.assertEqual(transfer.archive(actor, 1, forged, "sufficient_evidence").status,
                             "invalid_certificate")
            self.assertEqual(transfer.archive(actor, 2, grant, "sufficient_evidence").status,
                             "invalid_certificate")
            transfer.change(actor, 1, ("curator_approved",))
            self.assertEqual(transfer.archive(actor, 1, grant, "sufficient_evidence").status,
                             "guard_rejected")
            self.assertEqual(actor.execute("SELECT count(*) FROM archive_receipts").fetchone(), (0,))

    def test_minimal_subproofs_keep_both_acquired_paths(self):
        fields = transfer.FIELDS
        before = tuple(zip(fields, (1, 1, 1, 1, 1)))
        proofs = transfer.sufficient_subproofs(transfer.CONTRACT, before)
        self.assertEqual(proofs, (before[:2], (before[0], *before[2:4])))
        self.assertEqual(transfer.sufficient_subproofs(transfer.CONTRACT, before[:1]), ())
