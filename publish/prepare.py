from dataclasses import dataclass

from day.calendar import check_name, check_station_day
from intake.batches import StagedBatch, find_staged_batch
from intake.rows import RowMetrics, measure_rows
from publish.usage import QuotaReference, make_quota_reference


@dataclass(frozen=True, slots=True)
class PreparedBatch:
    release_id: str
    batch: StagedBatch
    quota: QuotaReference
    metrics: RowMetrics


def prepare_in_snapshot(store, db, station_day, batch_id, release_id, grant_id):
    check_station_day(station_day)
    check_name(batch_id, '报送号')
    check_name(release_id, '成果号')
    batch = find_staged_batch(db, batch_id)
    if batch is None:
        raise ValueError('未知合格报送')
    if batch.station_day != station_day:
        raise ValueError('报送与请求站日不符')
    reference = make_quota_reference(store, db, station_day, grant_id)
    return PreparedBatch(release_id, batch, reference, measure_rows(batch.rows))


def prepare_batch(store, station_day, batch_id, release_id, grant_id=None):
    with store.read_snapshot() as db:
        return prepare_in_snapshot(store, db, station_day, batch_id, release_id, grant_id)
