"""
Training Run Contract

Every training run produces a complete set of artifacts or is marked failed.
Implements lineage enforcement and metric validation.
"""
import hashlib
import json
import logging
import math
import subprocess
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class RunManifest:
    """Lineage manifest for a training run."""
    run_id: str
    symbol: str
    created_at: str
    lineage: Dict
    sealed: bool = False
    
    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass 
class TrainingRunResult:
    """Complete result of a training run."""
    run_id: str
    symbol: str
    status: str  # completed, failed, invalid, partial
    started_at: datetime
    completed_at: Optional[datetime]
    duration_seconds: Optional[float]
    
    # Lineage
    dataset_snapshot_id: str
    feature_version: str
    code_version: str
    training_data_rows: int
    
    # Metrics
    training_metrics: Dict
    validation_metrics: Dict
    baseline_results: Dict
    
    # Errors
    errors: List[str]
    
    def to_metrics_json(self) -> Dict:
        return {
            "run_id": self.run_id,
            "symbol": self.symbol,
            "status": self.status,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_seconds": self.duration_seconds,
            "lineage": {
                "dataset_snapshot_id": self.dataset_snapshot_id,
                "feature_version": self.feature_version,
                "code_version": self.code_version,
                "training_data_rows": self.training_data_rows
            },
            "training_metrics": self.training_metrics,
            "validation_metrics": self.validation_metrics,
            "errors": self.errors
        }


