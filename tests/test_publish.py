from audit.lookup import read_batch
from day.balance import QuotaExceeded
from day.budgets import get_budget
from intake.batches import find_staged_batch, stage
from intake.rows import RowMetrics
from publish.commit import commit_prepared, publish_batch
from publish.prepare import prepare_batch
from tests import BRANCH_ROWS, DAY, NEXT_DAY, WarehouseCase, fail_before_commit


class PublishTests(WarehouseCase):
    def test_normalized_release_and_missing_capacity(self):
        self.allocate(count=3)
        staged = stage(self.branch, DAY, 'b', '北岸晨报', BRANCH_ROWS)
        record, receipt = publish_batch(self.branch, DAY, 'b', 'r', 'G')
        view = read_batch(self.branch, 'r')
        self.assertEqual(view.rows, staged.rows)
        self.assertEqual(view.record, record)
        self.assertEqual(view.receipt, receipt)
        self.assertEqual(view.metrics, RowMetrics(3, 1, 18))
        self.assertEqual([row.litres for row in view.rows], [7, None, 11])
        self.assertEqual([(r.origin.source, r.origin.source_line, r.origin.position) for r in view.rows],
                         [('北岸晨报', 12, 1), ('北岸晨报', 16, 2), ('北岸晨报', 20, 3)])
        self.assertEqual(record.charged_rows, 3)
        self.assertEqual(self.branch_available(), 0)
        with self.assertRaises(QuotaExceeded):
            self.load(self.branch, 'extra', [1], 'G')
        self.assert_unpublished(self.branch, 'extra')

    def test_equal_values_zero_and_large_integer(self):
        self.allocate(count=5)
        big = 10 ** 5000
        stage(self.branch, DAY, 'equal', '原始流量表', (
            (101, 5, 'L'), (34, 5000, 'ml'), (9, 0, 'L'), (71, big * 1000, 'ml'), (16, -1000, 'ml')
        ))
        publish_batch(self.branch, DAY, 'equal', 'r', 'G')
        self.reopen_branch()
        view = read_batch(self.branch, 'r')
        self.assertEqual([r.origin.source_line for r in view.rows], [101, 34, 9, 71, 16])
        self.assertTrue([r.litres for r in view.rows] == [5, 5, 0, big, -1])
        self.assertTrue(view.metrics == RowMetrics(5, 0, big + 9))
        self.assertEqual(self.branch_available(), 0)

    def test_invalid_rows_are_atomic(self):
        self.allocate()
        illegal = ((16, 1500, 'ml'), (16, None, 'kg'), (16, True, 'L'),
                   (16, 1.0, 'L'), (0, 7, 'L'), (16, 7))
        for entry in illegal:
            with self.subTest(entry=entry):
                with self.assertRaises(ValueError):
                    stage(self.branch, DAY, 'b', '现场报送', (BRANCH_ROWS[0], entry))
                with self.branch.read_snapshot() as db:
                    self.assertIsNone(find_staged_batch(db, 'b'))
        with self.assertRaises(ValueError):
            stage(self.branch, DAY, 'b', ' ', BRANCH_ROWS)
        with self.assertRaisesRegex(ValueError, '未知合格报送'):
            publish_batch(self.branch, DAY, 'b', 'r', 'G')
        self.assertEqual(self.branch_available(), 6)
        self.assert_unpublished(self.branch, 'r')
        stage(self.branch, DAY, 'b', '现场报送', BRANCH_ROWS)
        self.assertEqual(publish_batch(self.branch, DAY, 'b', 'r', 'G')[1].metrics, RowMetrics(3, 1, 18))

    def test_rollback_and_replay_both_warehouses(self):
        self.allocate(count=1, limit=2)
        for name, grant_id, reopen in (('hub', None, self.reopen_hub), ('branch', 'G', self.reopen_branch)):
            with self.subTest(warehouse=name):
                store = getattr(self, name)
                stage(store, DAY, 'one', name, ((1, 7, 'L'),))
                with self.assertRaisesRegex(RuntimeError, '提交前中断'):
                    publish_batch(store, DAY, 'one', 'r', grant_id, before_commit=fail_before_commit)
                reopen()
                store = getattr(self, name)
                self.assert_unpublished(store, 'r')
                remaining = get_budget(self.hub, DAY).available if grant_id is None else self.branch_available()
                self.assertEqual(remaining, 1)
                first = publish_batch(store, DAY, 'one', 'r', grant_id)
                reopen()
                self.assertEqual(publish_batch(getattr(self, name), DAY, 'one', 'r', grant_id), first)
                self.assertEqual(first[1].available_after, 0)
                remaining = get_budget(self.hub, DAY).available if grant_id is None else self.branch_available()
                self.assertEqual(remaining, 0)

    def test_publish_replay_conflicts(self):
        self.allocate()
        original = stage(self.branch, DAY, 'b', '晨报', BRANCH_ROWS)
        self.assertEqual(stage(self.branch, DAY, 'b', '晨报', BRANCH_ROWS), original)
        with self.assertRaises(ValueError):
            stage(self.branch, DAY, 'b', '晨报', ((12, 7, 'L'), *BRANCH_ROWS[1:]))
        first = publish_batch(self.branch, DAY, 'b', 'r', 'G')
        with self.assertRaises(ValueError):
            publish_batch(self.branch, DAY, 'b', 'another-release', 'G')
        stage(self.branch, DAY, 'another-batch', '晚报', ((1, 9, 'L'),))
        with self.assertRaises(ValueError):
            publish_batch(self.branch, DAY, 'another-batch', 'r', 'G')
        self.assertEqual(publish_batch(self.branch, DAY, 'b', 'r', 'G'), first)
        self.assertEqual(self.branch_available(), 3)

    def test_wrong_quota_binding(self):
        self.allocate()
        stage(self.branch, NEXT_DAY, 'other-day', '晨报', ((1, 7, 'L'),))
        with self.assertRaises(ValueError):
            prepare_batch(self.branch, NEXT_DAY, 'other-day', 'r', 'G')
        with self.assertRaises(ValueError):
            prepare_batch(self.branch, DAY, 'other-day', 'r', 'G')
        stage(self.branch, DAY, 'b', '晨报', ((1, 7, 'L'),))
        stage(self.hub, DAY, 'b', '晨报', ((1, 7, 'L'),))
        prepared = prepare_batch(self.branch, DAY, 'b', 'r', 'G')
        with self.assertRaises(ValueError):
            commit_prepared(self.hub, prepared)
        self.assert_unpublished(self.hub, 'r')
        self.assert_unpublished(self.branch, 'r')
