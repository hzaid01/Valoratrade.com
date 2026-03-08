"""
Scheduled Retraining Job

Weekly retraining job that:
1. Collects new data
2. Generates new targets
3. Trains new model
4. Evaluates against baselines
5. Runs champion/challenger evaluation
6. Promotes only if gates pass
"""
import logging
from datetime import datetime
from typing import Dict, List

from app.core.data_pipeline import DataPipeline
from app.core.feature_engine import FeatureEngine
from app.core.target_engineer import TargetEngineer
from app.models.training.trainer import ModelTrainer
from app.models.registry.model_registry import ModelRegistry
from app.models.registry.champion_challenger import ChampionChallenger
from app.evaluation.baselines import BaselineStrategies
from app.evaluation.forward_engine import ForwardEngine
from app.governance.versioning import DatasetVersioning
from app.governance.lineage import LineageTracker
from app.config import get_settings

logger = logging.getLogger(__name__)


class RetrainJob:
    """
    Orchestrates the retraining pipeline.
    
    Run schedule: Weekly (configurable)
    """
    
    def __init__(self):
        self.data_pipeline = DataPipeline()
        self.feature_engine = FeatureEngine()
        self.target_engineer = TargetEngineer()
        self.trainer = ModelTrainer()
        self.registry = ModelRegistry()
        self.baselines = BaselineStrategies()
        self.forward_engine = ForwardEngine()
        self.dataset_versioning = DatasetVersioning()
        self.lineage_tracker = LineageTracker()
        self.champion_challenger = ChampionChallenger(self.registry, self.baselines)
        self.settings = get_settings()
    
    async def run(
        self,
        symbols: List[str],
        force: bool = False
    ) -> Dict:
        """
        Run retraining for specified symbols.
        
        Args:
            symbols: List of symbols to retrain
            force: Force retraining even if not needed
            
        Returns:
            Results for each symbol
        """
        results = {}
        
        for symbol in symbols:
            try:
                result = await self._retrain_symbol(symbol, force)
                results[symbol] = result
            except Exception as e:
                logger.error(f"Retraining failed for {symbol}: {e}")
                results[symbol] = {"status": "failed", "error": str(e)}
        
        return results
    
    async def _retrain_symbol(self, symbol: str, force: bool) -> Dict:
        """Retrain for a single symbol."""
        logger.info(f"Starting retraining for {symbol}")
        
        # Step 1: Fetch latest data
        candle_data = await self.data_pipeline.get_decision_data(symbol, limit=2000)
        df = candle_data.df
        
        if len(df) < 500:
            return {"status": "skipped", "reason": "insufficient_data"}
        
        # Step 2: Create dataset version
        dataset_version = self.dataset_versioning.create_version(
            df=df,
            symbol=symbol,
            timeframe="1h",
            metadata={"source": "binance", "job": "retrain"}
        )
        
        # Step 3: Compute features and create version
        _ = self.feature_engine.compute_features(df, symbol)
        # Feature versioning would happen here
        
        # Step 4: Run baselines
        baseline_results = self.baselines.run_all(df)
        
        # Step 5: Train new model
        training_result = self.trainer.train(
            df=df,
            symbol=symbol,
            model_version=f"v{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
        )
        
        # Step 6: Register model
        model_metadata = self.registry.register(
            version=training_result.model_version,
            symbol=symbol,
            patch_tst_path=training_result.patch_tst_path,
            xgboost_path=training_result.xgboost_path,
            dataset_version=dataset_version.version,
            feature_version="v1.0.0",  # Placeholder
            training_snapshot=dataset_version.version,
            training_metrics=training_result.metrics,
            validation_metrics=training_result.validation_metrics
        )
        
        # Step 7: Record lineage
        self.lineage_tracker.record_training(
            model_id=model_metadata.model_id,
            model_version=training_result.model_version,
            dataset_version=dataset_version.version,
            feature_version="v1.0.0",
            training_snapshot_id=dataset_version.version,
            training_params=training_result.config,
            training_metrics=training_result.metrics,
            validation_metrics=training_result.validation_metrics
        )
        
        # Step 8: Champion/Challenger evaluation
        promotion_result = self.champion_challenger.evaluate_challenger(
            challenger_id=model_metadata.model_id,
            baseline_results=baseline_results
        )
        
        return {
            "status": "completed",
            "model_id": model_metadata.model_id,
            "version": training_result.model_version,
            "metrics": training_result.metrics,
            "validation": training_result.validation_metrics,
            "promotion": promotion_result.to_dict()
        }


async def main():
    """Entry point for scheduled job."""
    logger.info("Starting weekly retraining job")
    
    job = RetrainJob()
    
    # Default symbols to retrain
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    
    results = await job.run(symbols)
    
    logger.info(f"Retraining complete: {results}")
    return results


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
