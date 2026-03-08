import logging
from fastapi import APIRouter, HTTPException, Header, Query, Request
from typing import Optional
from slowapi import Limiter
from slowapi.util import get_remote_address
from app.services.binance_service import BinanceService
from app.services.indicators import (
    calculate_indicators,
    calculate_support_resistance,
    detect_breaker_blocks,
    analyze_indicators
)
from app.services.openai_service import OpenAIService
from app.services.trade_calculator import calculate_trade_setup
from app.db import get_db
from app.firebase_config import verify_firebase_token
from app.utils.encryption import decrypt_value

logger = logging.getLogger(__name__)
limiter = Limiter(key_func=get_remote_address)

router = APIRouter(prefix="/api/market", tags=["market"])


def validate_authorization(authorization: Optional[str]) -> Optional[str]:
    """
    Validate and extract Firebase ID token if provided.
    Returns None if no valid authorization is provided.
    """
    if not authorization:
        return None
    
    if not authorization.startswith("Bearer "):
        return None
    
    token = authorization[7:]
    if not token or len(token) < 10:
        return None
    
    return token


def get_user_keys(token: Optional[str]) -> Optional[dict]:
    """
    Get decrypted user API keys from Firestore.
    Returns None if no token or no keys found.
    """
    if not token:
        return None
    
    try:
        # Verify Firebase token
        try:
            decoded_token = verify_firebase_token(token)
            user_id = decoded_token.get('uid')
            if not user_id:
                logger.warning("No user ID in token")
                return None
        except ValueError as e:
            logger.warning(f"Invalid token in get_user_keys: {e}")
            return None
        
        db = get_db()
        doc_ref = db.collection("user_api_keys").document(user_id)
        doc = doc_ref.get()
        result_data = doc.to_dict() if doc.exists else None
        
        if result_data:
            return {
                "binance_api_key": decrypt_value(result_data.get("binance_api_key", "")),
                "binance_secret_key": decrypt_value(result_data.get("binance_secret_key", "")),
                "openai_api_key": decrypt_value(result_data.get("openai_api_key", ""))
            }
        return None
    except Exception as e:
        logger.warning(f"Failed to get user keys: {e}")
        return None


@router.get("/top-coins")
async def get_top_coins(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500, description="Number of coins to return"),
    authorization: Optional[str] = Header(None)
):
    """
    Get top cryptocurrencies by trading volume.
    """
    try:
        # Get user API keys if authenticated
        token = validate_authorization(authorization)
        user_keys = get_user_keys(token)
        
        binance_api_key = user_keys.get("binance_api_key") if user_keys else None
        binance_secret = user_keys.get("binance_secret_key") if user_keys else None

        binance_service = BinanceService(binance_api_key, binance_secret)
        coins = binance_service.get_top_coins(limit)
        return {"success": True, "data": coins}
    except Exception as e:
        logger.error(f"Error fetching top coins: {e}")
        # Fallback to mock data if Binance fails (e.g. strict IP blocks)
        try:
            logger.info("Falling back to mock data")
            mock_service = BinanceService()
            coins = mock_service._get_mock_top_coins(limit)
            return {"success": True, "data": coins}
        except Exception as fallback_error:
            logger.error(f"Fallback failed: {fallback_error}")
            raise HTTPException(status_code=500, detail="Failed to fetch market data")


@router.get("/klines/{symbol}")
async def get_klines(
    request: Request,
    symbol: str, 
    interval: str = "1h",
    limit: int = 500,
    authorization: Optional[str] = Header(None)
):
    """
    Get historical kline data for a symbol.
    """
    try:
        # Validate symbol format
        symbol = symbol.upper().strip()
        if not symbol.endswith("USDT"):
            symbol = f"{symbol}USDT"
            
        # Get user API keys if authenticated
        token = validate_authorization(authorization)
        user_keys = get_user_keys(token)
        
        binance_api_key = user_keys.get("binance_api_key") if user_keys else None
        binance_secret = user_keys.get("binance_secret_key") if user_keys else None
        
        binance_service = BinanceService(binance_api_key, binance_secret)
        df = binance_service.get_klines(symbol, interval, limit)
        
        # Format for lightweight-charts
        data = []
        for index, row in df.iterrows():
            data.append({
                "time": int(index.timestamp()),
                "open": float(row['open']),
                "high": float(row['high']),
                "low": float(row['low']),
                "close": float(row['close']),
                "volume": float(row['volume'])
            })
            
        return {"success": True, "data": data}
    except Exception as e:
        logger.error(f"Error fetching klines for {symbol}: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch kline data")


@router.get("/analyze/{symbol}")
async def analyze_symbol(
    request: Request,
    symbol: str,
    authorization: Optional[str] = Header(None)
):
    """
    Analyze a cryptocurrency symbol and provide trading signals.
    """
    try:
        # Validate symbol format
        symbol = symbol.upper().strip()
        if not symbol or len(symbol) < 3 or len(symbol) > 20:
            raise HTTPException(status_code=400, detail="Invalid symbol format")
        
        if not symbol.endswith("USDT"):
            symbol = f"{symbol}USDT"
        
        # Get user API keys if authenticated
        token = validate_authorization(authorization)
        user_keys = get_user_keys(token)
        
        binance_api_key = user_keys.get("binance_api_key") if user_keys else None
        binance_secret = user_keys.get("binance_secret_key") if user_keys else None
        openai_api_key = user_keys.get("openai_api_key") if user_keys else None

        # Initialize services
        binance_service = BinanceService(binance_api_key, binance_secret)
        df = binance_service.get_klines(symbol)

        if df.empty:
            raise HTTPException(status_code=404, detail="No data available for this symbol")

        current_price = float(df['close'].iloc[-1])

        # Calculate analysis
        indicators = calculate_indicators(df)
        support_resistance = calculate_support_resistance(df)
        breaker_blocks = detect_breaker_blocks(df)

        # NOTE: LSTM model removed — using indicator-based signal only
        
        # Indicator-based signal
        indicator_signal = analyze_indicators(df)

        # AI decision
        openai_service = OpenAIService(openai_api_key)
        ai_decision = openai_service.get_trading_decision(
            symbol,
            indicator_signal,
            indicators,
            support_resistance
        )
        
        # Voting between AI decision and indicator signal
        signals = [s for s in [ai_decision['signal'], indicator_signal] if s in ['LONG', 'SHORT']]
        if signals:
            long_count = signals.count('LONG')
            short_count = signals.count('SHORT')
            if long_count > short_count:
                final_signal = 'LONG'
            elif short_count > long_count:
                final_signal = 'SHORT'
            else:
                final_signal = indicator_signal if indicator_signal in ['LONG', 'SHORT'] else 'HOLD'
        else:
            final_signal = 'HOLD'

        # Trade setup
        trade_setup = calculate_trade_setup(
            final_signal,
            current_price,
            support_resistance['support'],
            support_resistance['resistance']
        )

        logger.info(f"Analysis completed for {symbol}: IND={indicator_signal}, AI={ai_decision['signal']}, FINAL={final_signal}")

        return {
            "success": True,
            "data": {
                "symbol": symbol,
                "current_price": current_price,
                "indicators": indicators,
                "support_resistance": support_resistance,
                "breaker_blocks": breaker_blocks,
                "indicator_signal": indicator_signal,
                "ai_decision": ai_decision,
                "final_signal": final_signal,
                "trade_setup": trade_setup,
                "mode": "live" if (binance_api_key and openai_api_key) else "simulated"
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error analyzing {symbol}: {e}")
        raise HTTPException(status_code=500, detail="Failed to analyze symbol")
