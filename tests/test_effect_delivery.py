import sqlite3
import subprocess
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier

from resumecert.archive_repair import checkpoint, create_repair_schema, resume
from resumecert.archive_transfer import FIELDS, change, connect, seed
from resumecert.effect_delivery import (
    DeliveryConflict, acknowledge, create_inbox_schema, create_source_schema, deliver_once,
    pending_notice, prepare_notice, receive,
)


class EffectDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.source_path = self.root / "source.sqlite3"
        self.sink_path = self.root / "sink.sqlite3"
        self.source = self.enterContext(closing(connect(self.source_path)))
        self.sink = self.enterContext(closing(connect(self.sink_path)))
        create_repair_schema(self.source)
        create_source_schema(self.source)
        create_inbox_schema(self.sink)
        seed(self.source, 1, zip(FIELDS, (1, 1, 0, 0, 0)))
        checkpoint(self.source, 1, 4)

    def authorize(self):
        notice = prepare_notice(self.source, "fixture", 1, "Archive receipt ready")
        result = resume(self.source, 1, "reuse_only", 0)
        self.assertEqual(result.status, "committed")
        self.assertEqual(pending_notice(self.source, 1), notice)
        return notice

    def test_revoked_permission_never_releases_prepared_notice(self):
        prepare_notice(self.source, "fixture", 1, "Archive receipt ready")
        with closing(connect(self.source_path)) as writer:
            change(writer, 1, ("manifest_verified",))
        result = resume(self.source, 1, "reuse_exact", 4)
        self.assertEqual(result.status, "block")
        self.assertIsNone(deliver_once(self.source, 1, lambda n: receive(self.sink, n)))
        self.assertEqual(self.source.execute("SELECT status FROM notice_outbox").fetchone(),
                         ("prepared",))
        self.assertEqual(self.sink.execute("SELECT count(*) FROM inbox_effects").fetchone(), (0,))

    def test_receipt_and_outbox_activation_roll_back_together(self):
        with self.assertRaisesRegex(sqlite3.IntegrityError, "notice must be prepared"):
            resume(self.source, 1, "reuse_only", 0)
        self.assertEqual(self.source.execute("SELECT count(*) FROM archive_receipts").fetchone(), (0,))
        prepare_notice(self.source, "fixture", 1, "Archive receipt ready")
        self.source.executescript("""
            CREATE TRIGGER test_abort_notice AFTER UPDATE ON notice_outbox
            BEGIN SELECT RAISE(ABORT, 'fixture failure'); END;
        """)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "fixture failure"):
            resume(self.source, 1, "reuse_only", 0)
        self.assertIsNone(pending_notice(self.source, 1))
        self.assertEqual(self.source.execute("SELECT count(*) FROM archive_receipts").fetchone(), (0,))
        self.source.execute("DROP TRIGGER test_abort_notice")
        self.assertEqual(resume(self.source, 1, "reuse_only", 0).status, "committed")

    def test_process_exits_after_destination_commit_then_retry_gets_same_receipt(self):
        self.authorize()
        worker = """
import os
import sys
from resumecert.archive_transfer import connect
from resumecert.effect_delivery import pending_notice, receive

source, sink = map(connect, sys.argv[1:])
receipt = receive(sink, pending_notice(source, 1))
assert receipt.effect_id == 1
os._exit(23)
"""
        child = subprocess.run([sys.executable, "-c", worker, str(self.source_path), str(self.sink_path)],
                               cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=10)
        self.assertEqual(child.returncode, 23, child.stderr.decode())
        self.assertIsNotNone(pending_notice(self.source, 1))
        self.assertEqual(self.sink.execute("SELECT count(*) FROM inbox_effects").fetchone(), (1,))
        with closing(connect(self.source_path)) as recovered:
            receipt = deliver_once(recovered, 1, lambda n: receive(self.sink, n))
            self.assertEqual(receipt.effect_id, 1)
            self.assertIsNone(pending_notice(recovered, 1))
        self.assertEqual(self.sink.execute("SELECT count(*) FROM inbox_effects").fetchone(), (1,))

    def test_destination_rejects_same_key_with_other_payload(self):
        notice = self.authorize()
        receipt = receive(self.sink, notice)
        with self.assertRaises(DeliveryConflict):
            receive(self.sink, replace(notice, payload="different"))
        with self.assertRaises(DeliveryConflict):
            prepare_notice(self.source, "fixture", 1, "Other label")
        self.assertEqual(receive(self.sink, notice), receipt)
        self.assertEqual(self.sink.execute("SELECT count(*) FROM inbox_effects").fetchone(), (1,))

    def test_source_requires_matching_receipt_and_accepts_identical_ack_replay(self):
        notice = self.authorize()
        receipt = receive(self.sink, notice)
        with self.assertRaises(DeliveryConflict):
            acknowledge(self.source, 1, replace(receipt, payload_digest="wrong"))
        self.assertEqual(pending_notice(self.source, 1), notice)
        acknowledge(self.source, 1, receipt)
        acknowledge(self.source, 1, receipt)
        with self.assertRaises(DeliveryConflict):
            acknowledge(self.source, 1, replace(receipt, effect_id=2))
        self.assertIsNone(pending_notice(self.source, 1))

    def test_repaired_authorization_enqueues_a_historical_archive_event(self):
        seed(self.source, 2, zip(FIELDS, (1, 0, 1, 1, 0)))
        prepare_notice(self.source, "fixture", 2, "Archive receipt ready")
        checkpoint(self.source, 2, 4)
        with closing(connect(self.source_path)) as writer:
            change(writer, 2, ("curator_approved", "restricted_lane_open"))
        self.assertEqual(resume(self.source, 2, "reuse_only", 0).status, "defer")
        self.assertIsNone(pending_notice(self.source, 2))
        repaired = resume(self.source, 2, "reuse_exact", 1)
        self.assertEqual(repaired.status, "committed")
        self.assertEqual(repaired.certificate.spent, 1)
        with closing(connect(self.source_path)) as writer:
            change(writer, 2, ("curator_approved",))
        receipt = deliver_once(self.source, 2, lambda notice: receive(self.sink, notice))
        self.assertEqual(receipt.effect_id, 1)
        self.assertEqual(self.source.execute("SELECT status FROM notice_outbox WHERE request_id = 2")
                         .fetchone(), ("sent",))

    def test_two_destination_writers_receive_the_same_effect_id(self):
        notice = self.authorize()
        ready = Barrier(2)

        def worker():
            with closing(sqlite3.connect(self.sink_path, autocommit=True, timeout=4)) as sink:
                ready.wait(timeout=4)
                return receive(sink, notice)

        with ThreadPoolExecutor(max_workers=2) as workers:
            first, second = (workers.submit(worker) for _ in range(2))
            self.assertEqual(first.result(timeout=8), second.result(timeout=8))
        self.assertEqual(self.sink.execute("SELECT count(*) FROM inbox_effects").fetchone(), (1,))
