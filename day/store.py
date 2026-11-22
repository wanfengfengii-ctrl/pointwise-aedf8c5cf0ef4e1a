import hashlib
import sqlite3
from contextlib import contextmanager

from day.calendar import check_name


def encode_int(value: int) -> str:
    # SQLite INTEGER 只有 64 位。十六进制文本也避开十进制转换的位数限制。
    return format(value, 'x')


def decode_int(value: str) -> int:
    return int(value, 16)


class WarehouseStore:
    def __init__(self, db, role, warehouse_id, peer_id, key):
        self._db = db
        self.role = role
        self.warehouse_id = warehouse_id
        self.peer_id = peer_id
        self._key = key

    @property
    def exchange_key(self) -> bytes:
        return self._key

    @classmethod
    def open(cls, path, role, warehouse_id, peer_id, key, initialize=None):
        check_name(warehouse_id, '仓库')
        check_name(peer_id, '对端仓库')
        if role not in ('hub', 'branch') or warehouse_id == peer_id:
            raise ValueError('仓库角色或对端身份无效')
        if type(key) is not bytes or len(key) < 16:
            raise ValueError('交接密钥须为至少 16 字节的 bytes')
        db = sqlite3.connect(path, isolation_level=None)
        store = cls(db, role, warehouse_id, peer_id, key)
        try:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA foreign_keys = ON')
            identity = (role, warehouse_id, peer_id, hashlib.sha256(key).hexdigest())
            with store.write_transaction() as txn:
                txn.execute('''CREATE TABLE IF NOT EXISTS warehouse_identity (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    role TEXT NOT NULL, warehouse_id TEXT NOT NULL,
                    peer_id TEXT NOT NULL, key_fingerprint TEXT NOT NULL
                )''')
                saved = txn.execute('''SELECT role, warehouse_id, peer_id, key_fingerprint
                    FROM warehouse_identity WHERE singleton = 1''').fetchone()
                if saved is None:
                    txn.execute('INSERT INTO warehouse_identity VALUES (1, ?, ?, ?, ?)', identity)
                elif tuple(saved) != identity:
                    raise ValueError('仓库身份或信任配置与原库不符')
                if initialize is not None:
                    initialize(txn)
        except BaseException:
            store.close()
            raise
        return store

    def require_role(self, role):
        if self.role != role:
            raise ValueError(f'此操作要求 {role} 仓库')

    @contextmanager
    def _transaction(self, begin):
        self._db.execute(begin)
        try:
            yield self._db
            self._db.commit()
        except BaseException:
            if self._db.in_transaction:
                self._db.rollback()
            raise

    def read_snapshot(self):
        return self._transaction('BEGIN')

    def write_transaction(self):
        return self._transaction('BEGIN IMMEDIATE')

    def close(self):
        self._db.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, value, traceback):
        self.close()
