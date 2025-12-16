from binance.client import Client
from binance.exceptions import BinanceAPIException
import pandas as pd
from typing import List, Dict, Optional
from datetime import datetime

class BinanceService:
    def __init__(self, api_key: Optional[str] = None, api_secret: Optional[str] = None):
        self.client = Client(api_key, api_secret) if api_key and api_secret else Client()

    def get_top_coins(self, limit: int = 100) -> List[Dict]:
        try:
            tickers = self.client.get_ticker()

            usdt_pairs = [
                ticker for ticker in tickers
                if ticker['symbol'].endswith('USDT') and
                not any(x in ticker['symbol'] for x in ['UP', 'DOWN', 'BULL', 'BEAR'])
            ]

            sorted_pairs = sorted(
                usdt_pairs,
                key=lambda x: float(x['quoteVolume']),
                reverse=True
            )[:limit]

            result = []
            for ticker in sorted_pairs:
                result.append({
                    "symbol": ticker['symbol'],
                    "price": float(ticker['lastPrice']),
                    "change_24h": float(ticker['priceChangePercent']),
                    "volume": float(ticker['volume']),
                    "quote_volume": float(ticker['quoteVolume'])
                })

            return result
        except BinanceAPIException as e:
            print(f"Binance API error: {e}")
            return self._get_mock_top_coins(limit)
        except Exception as e:
            print(f"Error fetching top coins: {e}")
            return self._get_mock_top_coins(limit)

    def get_klines(self, symbol: str, interval: str = '1h', limit: int = 500) -> pd.DataFrame:
        try:
            klines = self.client.get_klines(symbol=symbol, interval=interval, limit=limit)

            df = pd.DataFrame(klines, columns=[
                'timestamp', 'open', 'high', 'low', 'close', 'volume',
                'close_time', 'quote_volume', 'trades', 'taker_buy_base',
                'taker_buy_quote', 'ignore'
            ])

            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('timestamp', inplace=True)

            for col in ['open', 'high', 'low', 'close', 'volume']:
                df[col] = df[col].astype(float)

            return df
        except BinanceAPIException as e:
            print(f"Binance API error: {e}")
            return self._get_mock_klines()
        except Exception as e:
            print(f"Error fetching klines: {e}")
            return self._get_mock_klines()

    def _get_mock_top_coins(self, limit: int) -> List[Dict]:
        mock_coins = [
            {"symbol": "BTCUSDT", "price": 43250.50, "change_24h": 2.45, "volume": 25000, "quote_volume": 1081263750},
            {"symbol": "ETHUSDT", "price": 2280.75, "change_24h": 1.85, "volume": 150000, "quote_volume": 342112500},
            {"symbol": "SOLUSDT", "price": 98.30, "change_24h": 5.20, "volume": 8500000, "quote_volume": 835550000},
            {"symbol": "BNBUSDT", "price": 315.40, "change_24h": -0.75, "volume": 2000000, "quote_volume": 630800000},
            {"symbol": "XRPUSDT", "price": 0.62, "change_24h": 3.10, "volume": 950000000, "quote_volume": 589000000},
        ]
        return mock_coins[:limit]

    def _get_mock_klines(self) -> pd.DataFrame:
        dates = pd.date_range(end=datetime.now(), periods=500, freq='H')
        data = {
            'open': [43000 + i * 10 for i in range(500)],
            'high': [43100 + i * 10 for i in range(500)],
            'low': [42900 + i * 10 for i in range(500)],
            'close': [43050 + i * 10 for i in range(500)],
            'volume': [1000 + i * 5 for i in range(500)]
        }
        df = pd.DataFrame(data, index=dates)
        return df
