from dataclasses import replace

from audit.lookup import read_batch
from audit.reconcile import audit_day
from day.allocations import grant
from day.balance import QuotaExceeded
from day.budgets import get_budget
from exchange.documents import issue_return_document
from exchange.grants import accept_grant
from exchange.returns import list_day_returns, return_quota
from exchange.settlements import list_day_settlements, settle_return
from intake.batches import stage
from intake.rows import RowMetrics
from publish.commit import commit_prepared, publish_batch
from publish.prepare import prepare_batch
from tests import BRANCH, BRANCH_ROWS, DAY, KEY, WarehouseCase, fail_before_commit


class ReturnTests(WarehouseCase):
    def test_out_of_order_return_and_publish_capacity(self):
        self.allocate()
        stage(self.branch, DAY, 'morning', '北岸晨报', BRANCH_ROWS)
        _, original = publish_batch(self.branch, DAY, 'morning', 'morning-release', 'G')
        stage(self.branch, DAY, 'late', '北岸补报', ((22, 3, 'L'), (25, 4, 'L')))
        late = prepare_batch(self.branch, DAY, 'late', 'late-release', 'G')
        first = return_quota(self.branch, DAY, 'G', 'first', 1)
        second = return_quota(self.branch, DAY, 'G', 'second', 2)
        self.assertEqual((first.available_after, second.available_after), (2, 0))
        self.assertEqual((first.document.cumulative_rows, second.document.cumulative_rows), (1, 3))
        restored = settle_return(self.hub, second.document)
        self.assertEqual((restored.newly_restored, restored.available_after), (3, 7))
        self.assertEqual(settle_return(self.hub, second.document), restored)
        self.assertEqual(get_budget(self.hub, DAY).available, 7)
        _, hub_receipt = self.load(self.hub, 'hub-seven', [2, 0, None, 3, 5, 5, 8], source='总仓日报')
        self.assertEqual(hub_receipt.metrics, RowMetrics(7, 1, 23))
        covered = settle_return(self.hub, first.document)
        self.assertEqual((covered.newly_restored, covered.restored_through, covered.available_after), (0, 3, 0))
        with self.assertRaises(QuotaExceeded):
            self.load(self.hub, 'hub-eight', [7])
        with self.assertRaises(QuotaExceeded):
            commit_prepared(self.branch, late)
        self.assert_unpublished(self.hub, 'hub-eight')
        self.assert_unpublished(self.branch, 'late-release')
        view = read_batch(self.branch, 'morning-release')
        self.assertEqual(view.receipt, original)
        self.assertEqual([(r.litres, r.origin.source_line) for r in view.rows], [(7, 12), (None, 16), (11, 20)])
        hub_view = read_batch(self.hub, 'hub-seven')
        self.assertEqual(hub_view.receipt, hub_receipt)
        self.assertEqual([(r.litres, r.origin.source_line) for r in hub_view.rows],
                         [(2, 1), (0, 2), (None, 3), (3, 4), (5, 5), (5, 6), (8, 7)])
        report = audit_day(DAY, *self.snapshots())
        self.assertEqual((report.hub.rows, report.branch.rows, report.joint.litres), (7, 3, 41))
        self.assertTrue(report.quota.balanced)

    def _prepare_two_with_three_available(self):
        self.allocate()
        self.load(self.branch, 'already-released', [7, None, 11], 'G')
        stage(self.branch, DAY, 'two', '北岸补报', ((24, 2, 'L'), (28, 8, 'L')))
        return prepare_batch(self.branch, DAY, 'two', 'r', 'G')

    def test_return_first_blocks_prepared_publish(self):
        prepared = self._prepare_two_with_three_available()
        returned = return_quota(self.branch, DAY, 'G', 'return-all', 3)
        self.reopen_branch()
        self.assertEqual(return_quota(self.branch, DAY, 'G', 'return-all', 3), returned)
        self.assertEqual(self.branch_available(), 0)
        with self.assertRaises(QuotaExceeded):
            commit_prepared(self.branch, prepared)
        self.assert_unpublished(self.branch, 'r')
        self.assertEqual(settle_return(self.hub, returned.document).available_after, 7)

    def test_publish_first_blocks_oversized_return(self):
        prepared = self._prepare_two_with_three_available()
        _, receipt = commit_prepared(self.branch, prepared)
        self.assertEqual(receipt.available_after, 1)
        with self.assertRaises(QuotaExceeded):
            return_quota(self.branch, DAY, 'G', 'return-all', 3)
        with self.branch.read_snapshot() as db:
            self.assertEqual(list_day_returns(db, DAY), ())
        self.assertEqual(self.branch_available(), 1)
        self.assertEqual(read_batch(self.branch, 'r').receipt, receipt)
        valid = return_quota(self.branch, DAY, 'G', 'return-all', 1)
        self.assertEqual(settle_return(self.hub, valid.document).available_after, 5)

    def test_partial_return_keeps_remaining_publishable(self):
        prepared = self._prepare_two_with_three_available()
        returned = return_quota(self.branch, DAY, 'G', 'return-one', 1)
        self.assertEqual(returned.available_after, 2)
        _, receipt = commit_prepared(self.branch, prepared)
        self.assertEqual((receipt.metrics, receipt.available_after), (RowMetrics(2, 0, 10), 0))
        self.assertEqual(settle_return(self.hub, returned.document).available_after, 5)
        self.assertTrue(audit_day(DAY, *self.snapshots()).quota.balanced)

    def test_branch_return_rollback_and_lost_reply(self):
        self.allocate()
        with self.assertRaisesRegex(RuntimeError, '提交前中断'):
            return_quota(self.branch, DAY, 'G', 'first', 1, before_commit=fail_before_commit)
        self.reopen_branch()
        with self.branch.read_snapshot() as db:
            self.assertEqual(list_day_returns(db, DAY), ())
        self.assertEqual(self.branch_available(), 6)
        first = return_quota(self.branch, DAY, 'G', 'first', 1)
        return_quota(self.branch, DAY, 'G', 'later', 2)
        self.reopen_branch()
        self.assertEqual(return_quota(self.branch, DAY, 'G', 'first', 1), first)
        self.assertEqual(first.available_after, 5)
        self.assertEqual(self.branch_available(), 3)
        with self.assertRaises(ValueError):
            return_quota(self.branch, DAY, 'G', 'first', 2)
        self.assertEqual(self.branch_available(), 3)

    def test_settlement_rollback_and_replay_after_live_balance_changes(self):
        self.allocate()
        returned = return_quota(self.branch, DAY, 'G', 'return', 1)
        with self.assertRaisesRegex(RuntimeError, '提交前中断'):
            settle_return(self.hub, returned.document, before_commit=fail_before_commit)
        self.reopen_hub()
        with self.hub.read_snapshot() as db:
            self.assertEqual(list_day_settlements(db, DAY), ())
        self.assertEqual(get_budget(self.hub, DAY).available, 4)
        original = settle_return(self.hub, returned.document)
        self.assertEqual((original.newly_restored, original.available_after), (1, 5))
        self.reopen_hub()
        record, receipt = self.load(self.hub, 'later', [21])
        self.assertEqual(get_budget(self.hub, DAY).available, 4)
        self.assertEqual(settle_return(self.hub, returned.document), original)
        conflict = issue_return_document(KEY, returned.document.grant, 'return', 2, 2)
        with self.assertRaisesRegex(ValueError, '同号退额'):
            settle_return(self.hub, conflict)
        self.assertEqual(get_budget(self.hub, DAY).available, 4)
        self.assertEqual((read_batch(self.hub, 'later').record, read_batch(self.hub, 'later').receipt), (record, receipt))

    def test_cross_grant_binding(self):
        self.allocate(count=4)
        other = grant(self.hub, DAY, 'other-grant', BRANCH, 4)
        accept_grant(self.branch, other)
        returned = return_quota(self.branch, DAY, 'G', 'return', 1)
        with self.assertRaises(ValueError):
            return_quota(self.branch, DAY, 'other-grant', 'return', 1)
        with self.assertRaises(ValueError):
            settle_return(self.hub, replace(returned.document, grant=other))
        self.assertEqual(get_budget(self.hub, DAY).available, 2)
        self.assertEqual(self.branch_available('other-grant'), 4)
        self.assertEqual(settle_return(self.hub, returned.document).available_after, 3)
        hub, _ = self.snapshots()
        self.assertEqual([row.document.grant.grant_id for row in hub.settlements], ['G'])
