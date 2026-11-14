"""
Model Registry

Stores and versions all trained models with full metadata.
Persists to Firestore for Cloud Run compatibility.
Supports rollback and lineage tracking.
"""
import copy
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Callable, Dict, List, Optional, Set, Tuple
import json
from pathlib import Path

logger = logging.getLogger(__name__)


class ModelStatus(str, Enum):
    """
    Model lifecycle. Stored as the plain string value, so existing
    "registered" / "champion" / "retired" docs load unchanged.
    """
    REGISTERED = "registered"  # trained, not yet evaluated (or legacy: unknown)
    PENDING = "pending"        # evaluated; waiting for forward metrics
    REJECTED = "rejected"      # failed the promotion gates
    CHAMPION = "champion"      # serving signals for its symbol
    RETIRED = "retired"        # no longer serving (see retired_reason)


class RetiredReason(str, Enum):
    SUPERSEDED = "superseded"  # replaced by a newer champion: was good
    DEMOTED = "demoted"        # removed for degradation: never roll back to it
    MANUAL = "manual"


# Normal transitions. Anything else needs override=True (logged + audited).
MODEL_TRANSITIONS: Dict[ModelStatus, Set[ModelStatus]] = {
    ModelStatus.REGISTERED: {ModelStatus.PENDING, ModelStatus.REJECTED, ModelStatus.CHAMPION, ModelStatus.RETIRED},
    ModelStatus.PENDING: {ModelStatus.PENDING, ModelStatus.REJECTED, ModelStatus.CHAMPION, ModelStatus.RETIRED},
    ModelStatus.REJECTED: {ModelStatus.RETIRED},
    ModelStatus.CHAMPION: {ModelStatus.RETIRED},
    ModelStatus.RETIRED: set(),
}


class IllegalModelTransition(Exception):
    pass


def _status(value) -> ModelStatus:
    try:
        return ModelStatus(value)
    except ValueError:
        logger.error(f"Unknown model status {value!r}; treating as registered")
        return ModelStatus.REGISTERED


@dataclass
class ModelMetadata:
    """Metadata for a registered model."""
    model_id: str
    version: str
    symbol: str
    created_at: datetime
    
    # Paths
    patch_tst_path: str
    xgboost_path: str
    
    # Lineage
    dataset_version: str
    feature_version: str
    training_snapshot: str
    
    # Metrics
    training_metrics: Dict
    validation_metrics: Dict
    forward_metrics: Optional[Dict] = None
    
    # Status (ModelStatus value; only changed via ModelRegistry transitions)
    status: str = ModelStatus.REGISTERED.value
    promoted_at: Optional[datetime] = None
    retired_at: Optional[datetime] = None
    retired_reason: Optional[str] = None
    promotion_status: Optional[str] = None   # last PromotionStatus value
    baseline_summary: Optional[Dict] = None  # baseline name -> sharpe, for re-evaluation
    provisional: bool = False                # first model promoted despite gate (log_only)
    
    def to_dict(self) -> Dict:
        return {
            "model_id": self.model_id,
            "version": self.version,
            "symbol": self.symbol,
            "created_at": self.created_at.isoformat(),
            "paths": {
                "patch_tst": self.patch_tst_path,
                "xgboost": self.xgboost_path
            },
            "lineage": {
                "dataset": self.dataset_version,
                "features": self.feature_version,
                "snapshot": self.training_snapshot
            },
            "metrics": {
                "training": self.training_metrics,
                "validation": self.validation_metrics,
                "forward": self.forward_metrics
            },
            "status": self.status,
            "promoted_at": self.promoted_at.isoformat() if self.promoted_at else None,
            "retired_at": self.retired_at.isoformat() if self.retired_at else None,
            "retired_reason": self.retired_reason,
            "promotion_status": self.promotion_status,
            "baseline_summary": self.baseline_summary,
            "provisional": self.provisional,
        }

    @classmethod
    def from_dict(cls, model_data: Dict) -> "ModelMetadata":
        return cls(
            model_id=model_data["model_id"],
            version=model_data["version"],
            symbol=model_data["symbol"],
            created_at=datetime.fromisoformat(model_data["created_at"]),
            patch_tst_path=model_data["paths"]["patch_tst"],
            xgboost_path=model_data["paths"]["xgboost"],
            dataset_version=model_data["lineage"]["dataset"],
            feature_version=model_data["lineage"]["features"],
            training_snapshot=model_data["lineage"]["snapshot"],
            training_metrics=model_data["metrics"]["training"],
            validation_metrics=model_data["metrics"]["validation"],
            forward_metrics=model_data["metrics"].get("forward"),
            status=_status(model_data.get("status", "registered")).value,
            promoted_at=datetime.fromisoformat(model_data["promoted_at"]) if model_data.get("promoted_at") else None,
            retired_at=datetime.fromisoformat(model_data["retired_at"]) if model_data.get("retired_at") else None,
            retired_reason=model_data.get("retired_reason"),
            promotion_status=model_data.get("promotion_status"),
            baseline_summary=model_data.get("baseline_summary"),
            provisional=bool(model_data.get("provisional", False)),
        )


