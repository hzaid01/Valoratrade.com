"""
Causal Feature Engineering

All features are computed using ONLY past data to prevent data leakage.
Features are versioned and tracked for reproducibility.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List
import pandas as pd
import numpy as np
from ta.momentum import RSIIndicator, StochasticOscillator
from ta.trend import MACD, EMAIndicator, ADXIndicator, CCIIndicator
from ta.volatility import AverageTrueRange, BollingerBands
from ta.volume import OnBalanceVolumeIndicator

logger = logging.getLogger(__name__)


@dataclass
class FeatureSet:
    """Container for computed features with metadata."""
    features: pd.DataFrame
    feature_names: List[str]
    version: str
    computed_at: datetime = field(default_factory=datetime.utcnow)
    source_symbol: str = ""
    source_timeframe: str = ""
    
    def to_numpy(self) -> np.ndarray:
        """Convert to numpy array for model input."""
        return self.features[self.feature_names].values
    
    def get_latest(self) -> Dict[str, float]:
        """Get latest feature values as dict."""
        if self.features.empty:
            return {}
        return self.features[self.feature_names].iloc[-1].to_dict()


class FeatureEngine:
    """
    Causal feature engineering engine.
    
    CRITICAL: All features are computed using only past data.
    No lookahead bias is allowed.
    """
    
    VERSION = "1.0.0"
    
    # Feature groups
    MOMENTUM_FEATURES = [
        'rsi_14', 'rsi_7', 'stoch_k', 'stoch_d', 'cci_20'
    ]
    
    TREND_FEATURES = [
        'ema_9', 'ema_21', 'ema_50', 'ema_200',
        'macd', 'macd_signal', 'macd_histogram',
        'adx', 'plus_di', 'minus_di'
    ]
    
    VOLATILITY_FEATURES = [
        'atr_14', 'atr_ratio', 'bb_width', 'bb_position',
        'volatility_20', 'volatility_ratio'
    ]
    
    VOLUME_FEATURES = [
        'obv', 'obv_slope', 'volume_sma_ratio', 'vwap_distance'
    ]
    
    MARKET_STRUCTURE_FEATURES = [
        'distance_to_high_20', 'distance_to_low_20',
        'range_position', 'trend_strength'
    ]
    
    PRICE_ACTION_FEATURES = [
        'return_1', 'return_4', 'return_12', 'return_24',
        'high_low_range', 'body_size', 'upper_wick', 'lower_wick'
    ]
    
    def __init__(self):
        self._all_features = (
            self.MOMENTUM_FEATURES +
            self.TREND_FEATURES +
            self.VOLATILITY_FEATURES +
            self.VOLUME_FEATURES +
            self.MARKET_STRUCTURE_FEATURES +
            self.PRICE_ACTION_FEATURES
        )
    
    def compute_features(
        self,
        df: pd.DataFrame,
        symbol: str = "",
        timeframe: str = ""
    ) -> FeatureSet:
        """
        Compute all features for a DataFrame.
        
        Args:
            df: OHLCV DataFrame with columns: open, high, low, close, volume
            symbol: Source symbol for metadata
            timeframe: Source timeframe for metadata
            
        Returns:
            FeatureSet with all computed features
        """
        df = df.copy()
        
        # Ensure lowercase column names
        df.columns = df.columns.str.lower()
        
        # Compute each feature group
        df = self._compute_momentum_features(df)
        df = self._compute_trend_features(df)
        df = self._compute_volatility_features(df)
        df = self._compute_volume_features(df)
        df = self._compute_market_structure_features(df)
        df = self._compute_price_action_features(df)
        
        # Get available features (some may be NaN at start)
        available_features = [f for f in self._all_features if f in df.columns]
        
        return FeatureSet(
            features=df,
            feature_names=available_features,
            version=self.VERSION,
            source_symbol=symbol,
            source_timeframe=timeframe
        )
    
    def compute_features_at_index(
        self,
        df: pd.DataFrame,
        idx: int
    ) -> Dict[str, float]:
        """
        Compute features at a specific index using ONLY past data.
        
        This is the key method for preventing data leakage during
        backtesting and walk-forward validation.
        """
        if idx < 50:  # Need minimum history
            return {}
        
        # Use only data up to and including idx
        historical = df.iloc[:idx + 1].copy()
        feature_set = self.compute_features(historical)
        
        return feature_set.get_latest()
    
    def _compute_momentum_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute momentum indicators."""
        close = df['close']
        high = df['high']
        low = df['low']
        
        # RSI
        df['rsi_14'] = RSIIndicator(close=close, window=14).rsi()
        df['rsi_7'] = RSIIndicator(close=close, window=7).rsi()
        
        # Stochastic
        stoch = StochasticOscillator(high=high, low=low, close=close)
        df['stoch_k'] = stoch.stoch()
        df['stoch_d'] = stoch.stoch_signal()
        
        # CCI
        df['cci_20'] = CCIIndicator(high=high, low=low, close=close, window=20).cci()
        
        return df
    
    def _compute_trend_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute trend indicators."""
        close = df['close']
        high = df['high']
        low = df['low']
        
        # EMAs
        df['ema_9'] = EMAIndicator(close=close, window=9).ema_indicator()
        df['ema_21'] = EMAIndicator(close=close, window=21).ema_indicator()
        df['ema_50'] = EMAIndicator(close=close, window=50).ema_indicator()
        df['ema_200'] = EMAIndicator(close=close, window=200).ema_indicator()
        
        # MACD
        macd = MACD(close=close)
        df['macd'] = macd.macd()
        df['macd_signal'] = macd.macd_signal()
        df['macd_histogram'] = macd.macd_diff()
        
        # ADX
        adx = ADXIndicator(high=high, low=low, close=close)
        df['adx'] = adx.adx()
        df['plus_di'] = adx.adx_pos()
        df['minus_di'] = adx.adx_neg()
        
        return df
    
    def _compute_volatility_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute volatility indicators."""
        close = df['close']
        high = df['high']
        low = df['low']
        
        # ATR
        atr = AverageTrueRange(high=high, low=low, close=close, window=14)
        df['atr_14'] = atr.average_true_range()
        df['atr_ratio'] = df['atr_14'] / close  # Normalized ATR
        
        # Bollinger Bands
        bb = BollingerBands(close=close, window=20, window_dev=2)
        df['bb_width'] = (bb.bollinger_hband() - bb.bollinger_lband()) / bb.bollinger_mavg()
        df['bb_position'] = (close - bb.bollinger_lband()) / (bb.bollinger_hband() - bb.bollinger_lband())
        
        # Historical volatility
        df['volatility_20'] = close.pct_change().rolling(20).std() * np.sqrt(24)  # Annualized hourly
        df['volatility_ratio'] = df['volatility_20'] / df['volatility_20'].rolling(50).mean()
        
        return df
    
    def _compute_volume_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute volume indicators."""
        close = df['close']
        high = df['high']
        low = df['low']
        volume = df['volume']
        
        # OBV
        obv = OnBalanceVolumeIndicator(close=close, volume=volume)
        df['obv'] = obv.on_balance_volume()
        df['obv_slope'] = df['obv'].diff(5) / df['obv'].shift(5)
        
        # Volume SMA ratio
        df['volume_sma_ratio'] = volume / volume.rolling(20).mean()
        
        # VWAP distance (simplified - true VWAP needs intraday reset)
        typical_price = (high + low + close) / 3
        df['vwap_distance'] = (close - typical_price.rolling(20).mean()) / close
        
        return df
    
    def _compute_market_structure_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute market structure features."""
        close = df['close']
        high = df['high']
        low = df['low']
        
        # Distance to recent high/low
        high_20 = high.rolling(20).max()
        low_20 = low.rolling(20).min()
        range_20 = high_20 - low_20
        
        df['distance_to_high_20'] = (high_20 - close) / close
        df['distance_to_low_20'] = (close - low_20) / close
        df['range_position'] = (close - low_20) / range_20
        
        # Trend strength (price position relative to EMAs)
        if 'ema_50' in df.columns and 'ema_200' in df.columns:
            df['trend_strength'] = (df['ema_50'] - df['ema_200']) / df['ema_200']
        else:
            df['trend_strength'] = 0.0
        
        return df
    
    def _compute_price_action_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute price action features."""
        close = df['close']
        open_price = df['open']
        high = df['high']
        low = df['low']
        
        # Returns at different horizons
        df['return_1'] = close.pct_change(1)
        df['return_4'] = close.pct_change(4)
        df['return_12'] = close.pct_change(12)
        df['return_24'] = close.pct_change(24)
        
        # Candle structure
        df['high_low_range'] = (high - low) / close
        df['body_size'] = abs(close - open_price) / close
        df['upper_wick'] = (high - pd.concat([close, open_price], axis=1).max(axis=1)) / close
        df['lower_wick'] = (pd.concat([close, open_price], axis=1).min(axis=1) - low) / close
        
        return df
    
    def get_feature_importance_groups(self) -> Dict[str, List[str]]:
        """Get features grouped by category."""
        return {
            'momentum': self.MOMENTUM_FEATURES,
            'trend': self.TREND_FEATURES,
            'volatility': self.VOLATILITY_FEATURES,
            'volume': self.VOLUME_FEATURES,
            'market_structure': self.MARKET_STRUCTURE_FEATURES,
            'price_action': self.PRICE_ACTION_FEATURES
        }
