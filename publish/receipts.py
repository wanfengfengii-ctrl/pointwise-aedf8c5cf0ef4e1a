from dataclasses import dataclass

from day.store import decode_int, encode_int
from intake.rows import RowMetrics
from publish.usage import QuotaReference


@dataclass(frozen=True, slots=True)
class ReleaseReceipt:
    release_id: str
    batch_id: str
    quota: QuotaReference
    metrics: RowMetrics
    available_after: int


def ensure_receipt_schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS release_receipts (
        release_id TEXT PRIMARY KEY NOT NULL REFERENCES releases (release_id),
        row_count TEXT NOT NULL, missing_count TEXT NOT NULL, litres TEXT NOT NULL,
        available_after TEXT NOT NULL
    )''')


def make_release_receipt(record, metrics, available_after):
    return ReleaseReceipt(record.release_id, record.batch_id, record.quota, metrics, available_after)


def store_receipt(db, receipt):
    metrics = receipt.metrics
    db.execute('INSERT INTO release_receipts VALUES (?, ?, ?, ?, ?)', (
        receipt.release_id, encode_int(metrics.rows), encode_int(metrics.missing),
        encode_int(metrics.litres), encode_int(receipt.available_after)
    ))


def load_receipt(db, record):
    row = db.execute('SELECT * FROM release_receipts WHERE release_id = ?', (record.release_id,)).fetchone()
    if row is None:
        raise RuntimeError('正式成果缺少已提交回执')
    metrics = RowMetrics(decode_int(row['row_count']), decode_int(row['missing_count']), decode_int(row['litres']))
    return make_release_receipt(record, metrics, decode_int(row['available_after']))
