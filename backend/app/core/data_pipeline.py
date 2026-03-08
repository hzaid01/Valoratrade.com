"""
Multi-Timeframe Data Pipeline

Handles data ingestion from Binance with strict timeframe separation:
- 15m: Visualization only
- 1H: DECISION TIMEFRAME (all model logic)
- 4H: Visualization + regime context

PRODUCTION MODE: No mock data fallback — fails loudly on Binance errors.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, Optional
import pandas as pd
import numpy as np
from binance.client import Client
from binance.exceptions import BinanceAPIException

from app.config import get_settings, Timeframes

logger = logging.getLogger(__name__)


class DataPipelineError(Exception):
    """Raised when data pipeline cannot fetch data."""
    pass


@dataclass
class CandleData:
    """Container for OHLCV candle data."""
    symbol: str
    timeframe: str
    df: pd.DataFrame
    fetched_at: datetime = field(default_factory=datetime.utcnow)

    @property
    def is_decision_timeframe(self) -> bool:
        """Check if this is 1H decision timeframe data."""
        return self.timeframe == Timeframes.DECISION_1H

    @property
    def latest_close(self) -> float:
        """Get most recent close price."""
        return float(self.df['close'].iloc[-1]) if not self.df.empty else 0.0

    @property
    def latest_timestamp(self) -> datetime:
        """Get most recent candle timestamp."""
        return self.df.index[-1] if not self.df.empty else datetime.utcnow()


class DataPipeline:
    """
    Multi-timeframe data pipeline with strict separation.

    Rules:
    - All model training/inference uses 1H ONLY
    - 15m and 4H are for visualization and context
    - No data leakage: always fetch up to current time only
    - No mock data in production: fails loudly on errors
    """

    KLINE_COLUMNS = [
        'timestamp', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_volume', 'trades', 'taker_buy_base',
        'taker_buy_quote', 'ignore'
    ]

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None
    ):
        settings = get_settings()
        self.api_key = api_key or settings.binance.api_key
        self.api_secret = api_secret or settings.binance.api_secret
        self.testnet = settings.binance.testnet
        self._client: Optional[Client] = None
        self._cache: Dict[str, CandleData] = {}

    @property
    def client(self) -> Client:
        """Lazy-load Binance client. Fails if keys missing."""
        if self._client is None:
            if not self.api_key or not self.api_secret:
                raise DataPipelineError(
                    "Binance API keys not configured. "
                    "Set BINANCE_API_KEY and BINANCE_API_SECRET in .env"
                )
            self._client = Client(self.api_key, self.api_secret, testnet=self.testnet)
            mode = "TESTNET" if self.testnet else "PRODUCTION"
            logger.info(f"Binance client initialized in {mode} mode.")
        return self._client

    async def get_candles(
        self,
        symbol: str,
        timeframe: str = Timeframes.DECISION_1H,
        limit: int = 500,
        use_cache: bool = True,
        cache_ttl_minutes: int = 5,
        end_time: Optional[int] = None
    ) -> CandleData:
        """
        Fetch candle data for a symbol and timeframe.

        Args:
            symbol: Trading pair (e.g., 'BTCUSDT')
            timeframe: One of '15m', '1h', '4h'
            limit: Number of candles to fetch
            use_cache: Whether to use cached data
            cache_ttl_minutes: Cache expiry in minutes

        Returns:
            CandleData with OHLCV DataFrame

        Raises:
            DataPipelineError: If Binance API fails
        """
        symbol = symbol.upper()
        if not symbol.endswith("USDT"):
            symbol = f"{symbol}USDT"

        if timeframe not in Timeframes.ALLOWED:
            raise ValueError(f"Timeframe must be one of {Timeframes.ALLOWED}")

        cache_key = f"{symbol}_{timeframe}"

        # Check cache
        if use_cache and cache_key in self._cache:
            cached = self._cache[cache_key]
            age = datetime.utcnow() - cached.fetched_at
            if age < timedelta(minutes=cache_ttl_minutes):
                logger.debug(f"Using cached data for {cache_key}")
                return cached

        # Fetch fresh data — no mock fallback
        df = await self._fetch_klines(symbol, timeframe, limit, end_time=end_time)
        candle_data = CandleData(
            symbol=symbol,
            timeframe=timeframe,
            df=df
        )
        self._cache[cache_key] = candle_data
        logger.info(f"Fetched {len(df)} candles for {symbol} ({timeframe})")
        return candle_data

    async def get_decision_data(
        self,
        symbol: str,
        limit: int = 500
    ) -> CandleData:
        """
        Get 1H decision timeframe data.

        This is the ONLY data that should be used for model decisions.
        """
        return await self.get_candles(
            symbol=symbol,
            timeframe=Timeframes.DECISION_1H,
            limit=limit
        )

    async def get_multi_timeframe(
        self,
        symbol: str,
        limit: int = 500
    ) -> Dict[str, CandleData]:
        """
        Get data for all timeframes.

        Returns dict with keys: '15m', '1h', '4h'
        """
        result = {}
        for tf in Timeframes.ALLOWED:
            # Adjust limit based on timeframe
            tf_limit = limit
            if tf == Timeframes.VISUALIZATION_15M:
                tf_limit = min(limit * 4, 1000)  # More granular
            elif tf == Timeframes.CONTEXT_4H:
                tf_limit = limit // 4  # Fewer candles needed

            result[tf] = await self.get_candles(symbol, tf, tf_limit)

        return result

    async def _fetch_klines(
        self,
        symbol: str,
        interval: str,
        limit: int,
        end_time: Optional[int] = None
    ) -> pd.DataFrame:
        """
        Fetch raw kline data from Binance.
        Raises DataPipelineError on failure — no mock data fallback.
        
        Args:
            end_time: Optional end time in ms. If set, fetches candles BEFORE this time.
        """
        try:
            kwargs = {
                "symbol": symbol,
                "interval": interval,
                "limit": limit
            }
            if end_time is not None:
                kwargs["endTime"] = end_time
            klines = self.client.get_klines(**kwargs)

            if not klines:
                raise DataPipelineError(
                    f"Binance returned 0 klines for {symbol} ({interval})"
                )

            df = pd.DataFrame(klines, columns=self.KLINE_COLUMNS)
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('timestamp', inplace=True)

            # Convert to float
            for col in ['open', 'high', 'low', 'close', 'volume', 'quote_volume']:
                df[col] = df[col].astype(float)

            logger.info(f"Binance: fetched {len(df)} klines for {symbol} ({interval})")
            return df

        except DataPipelineError:
            raise
        except BinanceAPIException as e:
            raise DataPipelineError(
                f"Binance API error for {symbol} ({interval}): "
                f"code={e.code}, msg={e.message}"
            )
        except Exception as e:
            raise DataPipelineError(
                f"Failed to fetch klines for {symbol} ({interval}): {e}"
            )

    def validate_decision_timeframe(self, candle_data: CandleData) -> None:
        """
        Validate that data is from decision timeframe.

        Raises ValueError if not 1H data.
        """
        if not candle_data.is_decision_timeframe:
            raise ValueError(
                f"Model operations require 1H data. "
                f"Got: {candle_data.timeframe}"
            )

    @staticmethod
    def align_to_hourly(df: pd.DataFrame, source_tf: str) -> pd.DataFrame:
        """
        Align higher-frequency data to hourly boundaries.

        Used for combining 15m visualization data with 1H model data.
        """
        if source_tf == '15m':
            # Resample 15m to 1h
            return df.resample('1h').agg({
                'open': 'first',
                'high': 'max',
                'low': 'min',
                'close': 'last',
                'volume': 'sum',
                'quote_volume': 'sum'
            }).dropna()
        return df
