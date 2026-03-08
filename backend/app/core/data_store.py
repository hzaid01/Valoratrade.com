"""
Persistent Data Store

Accumulates candles from Binance into Firestore for training.
Implements data freshness checks and minimum requirements gating.
"""
import logging
from datetime import datetime
from typing import Dict, List, Optional
import pandas as pd
from google.cloud.firestore import Query

from app.firebase_config import get_firestore
from app.core.data_pipeline import DataPipeline
from app.core.system_state import get_state_manager, SystemState

logger = logging.getLogger(__name__)


class DataIngestionError(Exception):
    """Raised when data ingestion fails."""
    pass


class InsufficientDataError(Exception):
    """Raised when not enough data for training."""
    pass


class DataStore:
    """
    Persistent candle storage with Firestore backend.
    
    Responsibilities:
    - Accumulate candles from Binance
    - Deduplicate by timestamp  
    - Track data freshness
    - Provide training data with minimum requirements
    """
    
    MIN_CANDLES_FOR_TRAINING = 500
    
    def __init__(self):
        self.db = get_firestore()
        self.data_pipeline = DataPipeline()
        self.state_manager = get_state_manager()
    
    async def ingest_latest_candles(
        self,
        symbol: str,
        timeframe: str = "1h",
        limit: int = 500
    ) -> Dict:
        """
        Fetch and store latest candles, avoiding duplicates.
        
        Updates system state based on data accumulation.
        
        Returns:
            Dict with ingestion stats
            
        Raises:
            DataIngestionError: If Binance returns no data
        """
        collection = self.db.collection(f'candles_{timeframe}')
        
        # Get last stored timestamp
        last_doc = (
            collection
            .where("symbol", "==", symbol)
            .order_by("timestamp", direction=Query.DESCENDING)
            .limit(1)
            .stream()
        )
        
        last_timestamp = None
        for doc in last_doc:
            last_timestamp = doc.to_dict().get("timestamp")
        
        # Fetch new candles
        try:
            candle_data = await self.data_pipeline.get_candles(
                symbol=symbol,
                timeframe=timeframe,
                limit=limit,
                use_cache=False
            )
        except Exception as e:
            raise DataIngestionError(f"Binance fetch failed for {symbol}: {e}")
        
        df = candle_data.df
        
        if len(df) == 0:
            raise DataIngestionError(f"Binance returned 0 candles for {symbol}")
        
        # Filter to only new candles
        original_count = len(df)
        if last_timestamp:
            # Convert Firestore DatetimeWithNanoseconds to pandas-compatible
            # Firestore stores tz-aware timestamps, but Binance klines are tz-naive
            last_ts = pd.Timestamp(last_timestamp)
            if last_ts.tzinfo is not None:
                last_ts = last_ts.tz_localize(None)
            df = df[df.index > last_ts]
        
        # Store each candle
        stored_count = 0
        errors = []
        
        for timestamp, row in df.iterrows():
            doc_id = f"{symbol}_{timestamp.isoformat()}"
            try:
                collection.document(doc_id).set({
                    "symbol": symbol,
                    "timestamp": timestamp,
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row["volume"]),
                    "stored_at": datetime.utcnow()
                })
                stored_count += 1
            except Exception as e:
                errors.append(f"{doc_id}: {e}")
                logger.error(f"Failed to store candle {doc_id}: {e}")
        
        # Update system state based on data count
        stats = self.get_data_stats(symbol, timeframe)
        self._update_state_for_data(symbol, stats["candle_count"])
        
        logger.info(f"Stored {stored_count}/{len(df)} new candles for {symbol}")
        
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "new_candles": stored_count,
            "total_fetched": original_count,
            "total_stored": stats["candle_count"],
            "last_timestamp": df.index[-1].isoformat() if len(df) > 0 else None,
            "errors": errors if errors else None
        }

    async def backfill_historical(
        self,
        symbol: str,
        timeframe: str = "1h",
        limit: int = 1000
    ) -> Dict:
        """
        Fetch older candles BEFORE the earliest stored timestamp.
        
        This fills in historical data by fetching candles ending
        before the earliest candle we already have.
        """
        collection = self.db.collection(f'candles_{timeframe}')
        
        # Get earliest stored timestamp
        first_doc = (
            collection
            .where("symbol", "==", symbol)
            .order_by("timestamp", direction=Query.ASCENDING)
            .limit(1)
            .stream()
        )
        
        first_timestamp = None
        for doc in first_doc:
            first_timestamp = doc.to_dict().get("timestamp")
        
        if not first_timestamp:
            # No data at all — just do a normal fetch
            return await self.ingest_latest_candles(symbol, timeframe, limit)
        
        # Convert to milliseconds for Binance API
        first_ts = pd.Timestamp(first_timestamp)
        if first_ts.tzinfo is not None:
            first_ts = first_ts.tz_localize(None)
        end_time_ms = int(first_ts.timestamp() * 1000)
        
        logger.info(
            f"[{symbol}] Backfilling historical data before {first_ts} "
            f"(fetching up to {limit} candles)"
        )
        
        # Fetch candles ending before our earliest
        try:
            candle_data = await self.data_pipeline.get_candles(
                symbol=symbol,
                timeframe=timeframe,
                limit=limit,
                use_cache=False,
                end_time=end_time_ms
            )
        except Exception as e:
            raise DataIngestionError(f"Historical backfill failed for {symbol}: {e}")
        
        df = candle_data.df
        if len(df) == 0:
            logger.info(f"[{symbol}] No older historical data available")
            return {"symbol": symbol, "new_candles": 0, "total_stored": self.get_data_stats(symbol, timeframe)["candle_count"]}
        
        # Filter out any that overlap with stored data
        df = df[df.index < first_ts]
        
        # Store each candle
        stored_count = 0
        for timestamp, row in df.iterrows():
            doc_id = f"{symbol}_{timestamp.isoformat()}"
            try:
                collection.document(doc_id).set({
                    "symbol": symbol,
                    "timestamp": timestamp,
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row["volume"]),
                    "stored_at": datetime.utcnow()
                })
                stored_count += 1
            except Exception as e:
                logger.error(f"Failed to store historical candle {doc_id}: {e}")
        
        stats = self.get_data_stats(symbol, timeframe)
        self._update_state_for_data(symbol, stats["candle_count"])
        
        logger.info(f"[{symbol}] Backfilled {stored_count} historical candles (total: {stats['candle_count']})")
        
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "new_candles": stored_count,
            "total_fetched": len(candle_data.df),
            "total_stored": stats["candle_count"],
            "earliest_timestamp": df.index[0].isoformat() if len(df) > 0 else None
        }

    
    def get_training_data(
        self,
        symbol: str,
        timeframe: str = "1h",
        min_candles: int = None,
        max_candles: int = 5000
    ) -> pd.DataFrame:
        """
        Get accumulated candles for training.
        
        Raises:
            InsufficientDataError: If not enough candles
        """
        min_candles = min_candles or self.MIN_CANDLES_FOR_TRAINING
        collection = self.db.collection(f'candles_{timeframe}')
        
        docs = (
            collection
            .where("symbol", "==", symbol)
            .order_by("timestamp", direction=Query.DESCENDING)
            .limit(max_candles)
            .stream()
        )
        
        rows = []
        for doc in docs:
            d = doc.to_dict()
            rows.append({
                "timestamp": d["timestamp"],
                "open": d["open"],
                "high": d["high"],
                "low": d["low"],
                "close": d["close"],
                "volume": d["volume"]
            })
        
        if len(rows) < min_candles:
            raise InsufficientDataError(
                f"Insufficient data for {symbol}: {len(rows)} < {min_candles} required"
            )
        
        df = pd.DataFrame(rows)
        df.set_index("timestamp", inplace=True)
        df.sort_index(inplace=True)  # Oldest first
        
        logger.info(f"Loaded {len(df)} candles for {symbol} training")
        return df
    
    def get_data_stats(self, symbol: str, timeframe: str = "1h") -> Dict:
        """Get data statistics for a symbol."""
        collection = self.db.collection(f'candles_{timeframe}')
        
        docs = (
            collection
            .where("symbol", "==", symbol)
            .order_by("timestamp", direction=Query.DESCENDING)
            .limit(10000)
            .stream()
        )
        
        count = 0
        first_ts = None
        last_ts = None
        
        for doc in docs:
            d = doc.to_dict()
            ts = d["timestamp"]
            count += 1
            if first_ts is None or ts < first_ts:
                first_ts = ts
            if last_ts is None or ts > last_ts:
                last_ts = ts
        
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "candle_count": count,
            "first_candle": first_ts.isoformat() if first_ts else None,
            "last_candle": last_ts.isoformat() if last_ts else None,
            "sufficient_for_training": count >= self.MIN_CANDLES_FOR_TRAINING,
            "data_hours": count if timeframe == "1h" else None
        }
    
    def _update_state_for_data(self, symbol: str, candle_count: int) -> None:
        """Update system state based on data accumulation."""
        current = self.state_manager.get_state(symbol)
        
        if current.state == SystemState.NO_DATA and candle_count > 0:
            self.state_manager.transition(
                symbol=symbol,
                new_state=SystemState.COLLECTING_DATA,
                actor="data_store",
                reason=f"First candles ingested: {candle_count}"
            )
        
        elif current.state == SystemState.COLLECTING_DATA:
            if candle_count >= self.MIN_CANDLES_FOR_TRAINING:
                self.state_manager.transition(
                    symbol=symbol,
                    new_state=SystemState.READY_FOR_TRAINING,
                    actor="data_store",
                    reason=f"Sufficient data: {candle_count} >= {self.MIN_CANDLES_FOR_TRAINING}"
                )
