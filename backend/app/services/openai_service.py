from openai import OpenAI
from typing import Dict, Optional
import time
import random
import threading
import logging

logger = logging.getLogger(__name__)

# Rate limiting for OpenAI API calls
_openai_last_call = 0
_OPENAI_MIN_INTERVAL = 1.2
_openai_lock = threading.Lock()


class OpenAIService:
    def __init__(self, api_key: Optional[str] = None):
        self.client = OpenAI(api_key=api_key) if api_key else None

    def get_trading_decision(
        self,
        symbol: str,
        lstm_signal: str,
        indicators: Dict,
        support_resistance: Dict,
        max_retries: int = 5
    ) -> Dict:
        if not self.client:
            return self._get_mock_decision(lstm_signal, indicators)

        global _openai_last_call
        
        try:
            prompt = f"""
            Analyze the following trading data for {symbol}:

            LSTM Model Signal: {lstm_signal}

            Technical Indicators:
            - RSI: {indicators['rsi']:.2f}
            - MACD: {indicators['macd']['macd']:.2f}
            - MACD Signal: {indicators['macd']['signal']:.2f}
            - EMA 9: {indicators['ema']['ema_9']:.2f}
            - EMA 21: {indicators['ema']['ema_21']:.2f}
            - EMA 50: {indicators['ema']['ema_50']:.2f}

            Support/Resistance:
            - Support: {support_resistance['support']:.2f}
            - Resistance: {support_resistance['resistance']:.2f}

            Provide a trading decision (LONG, SHORT, or HOLD) with a brief explanation.
            Format your response as: DECISION: [signal] | REASON: [explanation]
            """

            # Rate limiting
            with _openai_lock:
                elapsed = time.time() - _openai_last_call
                if elapsed < _OPENAI_MIN_INTERVAL:
                    time.sleep(_OPENAI_MIN_INTERVAL - elapsed)
                _openai_last_call = time.time()

            # Retry logic with exponential backoff
            backoff_base = 1.5
            for attempt in range(max_retries):
                try:
                    response = self.client.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=[
                            {"role": "system", "content": "You are an expert cryptocurrency trading analyst."},
                            {"role": "user", "content": prompt}
                        ],
                        max_tokens=150,
                        temperature=0.0
                    )

                    result = response.choices[0].message.content

                    if "LONG" in result:
                        signal = "LONG"
                    elif "SHORT" in result:
                        signal = "SHORT"
                    else:
                        signal = "HOLD"

                    reason = result.split("REASON:")[-1].strip() if "REASON:" in result else result

                    return {
                        "signal": signal,
                        "reason": reason
                    }
                except Exception as e:
                    if "429" in str(e) or "rate" in str(e).lower():
                        wait = min(30, backoff_base ** attempt + random.random())
                        logger.warning(f"OpenAI rate limited. Waiting {wait:.1f}s (attempt {attempt+1})")
                        time.sleep(wait)
                        continue
                    raise

            logger.warning("OpenAI: exhausted retries, using mock decision")
            return self._get_mock_decision(lstm_signal, indicators)

        except Exception as e:
            logger.error(f"OpenAI API error: {e}")
            return self._get_mock_decision(lstm_signal, indicators)

    def _get_mock_decision(self, lstm_signal: str, indicators: Dict) -> Dict:
        rsi = indicators['rsi']
        macd_histogram = indicators['macd']['histogram']

        if lstm_signal == "LONG" and rsi < 70 and macd_histogram > 0:
            return {
                "signal": "LONG",
                "reason": "LSTM model shows bullish signal, RSI not overbought, MACD positive momentum."
            }
        elif lstm_signal == "SHORT" and rsi > 30 and macd_histogram < 0:
            return {
                "signal": "SHORT",
                "reason": "LSTM model shows bearish signal, RSI not oversold, MACD negative momentum."
            }
        else:
            return {
                "signal": "HOLD",
                "reason": "Mixed signals from indicators, waiting for clearer trend confirmation."
            }
