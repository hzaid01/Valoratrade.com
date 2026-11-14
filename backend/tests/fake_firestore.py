"""
In-memory Firestore fake for unit tests.

Implements the subset of the google-cloud-firestore API used by the backend:
collection/document refs, get/set(merge)/update/delete/create, add, simple
where/order_by/limit queries, and transactions via ``run_in_transaction``.

Transactions are serialised with a single re-entrant lock, which is enough to
test that read-check-write logic runs as one atomic unit.
"""
import copy
import itertools
import threading
from datetime import datetime


class FakeConflict(Exception):
    """Raised by create() when the document already exists."""


class FakeSnapshot:
    def __init__(self, doc_id, data, reference=None):
        self.id = doc_id
        self._data = copy.deepcopy(data) if data is not None else None
        self.reference = reference

    @property
    def exists(self):
        return self._data is not None

    def to_dict(self):
        return copy.deepcopy(self._data) if self._data is not None else None

    def get(self, field):
        return (self._data or {}).get(field)


class FakeDocRef:
    def __init__(self, db, collection, doc_id):
        self._db = db
        self._collection = collection
        self.id = doc_id

    @property
    def _store(self):
        return self._db._data.setdefault(self._collection, {})

    def get(self, transaction=None):
        with self._db._lock:
            return FakeSnapshot(self.id, self._store.get(self.id), self)

    def set(self, data, merge=False):
        with self._db._lock:
            self._db.write_count += 1
            if merge and self.id in self._store:
                merged = copy.deepcopy(self._store[self.id])
                merged.update(copy.deepcopy(data))
                self._store[self.id] = merged
            else:
                self._store[self.id] = copy.deepcopy(data)

    def update(self, data):
        with self._db._lock:
            if self.id not in self._store:
                raise KeyError(f"No document to update: {self._collection}/{self.id}")
            self._db.write_count += 1
            self._store[self.id].update(copy.deepcopy(data))

    def create(self, data):
        with self._db._lock:
            if self.id in self._store:
                raise FakeConflict(f"{self._collection}/{self.id} exists")
            self._store[self.id] = copy.deepcopy(data)

    def delete(self):
        with self._db._lock:
            self._store.pop(self.id, None)


class FakeQuery:
    def __init__(self, collection, filters=None, order=None, limit_n=None):
        self._collection = collection
        self._filters = filters or []
        self._order = order
        self._limit = limit_n

    def where(self, field, op, value):
        return FakeQuery(self._collection, self._filters + [(field, op, value)], self._order, self._limit)

    def order_by(self, field, direction="ASCENDING"):
        return FakeQuery(self._collection, self._filters, (field, direction), self._limit)

    def limit(self, n):
        return FakeQuery(self._collection, self._filters, self._order, n)

    def stream(self):
        ops = {
            "==": lambda a, b: a == b,
            "!=": lambda a, b: a != b,
            "<": lambda a, b: a is not None and a < b,
            "<=": lambda a, b: a is not None and a <= b,
            ">": lambda a, b: a is not None and a > b,
            ">=": lambda a, b: a is not None and a >= b,
        }
        with self._collection._db._lock:
            items = list(self._collection._store.items())
        rows = [
            (doc_id, data) for doc_id, data in items
            if all(ops[op](data.get(f), v) for f, op, v in self._filters)
        ]
        if self._order:
            field, direction = self._order
            rows.sort(key=lambda r: r[1].get(field), reverse=str(direction).upper().startswith("DESC"))
        if self._limit is not None:
            rows = rows[: self._limit]
        return [FakeSnapshot(i, d, self._collection.document(i)) for i, d in rows]

    def get(self):
        return self.stream()


class FakeCollection(FakeQuery):
    _ids = itertools.count(1)

    def __init__(self, db, name):
        self._db = db
        self.name = name
        super().__init__(self)

    @property
    def _store(self):
        return self._db._data.setdefault(self.name, {})

    def document(self, doc_id=None):
        return FakeDocRef(self._db, self.name, doc_id or f"auto_{next(self._ids)}")

    def add(self, data):
        ref = self.document()
        ref.set(data)
        return datetime.utcnow(), ref


class FakeTransaction:
    """Buffers nothing: writes apply immediately while the global lock is held."""

    def __init__(self, db):
        self._db = db

    def get(self, ref):
        return ref.get(transaction=self)

    def set(self, ref, data, merge=False):
        ref.set(data, merge=merge)

    def update(self, ref, data):
        ref.update(data)

    def create(self, ref, data):
        ref.create(data)

    def delete(self, ref):
        ref.delete()


class FakeFirestore:
    def __init__(self):
        self._data = {}
        self._lock = threading.RLock()
        self.write_count = 0
        self.transaction_count = 0

    def collection(self, name):
        return FakeCollection(self, name)

    def transaction(self):
        return FakeTransaction(self)

    def run_in_transaction(self, fn):
        """Used by app.core.fs_tx.run_transaction when talking to the fake."""
        with self._lock:
            self.transaction_count += 1
            return fn(FakeTransaction(self))

    # Test helpers
    def raw(self, collection, doc_id):
        return copy.deepcopy(self._data.get(collection, {}).get(doc_id))
