from audit.lookup import read_batch
from audit.reconcile import audit_day
from audit.snapshots import read_day_snapshot
from audit.totals import SiteTotals
from day.allocations import grant
from day.budgets import open_day
from exchange.grants import accept_grant
from exchange.returns import return_quota
from exchange.settlements import settle_return
from intake.batches import stage
from publish.commit import publish_batch
from tests import BRANCH, BRANCH_ROWS, DAY, NEXT_DAY, WarehouseCase


class AuditTests(WarehouseCase):
    def test_history_provenance_and_same_local_report_id(self):
        self.allocate()
        stage(self.branch, DAY, 'same', '北岸晨报', BRANCH_ROWS)
        publish_batch(self.branch, DAY, 'same', 'same', 'G')
        original = read_batch(self.branch, 'same')
        self.load(self.branch, 'later', [2], 'G', source='北岸晚报')
        self.load(self.hub, 'same', [5], source='总仓晨报')
        self.reopen_hub()
        self.reopen_branch()
        current = read_batch(self.branch, 'same')
        self.assertEqual(current, original)
        self.assertEqual([(row.origin.source, row.origin.source_line) for row in current.rows],
                         [('北岸晨报', 12), ('北岸晨报', 16), ('北岸晨报', 20)])
        self.assertEqual(read_batch(self.branch, 'later').rows[0].origin.source, '北岸晚报')
        self.assertEqual(read_batch(self.hub, 'same').rows[0].origin.source, '总仓晨报')
        self.assertEqual(publish_batch(self.branch, DAY, 'same', 'same', 'G'), (original.record, original.receipt))
        report = audit_day(DAY, *self.snapshots())
        self.assertEqual(report.hub, SiteTotals(1, 0, 5))
        self.assertEqual(report.branch, SiteTotals(4, 1, 20))
        self.assertEqual(report.joint, SiteTotals(5, 1, 25))
        self.assertTrue(report.quota.balanced)
        with self.assertRaisesRegex(ValueError, '未知正式成果'):
            read_batch(self.branch, 'absent')

    def test_formal_rows_only_and_different_day(self):
        self.allocate()
        self.load(self.hub, 'today', [0])
        stage(self.branch, DAY, 'draft', '待发布', BRANCH_ROWS)
        open_day(self.hub, NEXT_DAY, 3)
        self.load(self.hub, 'tomorrow', [30], day=NEXT_DAY)
        hub, branch = self.snapshots()
        report = audit_day(DAY, hub, branch)
        self.assertEqual(report.joint, SiteTotals(1, 0, 0))
        self.assertEqual(report.branch, SiteTotals(0, 0, 0))
        self.assertTrue(report.quota.balanced)
        with self.assertRaises(ValueError):
            audit_day(DAY, hub, read_day_snapshot(self.branch, NEXT_DAY))
        with self.assertRaises(ValueError):
            audit_day(DAY, branch, hub)

    def test_in_transit_and_separate_snapshots(self):
        open_day(self.hub, DAY, 10)
        grant_doc = grant(self.hub, DAY, 'G', BRANCH, 6)
        pending = audit_day(DAY, *self.snapshots()).quota
        self.assertEqual((pending.hub_available, pending.branch_available, pending.pending_acceptance,
                          pending.pending_return, pending.difference), (4, 0, 6, 0, 0))
        self.assertTrue(pending.balanced)
        accept_grant(self.branch, grant_doc)
        branch_before_return = read_day_snapshot(self.branch, DAY)
        self.load(self.branch, 'observed', [7, None, 11], 'G')
        first = return_quota(self.branch, DAY, 'G', 'one', 1)
        second = return_quota(self.branch, DAY, 'G', 'two', 2)
        pending = audit_day(DAY, *self.snapshots()).quota
        self.assertEqual((pending.published, pending.hub_available, pending.branch_available,
                          pending.pending_acceptance, pending.pending_return, pending.difference), (3, 4, 0, 0, 3, 0))
        self.assertTrue(pending.balanced)
        settle_return(self.hub, first.document)
        partial = audit_day(DAY, *self.snapshots()).quota
        self.assertEqual((partial.pending_return, partial.grants[0].returned, partial.grants[0].restored), (2, 3, 1))
        self.assertTrue(partial.balanced)
        settle_return(self.hub, second.document)
        hub_after, branch_after = self.snapshots()
        fresh = audit_day(DAY, hub_after, branch_after).quota
        self.assertEqual((fresh.hub_available, fresh.pending_return, fresh.difference), (7, 0, 0))
        self.assertTrue(fresh.balanced)
        mixed = audit_day(DAY, hub_after, branch_before_return).quota
        self.assertEqual((mixed.branch_available, mixed.difference), (6, -3))
        self.assertFalse(mixed.balanced)
        self.assertTrue(mixed.issues)
