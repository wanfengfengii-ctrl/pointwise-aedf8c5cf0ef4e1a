from dataclasses import dataclass

from day.calendar import check_name
from publish.receipts import ReleaseReceipt, load_receipt
from publish.records import ReleaseRecord, find_release


@dataclass(frozen=True, slots=True)
class ReleaseHistory:
    record: ReleaseRecord
    receipt: ReleaseReceipt


def load_release_history(store, release_id):
    check_name(release_id, '成果号')
    with store.read_snapshot() as db:
        record = find_release(db, release_id)
        if record is None:
            raise ValueError('未知正式成果')
        return ReleaseHistory(record, load_receipt(db, record))
