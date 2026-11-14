"""
Firestore transaction helper.

``run_transaction(db, fn)`` runs ``fn(transaction)`` atomically: reads made via
``ref.get(transaction=tx)`` and writes made via ``tx.set/update`` are committed
together, and Firestore retries ``fn`` on contention. ``fn`` must therefore be
side-effect free apart from transaction writes (it may run more than once).

The in-memory test fake exposes ``run_in_transaction`` and is used directly.
"""
from typing import Any, Callable


def run_transaction(db, fn: Callable[[Any], Any]) -> Any:
    fake_runner = getattr(db, "run_in_transaction", None)
    if callable(fake_runner):
        return fake_runner(fn)

    from google.cloud import firestore  # imported lazily; heavy dependency

    @firestore.transactional
    def _run(transaction):
        return fn(transaction)

    return _run(db.transaction())