class TrainingRun:
    """
    Manages a single training run with contract enforcement.
    
    Required outputs:
    - manifest.json (lineage)
    - metrics.json (training results)
    - baselines.json (baseline comparisons)
    - forward_metrics.json (placeholder)
    - model artifacts
    """
    
    REQUIRED_FILES = [
        "manifest.json",
        "metrics.json",
        "baselines.json",
        "forward_metrics.json"
    ]
    
    def __init__(self, run_id: str, symbol: str, runs_dir: str = "runs"):
        self.run_id = run_id
        self.symbol = symbol
        self.runs_dir = Path(runs_dir)
        self.run_path = self.runs_dir / run_id
        self.run_path.mkdir(parents=True, exist_ok=True)
        
        self.started_at = datetime.utcnow()
        self.errors: List[str] = []
        self.status = "running"
        self.manifest: Optional[RunManifest] = None
    
    def get_code_version(self) -> str:
        """Get current code version from git or fallback."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                timeout=5,
                cwd=str(Path(__file__).parent.parent.parent)
            )
            if result.returncode == 0:
                return f"git:{result.stdout.strip()}"
        except Exception:
            pass
        return "version:2.0.0"
    
    def compute_dataset_snapshot_id(self, df) -> str:
        """Compute SHA256 hash of dataset."""
        data_str = df.to_json()
        return f"sha256:{hashlib.sha256(data_str.encode()).hexdigest()[:16]}"
    
    def create_manifest(
        self,
        dataset_snapshot_id: str,
        feature_version: str,
        training_data_rows: int
    ) -> RunManifest:
        """Create and save manifest BEFORE training starts."""
        self.manifest = RunManifest(
            run_id=self.run_id,
            symbol=self.symbol,
            created_at=self.started_at.isoformat(),
            lineage={
                "dataset_snapshot_id": dataset_snapshot_id,
                "feature_version": feature_version,
                "code_version": self.get_code_version(),
                "training_data_rows": training_data_rows
            },
            sealed=False
        )
        
        path = self.run_path / "manifest.json"
        with open(path, 'w') as f:
            json.dump(self.manifest.to_dict(), f, indent=2)
        
        logger.info(f"Created manifest for run {self.run_id}")
        return self.manifest
    
    def seal_manifest(self) -> None:
        """Seal manifest - no more lineage changes allowed."""
        if not self.manifest:
            raise RuntimeError("Cannot seal - manifest not created")
        
        self.manifest.sealed = True
        
        path = self.run_path / "manifest.json"
        with open(path, 'w') as f:
            json.dump(self.manifest.to_dict(), f, indent=2)
        
        logger.info(f"Sealed manifest for run {self.run_id}")
    
    def validate_metrics(self, metrics: Dict) -> Tuple[bool, List[str]]:
        """Validate that all metrics are finite numbers."""
        errors = []
        
        def check_value(key: str, value):
            if isinstance(value, float):
                if math.isnan(value):
                    errors.append(f"{key} is NaN")
                if math.isinf(value):
                    errors.append(f"{key} is Inf")
            elif isinstance(value, dict):
                for k, v in value.items():
                    check_value(f"{key}.{k}", v)
        
        for key, value in metrics.items():
            check_value(key, value)
        
        return len(errors) == 0, errors
    
    def save_metrics(self, result: TrainingRunResult) -> None:
        """Save metrics.json."""
        path = self.run_path / "metrics.json"
        with open(path, 'w') as f:
            json.dump(result.to_metrics_json(), f, indent=2)
        logger.info(f"Saved metrics to {path}")
    
    def save_baselines(self, baseline_results: Dict, dataset_snapshot_id: str) -> None:
        """Save baselines.json with all baseline strategy results."""
        best_name = None
        best_sharpe = float('-inf')
        
        baselines_data = {}
        for name, result in baseline_results.items():
            # Handle both BaselineResult objects and dicts
            if hasattr(result, 'metrics'):
                metrics = result.metrics
            else:
                metrics = result
            
            baselines_data[name] = {
                "sharpe_ratio": metrics.get("sharpe_ratio", 0),
                "total_return": metrics.get("total_return", 0),
                "max_drawdown": metrics.get("max_drawdown", 0),
                "profit_factor": metrics.get("profit_factor", 0),
                "win_rate": metrics.get("win_rate", 0),
                "trade_count": metrics.get("trade_count", 0)
            }
            
            if baselines_data[name]["sharpe_ratio"] > best_sharpe:
                best_sharpe = baselines_data[name]["sharpe_ratio"]
                best_name = name
        
        output = {
            "run_id": self.run_id,
            "dataset_snapshot_id": dataset_snapshot_id,
            "computed_at": datetime.utcnow().isoformat(),
            "baselines": baselines_data,
            "best_baseline": best_name,
            "best_sharpe": best_sharpe
        }
        
        path = self.run_path / "baselines.json"
        with open(path, 'w') as f:
            json.dump(output, f, indent=2)
        logger.info(f"Saved baselines to {path}")
    
    def save_forward_metrics_placeholder(self) -> None:
        """Save empty forward_metrics.json (filled later by resolution job)."""
        output = {
            "run_id": self.run_id,
            "status": "pending",
            "message": "Forward metrics computed after 24h holding period",
            "created_at": datetime.utcnow().isoformat()
        }
        
        path = self.run_path / "forward_metrics.json"
        with open(path, 'w') as f:
            json.dump(output, f, indent=2)
    
    def get_model_paths(self) -> Dict[str, Path]:
        """Get paths for model artifacts."""
        return {
            "patchtst": self.run_path / "patchtst.pt",
            "xgboost": self.run_path / "xgboost_models.joblib"
        }
    
    def mark_failed(self, error: str, stage: str) -> None:
        """Mark run as failed at a specific stage."""
        self.status = "failed"
        self.errors.append(f"[{stage}] {error}")
        
        partial = {
            "run_id": self.run_id,
            "status": "failed",
            "failed_at": datetime.utcnow().isoformat(),
            "failed_stage": stage,
            "errors": self.errors
        }
        
        path = self.run_path / "metrics.json"
        with open(path, 'w') as f:
            json.dump(partial, f, indent=2)
        
        logger.error(f"Run {self.run_id} failed at {stage}: {error}")
    
    def mark_invalid(self, validation_errors: List[str]) -> None:
        """Mark run as invalid (completed but bad metrics)."""
        self.status = "invalid"
        self.errors.extend(validation_errors)
        logger.warning(f"Run {self.run_id} marked invalid: {validation_errors}")
    
    def verify_complete(self) -> Tuple[bool, List[str]]:
        """Verify all required files exist."""
        missing = []
        for filename in self.REQUIRED_FILES:
            if not (self.run_path / filename).exists():
                missing.append(filename)
        
        return len(missing) == 0, missing
    
    def finalize(
        self,
        training_metrics: Dict,
        validation_metrics: Dict,
        baseline_results: Dict
    ) -> TrainingRunResult:
        """Finalize the run and create result object."""
        completed_at = datetime.utcnow()
        duration = (completed_at - self.started_at).total_seconds()
        
        # Validate metrics
        valid, metric_errors = self.validate_metrics(training_metrics)
        if not valid:
            self.mark_invalid(metric_errors)
        
        valid, val_errors = self.validate_metrics(validation_metrics)
        if not valid:
            self.mark_invalid(val_errors)
        
        if self.status not in ["failed", "invalid"]:
            self.status = "completed"
        
        result = TrainingRunResult(
            run_id=self.run_id,
            symbol=self.symbol,
            status=self.status,
            started_at=self.started_at,
            completed_at=completed_at,
            duration_seconds=duration,
            dataset_snapshot_id=self.manifest.lineage["dataset_snapshot_id"] if self.manifest else "",
            feature_version=self.manifest.lineage["feature_version"] if self.manifest else "",
            code_version=self.manifest.lineage["code_version"] if self.manifest else "",
            training_data_rows=self.manifest.lineage["training_data_rows"] if self.manifest else 0,
            training_metrics=training_metrics,
            validation_metrics=validation_metrics,
            baseline_results=baseline_results,
            errors=self.errors
        )
        
        # Save all artifacts
        self.save_metrics(result)
        self.save_baselines(baseline_results, result.dataset_snapshot_id)
        self.save_forward_metrics_placeholder()
        
        # Verify completeness
        complete, missing = self.verify_complete()
        if not complete:
            logger.warning(f"Run {self.run_id} missing files: {missing}")
        
        return result