class ModelRegistry:
    """
    Central registry for all trained models.
    
    Persists to Firestore so state survives Cloud Run container restarts.
    Also saves locally as a cache/fallback.
    """
    
    def __init__(self, storage_path: str = "model_registry"):
        self.storage_path = Path(storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)
        
        # Runs directory for training artifacts
        self.runs_path = Path("runs")
        self.runs_path.mkdir(parents=True, exist_ok=True)
        
        # Models directory for production model files
        self.models_path = Path("models")
        self.models_path.mkdir(parents=True, exist_ok=True)
        
        # Orphan tracking for failed registrations
        self.orphan_path = self.storage_path / "orphan_runs.json"
        
        self._models: Dict[str, ModelMetadata] = {}
        self._champions: Dict[str, str] = {}  # symbol -> model_id
        
        # Try Firestore first, then local fallback
        self._load_from_firestore()
        if not self._models:
            self._load_registry()
    
    def _get_firestore(self):
        """Get Firestore client, returns None if unavailable."""
        try:
            from app.firebase_config import get_firestore
            return get_firestore()
        except Exception as e:
            logger.warning(f"Firestore unavailable for registry: {e}")
            return None
    
    # ─── Atomic mutation ─────────────────────────────────────────

    def _mutate(self, fn: Callable[[Dict[str, ModelMetadata], Dict[str, str], List[Dict]], object]):
        """
        Apply ``fn(models, champions, audit)`` to the CURRENT registry in
        Firestore inside one transaction and write it back. Previously each
        instance wrote its whole in-memory registry, so a stale instance
        could revert another instance's promotion. ``fn`` must be pure (it
        may be retried); it appends audit rows to ``audit``.
        """
        db = self._get_firestore()
        if db is None:
            audit: List[Dict] = []
            result = fn(self._models, self._champions, audit)
            self._save_registry()
            return result

        from app.core.fs_tx import run_transaction
        ref = db.collection("system").document("model_registry")

        def _tx(transaction):
            snap = ref.get(transaction=transaction)
            if snap.exists:
                models, champions = self._parse(snap.to_dict())
            else:  # first write: seed from what we loaded (e.g. local fallback)
                models, champions = copy.deepcopy(self._models), dict(self._champions)
            audit: List[Dict] = []
            result = fn(models, champions, audit)
            transaction.set(ref, {
                "models": {k: v.to_dict() for k, v in models.items()},
                "champions": champions,
                "updated_at": datetime.utcnow().isoformat()
            })
            for row in audit:
                transaction.set(db.collection("model_transitions").document(), row)
            return result, models, champions

        result, models, champions = run_transaction(db, _tx)
        self._models, self._champions = models, champions
        self._save_registry()
        return result

    @staticmethod
    def _apply_status(
        models: Dict[str, ModelMetadata],
        champions: Dict[str, str],
        audit: List[Dict],
        model_id: str,
        new: ModelStatus,
        actor: str,
        reason: str,
        override: bool = False,
        retired_reason: Optional[RetiredReason] = None,
    ) -> bool:
        """
        The ONLY place a model's status changes. Returns False (no-op) if
        already in ``new`` and the transition is not a self-loop; raises
        IllegalModelTransition if not allowed and not overridden.
        """
        m = models[model_id]
        cur = _status(m.status)
        if cur == new and new not in MODEL_TRANSITIONS[cur]:
            return False  # idempotent duplicate
        allowed = new in MODEL_TRANSITIONS[cur]
        if not allowed and not override:
            raise IllegalModelTransition(f"{model_id}: {cur.value} -> {new.value} not allowed")
        now = datetime.utcnow()
        m.status = new.value
        if new == ModelStatus.CHAMPION:
            m.promoted_at = now
            m.retired_at = None
            m.retired_reason = None
            champions[m.symbol] = model_id
        elif new == ModelStatus.RETIRED:
            m.retired_at = now
            m.retired_reason = (retired_reason or RetiredReason.MANUAL).value
            if champions.get(m.symbol) == model_id:
                del champions[m.symbol]
        audit.append({
            "model_id": model_id, "symbol": m.symbol,
            "from_status": cur.value, "to_status": new.value,
            "actor": actor, "reason": reason,
            "override": bool(override and not allowed),
            "timestamp": now.isoformat(),
        })
        return True

    # ─── Public API ──────────────────────────────────────────────

    def register(
        self,
        version: str,
        symbol: str,
        patch_tst_path: str,
        xgboost_path: str,
        dataset_version: str,
        feature_version: str,
        training_snapshot: str,
        training_metrics: Dict,
        validation_metrics: Dict,
        baseline_summary: Optional[Dict] = None
    ) -> ModelMetadata:
        """Register a new model (status REGISTERED)."""
        model_id = f"{symbol}_{version}"

        metadata = ModelMetadata(
            model_id=model_id,
            version=version,
            symbol=symbol,
            created_at=datetime.utcnow(),
            patch_tst_path=patch_tst_path,
            xgboost_path=xgboost_path,
            dataset_version=dataset_version,
            feature_version=feature_version,
            training_snapshot=training_snapshot,
            training_metrics=training_metrics,
            validation_metrics=validation_metrics,
            baseline_summary=baseline_summary
        )

        def fn(models, champions, audit):
            if model_id in models:
                return models[model_id]  # idempotent re-register
            models[model_id] = copy.deepcopy(metadata)
            return models[model_id]

        result = self._mutate(fn)
        logger.info(f"Registered model: {model_id}")
        return self._models.get(model_id, result)

    def get(self, model_id: str) -> Optional[ModelMetadata]:
        """Get model metadata by ID."""
        return self._models.get(model_id)

    def get_champion(self, symbol: str) -> Optional[ModelMetadata]:
        """Get current champion model for a symbol."""
        model_id = self._champions.get(symbol)
        if model_id:
            return self._models.get(model_id)
        return None

    def promote_to_champion(
        self,
        model_id: str,
        override: bool = False,
        actor: str = "system",
        reason: str = "",
        provisional: bool = False
    ) -> bool:
        """
        Promote a model to champion; the previous champion is retired as
        SUPERSEDED. A RETIRED or REJECTED model is refused unless
        override=True, which is logged at WARNING and audited.
        Returns False (logged) if refused or unknown.
        """
        def fn(models, champions, audit):
            if model_id not in models:
                return "missing"
            m = models[model_id]
            if _status(m.status) == ModelStatus.CHAMPION and champions.get(m.symbol) == model_id:
                return "noop"
            cur = _status(m.status)
            if ModelStatus.CHAMPION not in MODEL_TRANSITIONS[cur] and not override:
                return f"illegal:{cur.value}"
            old_id = champions.get(m.symbol)
            if old_id and old_id != model_id and old_id in models:
                ModelRegistry._apply_status(
                    models, champions, audit, old_id, ModelStatus.RETIRED,
                    actor, f"superseded by {model_id}", retired_reason=RetiredReason.SUPERSEDED
                )
            ModelRegistry._apply_status(
                models, champions, audit, model_id, ModelStatus.CHAMPION,
                actor, reason or "promoted", override=override
            )
            m.provisional = provisional
            return "overridden" if ModelStatus.CHAMPION not in MODEL_TRANSITIONS[cur] else "ok"

        outcome = self._mutate(fn)
        if outcome == "missing":
            logger.error(f"Model {model_id} not found")
            return False
        if isinstance(outcome, str) and outcome.startswith("illegal:"):
            logger.error(
                f"Refused to promote {model_id}: status {outcome.split(':', 1)[1]} cannot "
                f"become champion without an explicit admin override"
            )
            return False
        if outcome == "overridden":
            logger.warning(f"ADMIN OVERRIDE: promoted {model_id} to champion by {actor}: {reason}")
        elif outcome == "ok":
            logger.info(f"Promoted {model_id} to champion for {self._models[model_id].symbol}")
        return True

    def record_evaluation(
        self,
        model_id: str,
        promotion_status: str,
        baseline_summary: Optional[Dict] = None,
        actor: str = "champion_challenger"
    ) -> bool:
        """
        Store a non-promoting evaluation result: pending_* -> PENDING,
        rejected_* -> REJECTED. Champions/retired models only get the
        promotion_status recorded (no status change).
        """
        target = ModelStatus.PENDING if promotion_status.startswith("pending") else (
            ModelStatus.REJECTED if promotion_status.startswith("rejected") else None
        )

        def fn(models, champions, audit):
            if model_id not in models:
                return False
            m = models[model_id]
            m.promotion_status = promotion_status
            if baseline_summary and not m.baseline_summary:
                m.baseline_summary = dict(baseline_summary)
            cur = _status(m.status)
            if target and target in MODEL_TRANSITIONS[cur]:
                ModelRegistry._apply_status(models, champions, audit, model_id, target, actor, promotion_status)
            return True

        return bool(self._mutate(fn))

    def retire(self, model_id: str, reason: str = RetiredReason.MANUAL.value) -> bool:
        """Retire a model (reason: superseded | demoted | manual)."""
        try:
            retired_reason = RetiredReason(reason)
        except ValueError:
            retired_reason = RetiredReason.MANUAL

        def fn(models, champions, audit):
            if model_id not in models:
                return "missing"
            try:
                changed = ModelRegistry._apply_status(
                    models, champions, audit, model_id, ModelStatus.RETIRED,
                    "registry", f"retire:{retired_reason.value}", retired_reason=retired_reason
                )
            except IllegalModelTransition:
                return "noop"  # already retired
            return "ok" if changed else "noop"

        outcome = self._mutate(fn)
        return outcome != "missing"

    def update_forward_metrics(
        self,
        model_id: str,
        forward_metrics: Dict
    ) -> bool:
        """Update forward test metrics for a model."""
        def fn(models, champions, audit):
            if model_id not in models:
                return False
            models[model_id].forward_metrics = dict(forward_metrics)
            return True

        return bool(self._mutate(fn))

    def list_models(
        self,
        symbol: Optional[str] = None,
        status: Optional[str] = None
    ) -> List[ModelMetadata]:
        """List models with optional filters."""
        models = list(self._models.values())

        if symbol:
            models = [m for m in models if m.symbol == symbol]

        if status:
            status_value = status.value if isinstance(status, ModelStatus) else status
            models = [m for m in models if m.status == status_value]

        return sorted(models, key=lambda m: m.created_at, reverse=True)

    def get_model_history(self, symbol: str) -> List[ModelMetadata]:
        """Get all versions of models for a symbol."""
        return [
            m for m in self._models.values()
            if m.symbol == symbol
        ]

    def find_rollback_target(self, symbol: str, exclude: Optional[str] = None) -> Optional[str]:
        """
        Best model to roll back to: a former champion retired as SUPERSEDED
        (or legacy retired-with-promoted_at, which before this change could
        only mean superseded). Never a rejected/pending/registered model and
        never one demoted for degradation. Most recently retired first.
        """
        candidates = []
        for m in self._models.values():
            if m.symbol != symbol or m.model_id == exclude:
                continue
            if _status(m.status) != ModelStatus.RETIRED or m.promoted_at is None:
                continue
            reason = m.retired_reason or RetiredReason.SUPERSEDED.value  # legacy
            if reason != RetiredReason.SUPERSEDED.value:
                continue
            candidates.append(m)
        if not candidates:
            return None
        best = max(candidates, key=lambda m: (m.retired_at or datetime.min,
                                              m.validation_metrics.get("sharpe_ratio", 0)))
        return best.model_id

    def rollback(self, symbol: str, to_version: str, actor: str = "admin_rollback") -> bool:
        """Rollback to a previous model version (explicit override, audited)."""
        model_id = f"{symbol}_{to_version}"

        if model_id not in self._models:
            logger.error(f"Cannot rollback: {model_id} not found")
            return False

        return self.promote_to_champion(
            model_id, override=True, actor=actor, reason=f"rollback to {to_version}"
        )

    # ─── Firestore persistence ──────────────────────────────────────
    
    def _load_from_firestore(self) -> None:
        """Load registry state from Firestore."""
        db = self._get_firestore()
        if not db:
            return
        
        try:
            doc = db.collection("system").document("model_registry").get()
            if not doc.exists:
                logger.info("No registry found in Firestore")
                return
            
            data = doc.to_dict()
            self._load_from_dict(data)
            logger.info(
                f"Registry loaded from Firestore: "
                f"{len(self._models)} models, "
                f"{len(self._champions)} champions"
            )
        except Exception as e:
            logger.error(f"Failed to load registry from Firestore: {e}")
    
    @staticmethod
    def _parse(data: Dict) -> Tuple[Dict[str, ModelMetadata], Dict[str, str]]:
        models: Dict[str, ModelMetadata] = {}
        for model_id, model_data in (data or {}).get("models", {}).items():
            try:
                models[model_id] = ModelMetadata.from_dict(model_data)
            except Exception as e:
                logger.error(f"Error loading model {model_id}: {e}")
        return models, dict((data or {}).get("champions", {}))

    def _load_from_dict(self, data: Dict) -> None:
        """Load registry from a dict (shared by Firestore and local)."""
        self._models, self._champions = self._parse(data)

    # ─── Local file persistence (cache/fallback) ─────────────────
    
    def _save_registry(self) -> None:
        """Save registry to local disk (cache)."""
        data = {
            "models": {k: v.to_dict() for k, v in self._models.items()},
            "champions": self._champions
        }
        
        path = self.storage_path / "registry.json"
        try:
            with open(path, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save local registry: {e}")
    
    def _load_registry(self) -> None:
        """Load registry from local disk (fallback)."""
        path = self.storage_path / "registry.json"
        
        if not path.exists():
            return
        
        try:
            with open(path, 'r') as f:
                data = json.load(f)
            
            self._load_from_dict(data)
            logger.info(f"Registry loaded from local file: {len(self._models)} models")
            
        except Exception as e:
            logger.error(f"Error loading registry: {e}")
