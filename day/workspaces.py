from day.allocations import ensure_allocation_schema
from day.budgets import ensure_budget_schema
from day.store import WarehouseStore
from exchange.grants import ensure_grant_schema
from exchange.returns import ensure_return_schema
from exchange.settlements import ensure_settlement_schema
from intake.batches import ensure_batch_schema
from publish.receipts import ensure_receipt_schema
from publish.records import ensure_release_schema


def _initialize_hub(db):
    ensure_budget_schema(db)
    ensure_allocation_schema(db)
    ensure_settlement_schema(db)
    ensure_batch_schema(db)
    ensure_release_schema(db)
    ensure_receipt_schema(db)


def _initialize_branch(db):
    ensure_grant_schema(db)
    ensure_return_schema(db)
    ensure_batch_schema(db)
    ensure_release_schema(db)
    ensure_receipt_schema(db)


def open_hub_store(path, hub_id, branch_id, key):
    return WarehouseStore.open(path, 'hub', hub_id, branch_id, key, _initialize_hub)


def open_branch_store(path, branch_id, hub_id, key):
    return WarehouseStore.open(path, 'branch', branch_id, hub_id, key, _initialize_branch)
