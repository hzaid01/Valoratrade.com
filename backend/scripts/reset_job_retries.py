#!/usr/bin/env python3
"""
Reset job retry counters in the ``job_retries`` Firestore collection.

Use this when a job has hit JobLockManager.MAX_RETRIES and you have fixed the
underlying cause. (A run with force=True also bypasses the limit.)

DRY RUN BY DEFAULT: without --apply, nothing is written; the script only
prints what it would reset.

Examples (run from backend/):
    python scripts/reset_job_retries.py                         # dry run, all docs
    python scripts/reset_job_retries.py --symbol BTCUSDT        # dry run, one symbol
    python scripts/reset_job_retries.py --symbol BTCUSDT --job-type training --apply
"""
import argparse
import os
import sys
from datetime import datetime
from typing import List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def reset_job_retries(
    db,
    symbol: Optional[str] = None,
    job_type: Optional[str] = None,
    apply: bool = False,
    actor: str = "reset_job_retries_script",
) -> List[dict]:
    """
    Find job_retries docs matching the filters and (if apply) set count to 0.

    Returns a list of {"doc_id", "count", "reset"} for every matching doc.
    """
    changes = []
    for doc in db.collection("job_retries").stream():
        doc_id = doc.id
        if symbol and not doc_id.startswith(f"{symbol}_"):
            continue
        if job_type and not doc_id.endswith(f"_{job_type}"):
            continue
        count = (doc.to_dict() or {}).get("count", 0)
        if count == 0:
            continue
        if apply:
            db.collection("job_retries").document(doc_id).set({
                "count": 0,
                "reset_at": datetime.utcnow().isoformat(),
                "reset_by": actor,
                "previous_count": count,
            })
        changes.append({"doc_id": doc_id, "count": count, "reset": apply})
    return changes


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--symbol", help="e.g. BTCUSDT (default: all)")
    parser.add_argument("--job-type", help="e.g. training (default: all)")
    parser.add_argument("--apply", action="store_true", help="actually write; default is dry run")
    args = parser.parse_args(argv)

    from app.firebase_config import get_firestore
    db = get_firestore()

    changes = reset_job_retries(db, args.symbol, args.job_type, apply=args.apply)
    if not changes:
        print("No non-zero retry counters matched.")
        return 0
    for c in changes:
        verb = "RESET" if c["reset"] else "would reset"
        print(f"{verb}: {c['doc_id']} (count was {c['count']})")
    if not args.apply:
        print("\nDry run only. Re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
