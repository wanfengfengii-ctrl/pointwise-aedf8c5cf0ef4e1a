from dataclasses import dataclass

from audit.history import load_release_history
from audit.totals import measure_release
from intake.rows import RowMetrics
from publish.receipts import ReleaseReceipt
from publish.records import ReleaseRecord


@dataclass(frozen=True, slots=True)
class BatchReadView:
    record: ReleaseRecord
    metrics: RowMetrics
    receipt: ReleaseReceipt

    @property
    def rows(self):
        return self.record.rows


def read_batch(store, release_id):
    history = load_release_history(store, release_id)
    measured = measure_release(history.record)
    if measured != history.receipt.metrics or measured.rows != history.record.charged_rows:
        raise RuntimeError('正式成果计量与原回执或占额不符')
    return BatchReadView(history.record, measured, history.receipt)
