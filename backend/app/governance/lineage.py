"""
Lineage Tracking

Full lineage tracking from data to model to signals.
Answers: "Which exact data trained this model?"
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional
import json
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class ModelLineage:
    """Complete lineage record for a model."""
    model_id: str
    model_version: str
    
    # Data lineage
    dataset_version: str
    feature_version: str
    training_snapshot_id: str
    
    # Training parameters
    training_params: Dict
    training_timestamp: datetime
    
    # Environment
    python_version: str = ""
    package_versions: Dict = field(default_factory=dict)
    
    # Results
    training_metrics: Dict = field(default_factory=dict)
    validation_metrics: Dict = field(default_factory=dict)
    
    def to_dict(self) -> Dict:
        return {
            "model_id": self.model_id,
            "model_version": self.model_version,
            "data_lineage": {
                "dataset": self.dataset_version,
                "features": self.feature_version,
                "snapshot": self.training_snapshot_id
            },
            "training": {
                "params": self.training_params,
                "timestamp": self.training_timestamp.isoformat(),
                "metrics": self.training_metrics
            },
            "validation": self.validation_metrics,
            "environment": {
                "python": self.python_version,
                "packages": self.package_versions
            }
        }
    
    def get_reproducibility_manifest(self) -> Dict:
        """
        Get manifest for reproducing this exact model.
        """
        return {
            "model": self.model_id,
            "version": self.model_version,
            "to_reproduce": {
                "dataset": self.dataset_version,
                "features": self.feature_version,
                "snapshot": self.training_snapshot_id,
                "params": self.training_params
            },
            "expected_metrics": {
                "training": self.training_metrics,
                "validation": self.validation_metrics
            }
        }


class LineageTracker:
    """
    Track complete lineage from data to predictions.
    
    Ensures:
    - Every model can be traced to exact training data
    - Training runs are reproducible
    - Audit trail for all model decisions
    """
    
    def __init__(self, storage_path: str = "lineage"):
        self.storage_path = Path(storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)
        
        self._lineage: Dict[str, ModelLineage] = {}
        self._load_lineage()
    
    def record_training(
        self,
        model_id: str,
        model_version: str,
        dataset_version: str,
        feature_version: str,
        training_snapshot_id: str,
        training_params: Dict,
        training_metrics: Dict,
        validation_metrics: Dict
    ) -> ModelLineage:
        """
        Record lineage for a training run.
        
        This creates an immutable record of what trained this model.
        """
        import sys
        
        lineage = ModelLineage(
            model_id=model_id,
            model_version=model_version,
            dataset_version=dataset_version,
            feature_version=feature_version,
            training_snapshot_id=training_snapshot_id,
            training_params=training_params,
            training_timestamp=datetime.utcnow(),
            python_version=sys.version,
            package_versions=self._get_package_versions(),
            training_metrics=training_metrics,
            validation_metrics=validation_metrics
        )
        
        # Save to storage
        lineage_path = self.storage_path / f"{model_id}.json"
        with open(lineage_path, 'w') as f:
            json.dump(lineage.to_dict(), f, indent=2)
        
        self._lineage[model_id] = lineage
        
        logger.info(f"Recorded lineage for model: {model_id}")
        return lineage
    
    def get_lineage(self, model_id: str) -> Optional[ModelLineage]:
        """Get lineage for a model."""
        return self._lineage.get(model_id)
    
    def trace_prediction(
        self,
        model_id: str,
        prediction_id: str
    ) -> Optional[Dict]:
        """
        Trace a prediction back to its training data.
        
        Returns full lineage from prediction to training data.
        """
        lineage = self.get_lineage(model_id)
        if not lineage:
            return None
        
        return {
            "prediction_id": prediction_id,
            "model_id": model_id,
            "model_version": lineage.model_version,
            "trained_on": {
                "dataset": lineage.dataset_version,
                "features": lineage.feature_version,
                "snapshot": lineage.training_snapshot_id,
                "timestamp": lineage.training_timestamp.isoformat()
            },
            "training_params": lineage.training_params,
            "model_metrics": {
                "training": lineage.training_metrics,
                "validation": lineage.validation_metrics
            }
        }
    
    def compare_lineage(
        self,
        model_id_1: str,
        model_id_2: str
    ) -> Dict:
        """Compare lineage between two models."""
        l1 = self.get_lineage(model_id_1)
        l2 = self.get_lineage(model_id_2)
        
        if not l1 or not l2:
            return {"error": "One or both models not found"}
        
        return {
            "model_1": model_id_1,
            "model_2": model_id_2,
            "differences": {
                "dataset": l1.dataset_version != l2.dataset_version,
                "features": l1.feature_version != l2.feature_version,
                "params": l1.training_params != l2.training_params
            },
            "metrics_comparison": {
                "model_1_val_accuracy": l1.validation_metrics.get("accuracy", 0),
                "model_2_val_accuracy": l2.validation_metrics.get("accuracy", 0)
            }
        }
    
    def get_models_for_dataset(self, dataset_version: str) -> List[str]:
        """Get all models trained on a specific dataset version."""
        return [
            model_id for model_id, lineage in self._lineage.items()
            if lineage.dataset_version == dataset_version
        ]
    
    def _get_package_versions(self) -> Dict:
        """Get versions of key packages."""
        packages = {}
        
        try:
            import torch
            packages["torch"] = torch.__version__
        except ImportError:
            pass
        
        try:
            import xgboost
            packages["xgboost"] = xgboost.__version__
        except ImportError:
            pass
        
        try:
            import pandas
            packages["pandas"] = pandas.__version__
        except ImportError:
            pass
        
        try:
            import numpy
            packages["numpy"] = numpy.__version__
        except ImportError:
            pass
        
        return packages
    
    def _load_lineage(self) -> None:
        """Load existing lineage records."""
        for lineage_file in self.storage_path.glob("*.json"):
            try:
                with open(lineage_file, 'r') as f:
                    data = json.load(f)
                
                model_id = data["model_id"]
                self._lineage[model_id] = ModelLineage(
                    model_id=model_id,
                    model_version=data["model_version"],
                    dataset_version=data["data_lineage"]["dataset"],
                    feature_version=data["data_lineage"]["features"],
                    training_snapshot_id=data["data_lineage"]["snapshot"],
                    training_params=data["training"]["params"],
                    training_timestamp=datetime.fromisoformat(data["training"]["timestamp"]),
                    python_version=data.get("environment", {}).get("python", ""),
                    package_versions=data.get("environment", {}).get("packages", {}),
                    training_metrics=data["training"]["metrics"],
                    validation_metrics=data.get("validation", {})
                )
            except Exception as e:
                logger.warning(f"Failed to load lineage from {lineage_file}: {e}")
