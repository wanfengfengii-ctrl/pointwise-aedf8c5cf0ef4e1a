from dataclasses import dataclass

from intake.rows import measure_rows


@dataclass(frozen=True, slots=True)
class SiteTotals:
    rows: int
    missing: int
    litres: int


def measure_release(record):
    return measure_rows(record.rows)


def sum_release_metrics(records):
    count = missing = litres = 0
    for record in records:
        metrics = measure_release(record)
        count += metrics.rows
        missing += metrics.missing
        litres += metrics.litres
    return SiteTotals(count, missing, litres)
