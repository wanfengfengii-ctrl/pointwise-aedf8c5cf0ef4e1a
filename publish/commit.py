from publish.prepare import PreparedBatch, prepare_batch, prepare_in_snapshot
from publish.receipts import load_receipt, make_release_receipt, store_receipt
from publish.records import ReleaseRecord, find_release, find_release_for_batch, store_release
from publish.usage import charge_release_rows


def commit_prepared(store, prepared, *, before_commit=None):
    if type(prepared) is not PreparedBatch:
        raise ValueError('须提供待提交发布项')
    batch = prepared.batch
    with store.write_transaction() as db:
        current = prepare_in_snapshot(store, db, batch.station_day, batch.batch_id,
                                      prepared.release_id, prepared.quota.grant_id)
        if current != prepared:
            raise ValueError('待提交发布项与当地合法报送或额度归属不符')
        record = ReleaseRecord(current.release_id, batch.batch_id, current.quota,
                               current.metrics.rows, current.batch.rows)
        existing = find_release(db, record.release_id)
        if existing is not None:
            if existing != record:
                raise ValueError('同号成果已使用不同参数')
            return existing, load_receipt(db, existing)
        if find_release_for_batch(db, batch.batch_id) is not None:
            raise ValueError('此报送已用其他成果号发布')
        remaining = charge_release_rows(db, current.quota, record.charged_rows)
        receipt = make_release_receipt(record, current.metrics, remaining)
        store_release(db, record)
        store_receipt(db, receipt)
        if before_commit is not None:
            before_commit()
        return record, receipt


def publish_batch(store, station_day, batch_id, release_id, grant_id=None, *, before_commit=None):
    prepared = prepare_batch(store, station_day, batch_id, release_id, grant_id)
    return commit_prepared(store, prepared, before_commit=before_commit)
