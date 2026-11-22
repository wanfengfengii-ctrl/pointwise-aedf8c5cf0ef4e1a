from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from audit.snapshots import read_day_snapshot
from day.allocations import grant
from day.budgets import open_day
from day.calendar import parse_station_day
from day.workspaces import open_branch_store, open_hub_store
from exchange.grants import accept_grant, find_accepted_grant
from intake.batches import stage
from publish.commit import publish_batch
from publish.records import find_release


DAY = parse_station_day('石桥站', '2026-11-21')
NEXT_DAY = parse_station_day('石桥站', '2026-11-22')
HUB = '总仓'
BRANCH = '北岸分仓'
KEY = b'watershed-regression-key'
BRANCH_ROWS = ((12, 7000, 'ml'), (16, None, 'L'), (20, 11, 'L'))


def fail_before_commit():
    raise RuntimeError('操作在提交前中断')


class WarehouseCase(TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.hub_path = Path(directory.name) / 'hub.sqlite'
        self.branch_path = Path(directory.name) / 'branch.sqlite'
        self.hub = open_hub_store(self.hub_path, HUB, BRANCH, KEY)
        self.addCleanup(lambda: self.hub.close())
        self.branch = open_branch_store(self.branch_path, BRANCH, HUB, KEY)
        self.addCleanup(lambda: self.branch.close())

    def reopen_hub(self):
        self.hub.close()
        self.hub = open_hub_store(self.hub_path, HUB, BRANCH, KEY)

    def reopen_branch(self):
        self.branch.close()
        self.branch = open_branch_store(self.branch_path, BRANCH, HUB, KEY)

    def allocate(self, count=6, limit=10):
        open_day(self.hub, DAY, limit)
        document = grant(self.hub, DAY, 'G', BRANCH, count)
        accept_grant(self.branch, document)
        return document

    def branch_available(self, grant_id='G'):
        with self.branch.read_snapshot() as db:
            return find_accepted_grant(db, grant_id).available

    def load(self, store, batch_id, values, grant_id=None, *, release_id=None, source='试验报送', day=DAY):
        entries = tuple((position, value, 'L') for position, value in enumerate(values, 1))
        stage(store, day, batch_id, source, entries)
        return publish_batch(store, day, batch_id, release_id or batch_id, grant_id)

    def assert_unpublished(self, store, release_id):
        with store.read_snapshot() as db:
            self.assertIsNone(find_release(db, release_id))
            receipt = db.execute('SELECT release_id FROM release_receipts WHERE release_id = ?', (release_id,))
            self.assertIsNone(receipt.fetchone())

    def snapshots(self, day=DAY):
        return read_day_snapshot(self.hub, day), read_day_snapshot(self.branch, day)
