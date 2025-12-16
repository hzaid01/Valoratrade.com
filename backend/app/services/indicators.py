import pandas as pd
import numpy as np
from ta.momentum import RSIIndicator
from ta.trend import MACD, EMAIndicator
from typing import Dict, List, Tuple

def calculate_indicators(df: pd.DataFrame) -> Dict:
    close = df['close']
    high = df['high']
    low = df['low']

    rsi_indicator = RSIIndicator(close=close, window=14)
    rsi = rsi_indicator.rsi().iloc[-1]

    macd_indicator = MACD(close=close)
    macd = macd_indicator.macd().iloc[-1]
    macd_signal = macd_indicator.macd_signal().iloc[-1]
    macd_diff = macd_indicator.macd_diff().iloc[-1]

    ema_9 = EMAIndicator(close=close, window=9).ema_indicator().iloc[-1]
    ema_21 = EMAIndicator(close=close, window=21).ema_indicator().iloc[-1]
    ema_50 = EMAIndicator(close=close, window=50).ema_indicator().iloc[-1]

    return {
        "rsi": float(rsi),
        "macd": {
            "macd": float(macd),
            "signal": float(macd_signal),
            "histogram": float(macd_diff)
        },
        "ema": {
            "ema_9": float(ema_9),
            "ema_21": float(ema_21),
            "ema_50": float(ema_50)
        }
    }

def calculate_support_resistance(df: pd.DataFrame, window: int = 20) -> Dict:
    high = df['high']
    low = df['low']

    resistance = float(high.rolling(window=window).max().iloc[-1])
    support = float(low.rolling(window=window).min().iloc[-1])

    return {
        "resistance": resistance,
        "support": support
    }

def detect_breaker_blocks(df: pd.DataFrame, lookback: int = 50) -> List[Dict]:
    breaker_blocks = []

    for i in range(lookback, len(df)):
        current_high = df['high'].iloc[i]
        current_low = df['low'].iloc[i]
        prev_high = df['high'].iloc[i-1]
        prev_low = df['low'].iloc[i-1]

        if current_low > prev_high:
            breaker_blocks.append({
                "type": "bullish",
                "level": float(prev_high),
                "timestamp": str(df.index[i])
            })

        if current_high < prev_low:
            breaker_blocks.append({
                "type": "bearish",
                "level": float(prev_low),
                "timestamp": str(df.index[i])
            })

    return breaker_blocks[-5:] if len(breaker_blocks) > 5 else breaker_blocks

def prepare_lstm_features(df: pd.DataFrame) -> np.ndarray:
    indicators = calculate_indicators(df)

    features = [
        indicators['rsi'] / 100,
        indicators['macd']['macd'],
        indicators['macd']['signal'],
        indicators['macd']['histogram'],
        indicators['ema']['ema_9'],
        indicators['ema']['ema_21'],
        indicators['ema']['ema_50'],
        float(df['close'].iloc[-1]),
        float(df['volume'].iloc[-1]),
        float(df['high'].iloc[-1] - df['low'].iloc[-1])
    ]

    sequence_length = 60
    feature_matrix = np.tile(features, (sequence_length, 1))

    return feature_matrix


def analyze_indicators(df: pd.DataFrame) -> str:
    """
    Analyze technical indicators to generate a trading signal.
    Based on RSI, EMA crossover, and MACD.
    Returns: 'LONG', 'SHORT', or 'HOLD'
    """
    df = df.copy()
    
    # Calculate indicators
    df['RSI'] = RSIIndicator(close=df['close']).rsi()
    df['EMA20'] = EMAIndicator(close=df['close'], window=20).ema_indicator()
    df['EMA50'] = EMAIndicator(close=df['close'], window=50).ema_indicator()
    df['MACD_DIFF'] = MACD(close=df['close']).macd_diff()
    
    df = df.dropna()
    if len(df) == 0:
        return 'HOLD'
    
    macd_val = df['MACD_DIFF'].iloc[-1]
    rsi = df['RSI'].iloc[-1]
    ema_cross = df['EMA20'].iloc[-1] > df['EMA50'].iloc[-1]
    
    # Trading rules from update.py
    if rsi < 30 and ema_cross and macd_val > 0:
        return 'LONG'
    elif rsi > 70 and not ema_cross and macd_val < 0:
        return 'SHORT'
    else:
        return 'HOLD'

