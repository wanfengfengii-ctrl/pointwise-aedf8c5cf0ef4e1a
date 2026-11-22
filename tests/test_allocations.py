from dataclasses import replace

from day.allocations import get_issued_grant, grant
from day.balance import QuotaExceeded
from day.budgets import get_budget, open_day
from day.calendar import parse_station_day
from day.workspaces import open_branch_store, open_hub_store
from exchange.grants import accept_grant
from exchange.returns import return_quota
from tests import BRANCH, DAY, HUB, KEY, NEXT_DAY, WarehouseCase


class AllocationTests(WarehouseCase):
    def test_open_issue_accept(self):
        budget = open_day(self.hub, DAY, 10)
        self.assertEqual((budget.limit, budget.available), (10, 10))
        document = grant(self.hub, DAY, 'G', BRANCH, 6)
        self.assertEqual(get_budget(self.hub, DAY).available, 4)
        first = accept_grant(self.branch, document)
        self.assertEqual(first.available, 6)
        self.assertEqual(accept_grant(self.branch, document), first)
        self.assertEqual(self.branch_available(), 6)
        with self.assertRaises(QuotaExceeded):
            grant(self.hub, DAY, 'over', BRANCH, 5)
        with self.hub.read_snapshot() as db:
            self.assertIsNone(get_issued_grant(db, 'over'))

    def test_reopen_day(self):
        opened = open_day(self.hub, DAY, 10)
        grant(self.hub, DAY, 'G', BRANCH, 6)
        self.reopen_hub()
        self.assertEqual(open_day(self.hub, DAY, 10), opened)
        self.assertEqual(get_budget(self.hub, DAY).available, 4)
        for limit in (4, -1, True):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                open_day(self.hub, DAY, limit)
        with self.assertRaises(ValueError):
            open_hub_store(self.hub_path, HUB, BRANCH, b'changed-regression-key')
        with self.assertRaises(ValueError):
            open_branch_store(self.hub_path, BRANCH, HUB, KEY)
        self.assertEqual(get_budget(self.hub, DAY).available, 4)
        self.assertEqual(open_day(self.hub, NEXT_DAY, 0).available, 0)
        for value in ('20261121', '2026-02-30'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_station_day('石桥站', value)

    def test_issue_replay_after_reopen(self):
        open_day(self.hub, DAY, 10)
        grant(self.hub, DAY, 'G', BRANCH, 6)
        self.reopen_hub()
        replay = grant(self.hub, DAY, 'G', BRANCH, 6)
        with self.hub.read_snapshot() as db:
            self.assertEqual(get_issued_grant(db, 'G'), replay)
        self.assertEqual(accept_grant(self.branch, replay).available, 6)
        self.assertEqual(get_budget(self.hub, DAY).available, 4)

    def test_accept_replay_after_use(self):
        document = self.allocate()
        return_quota(self.branch, DAY, 'G', 'return', 1)
        self.reopen_branch()
        self.assertEqual(accept_grant(self.branch, document).available, 6)
        self.assertEqual(self.branch_available(), 5)

    def test_binding_and_conflicts(self):
        document = self.allocate()
        open_day(self.hub, NEXT_DAY, 10)
        for day, count in ((DAY, 5), (NEXT_DAY, 6)):
            with self.subTest(day=day, count=count), self.assertRaises(ValueError):
                grant(self.hub, day, 'G', BRANCH, count)
        with self.assertRaises(ValueError):
            grant(self.hub, DAY, 'other', '南岸分仓', 1)
        with open_branch_store(':memory:', '南岸分仓', HUB, KEY) as other:
            with self.assertRaises(ValueError):
                accept_grant(other, document)
        with self.assertRaises(ValueError):
            accept_grant(self.branch, replace(document, grant_id='other'))
        with open_hub_store(':memory:', HUB, BRANCH, KEY) as alternate:
            open_day(alternate, DAY, 10)
            conflict = grant(alternate, DAY, 'G', BRANCH, 5)
        with self.assertRaises(ValueError):
            accept_grant(self.branch, conflict)
        self.assertEqual(self.branch_available(), 6)
        self.assertEqual(get_budget(self.hub, NEXT_DAY).available, 10)

    def test_large_exact_quota(self):
        limit = 10 ** 5000
        self.allocate(count=limit - 4, limit=limit)
        self.reopen_hub()
        self.reopen_branch()
        self.assertTrue(get_budget(self.hub, DAY).limit == limit)
        self.assertEqual(get_budget(self.hub, DAY).available, 4)
        self.assertTrue(self.branch_available() == limit - 4)
