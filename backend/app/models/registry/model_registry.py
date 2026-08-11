"""
Model Registry

Stores and versions all trained models with full metadata.
Persists to Firestore for Cloud Run compatibility.
Supports rollback and lineage tracking.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional
import json
from pathlib import Path

logger = logging.getLogger(__name__)


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
    
    # Status
    status: str = "registered"  # registered, champion, retired
    promoted_at: Optional[datetime] = None
    retired_at: Optional[datetime] = None
    
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
            "retired_at": self.retired_at.isoformat() if self.retired_at else None
        }


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
        validation_metrics: Dict
    ) -> ModelMetadata:
        """Register a new model."""
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
            validation_metrics=validation_metrics
        )
        
        self._models[model_id] = metadata
        self._save_registry()
        self._save_to_firestore()
        
        logger.info(f"Registered model: {model_id}")
        return metadata
    
    def get(self, model_id: str) -> Optional[ModelMetadata]:
        """Get model metadata by ID."""
        return self._models.get(model_id)
    
    def get_champion(self, symbol: str) -> Optional[ModelMetadata]:
        """Get current champion model for a symbol."""
        model_id = self._champions.get(symbol)
        if model_id:
            return self._models.get(model_id)
        return None
    
    def promote_to_champion(self, model_id: str) -> bool:
        """Promote a model to champion status."""
        if model_id not in self._models:
            logger.error(f"Model {model_id} not found")
            return False
        
        metadata = self._models[model_id]
        symbol = metadata.symbol
        
        # Retire current champion
        if symbol in self._champions:
            old_champion_id = self._champions[symbol]
            old_champion = self._models.get(old_champion_id)
            if old_champion:
                old_champion.status = "retired"
                old_champion.retired_at = datetime.utcnow()
        
        # Promote new champion
        metadata.status = "champion"
        metadata.promoted_at = datetime.utcnow()
        self._champions[symbol] = model_id
        
        self._save_registry()
        self._save_to_firestore()
        logger.info(f"Promoted {model_id} to champion for {symbol}")
        
        return True
    
    def retire(self, model_id: str) -> bool:
        """Retire a model."""
        if model_id not in self._models:
            return False
        
        metadata = self._models[model_id]
        metadata.status = "retired"
        metadata.retired_at = datetime.utcnow()
        
        # Remove from champions if applicable
        symbol = metadata.symbol
        if self._champions.get(symbol) == model_id:
            del self._champions[symbol]
        
        self._save_registry()
        self._save_to_firestore()
        return True
    
    def update_forward_metrics(
        self,
        model_id: str,
        forward_metrics: Dict
    ) -> bool:
        """Update forward test metrics for a model."""
        if model_id not in self._models:
            return False
        
        self._models[model_id].forward_metrics = forward_metrics
        self._save_registry()
        self._save_to_firestore()
        return True
    
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
            models = [m for m in models if m.status == status]
        
        return sorted(models, key=lambda m: m.created_at, reverse=True)
    
    def get_model_history(self, symbol: str) -> List[ModelMetadata]:
        """Get all versions of models for a symbol."""
        return [
            m for m in self._models.values()
            if m.symbol == symbol
        ]
    
    def rollback(self, symbol: str, to_version: str) -> bool:
        """Rollback to a previous model version."""
        model_id = f"{symbol}_{to_version}"
        
        if model_id not in self._models:
            logger.error(f"Cannot rollback: {model_id} not found")
            return False
        
        return self.promote_to_champion(model_id)
    
    # ─── Firestore persistence ──────────────────────────────────────
    
    def _save_to_firestore(self) -> None:
        """Persist registry state to Firestore."""
        db = self._get_firestore()
        if not db:
            return
        
        try:
            data = {
                "models": {k: v.to_dict() for k, v in self._models.items()},
                "champions": self._champions,
                "updated_at": datetime.utcnow().isoformat()
            }
            db.collection("system").document("model_registry").set(data)
            logger.info("Registry saved to Firestore")
        except Exception as e:
            logger.error(f"Failed to save registry to Firestore: {e}")
    
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
    
    def _load_from_dict(self, data: Dict) -> None:
        """Load registry from a dict (shared by Firestore and local)."""
        for model_id, model_data in data.get("models", {}).items():
            try:
                self._models[model_id] = ModelMetadata(
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
                    status=model_data["status"],
                    promoted_at=datetime.fromisoformat(model_data["promoted_at"]) if model_data.get("promoted_at") else None,
                    retired_at=datetime.fromisoformat(model_data["retired_at"]) if model_data.get("retired_at") else None
                )
            except Exception as e:
                logger.error(f"Error loading model {model_id}: {e}")
        
        self._champions = data.get("champions", {})
    
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
