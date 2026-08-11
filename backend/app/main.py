"""
Production Trading System API

FastAPI application with:
- Strict startup validation (fails loudly on misconfiguration)
- Multi-timeframe data pipeline
- XGBoost model stack
- Capital survival controller
- Forward-only evaluation
- Champion/Challenger model promotion
- /debug/status observability endpoint
"""
import os
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware
from dotenv import load_dotenv

from app.api import signals_router, market_router, backtest_router, admin_router, training_router
from app.routes import user  # Keep existing user routes
from app.config import get_settings, validate_startup_config, ConfigurationError

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load environment
load_dotenv()
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '..', '.env'))

# Get settings
settings = get_settings()

# Initialize rate limiter
limiter = Limiter(key_func=get_remote_address)

# Track startup time
_startup_time = None


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler with strict startup validation."""
    global _startup_time

    logger.info("="*60)
    logger.info("  Starting Production Trading System API v2.1.0")
    logger.info("="*60)

    # ── STRICT STARTUP VALIDATION ─────────────────────────────────
    try:
        warnings = validate_startup_config()
        logger.info("Configuration validation PASSED.")
    except ConfigurationError as e:
        logger.critical(f"STARTUP ABORTED: {e}")
        raise  # This will prevent the app from starting

    _startup_time = datetime.utcnow()

    logger.info("Decision timeframe: 1H (strict)")
    logger.info("Visualization timeframes: 15m, 1H, 4H")
    logger.info("System ready to accept requests.")
    logger.info("="*60)

    yield

    logger.info("Shutting down...")


app = FastAPI(
    title="Production Trading System API",
    description="""
    Capital-safe, leakage-free crypto trading signal system.

    ## Architecture
    - **XGBoost**: Gradient boosting decision model on engineered features
    - **1H Decision Timeframe**: All model logic strictly on hourly candles
    - **Multi-Timeframe Visualization**: 15m, 1H, 4H for frontend charts
    - **Capital Controller**: Kill-switch, exposure limits, drawdown throttling
    - **Champion/Challenger**: Model promotion with gates

    ## Key Principles
    - No data leakage
    - Forward-only evaluation
    - Must beat baselines
    - Capital preservation first
    """,
    version="2.1.0",
    lifespan=lifespan
)

# Rate limiter
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Security headers
app.add_middleware(SecurityHeadersMiddleware)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.api.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler."""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "An internal server error occurred."}
    )


# Include routers
app.include_router(signals_router)
app.include_router(market_router)
app.include_router(backtest_router)
app.include_router(admin_router)
app.include_router(training_router)
app.include_router(user.router)


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "name": "Production Trading System API",
        "version": "2.1.0",
        "architecture": "XGBoost (engineered features)",
        "decision_timeframe": "1H",
        "status": "running"
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "version": "2.1.0",
        "uptime_seconds": (
            (datetime.utcnow() - _startup_time).total_seconds()
            if _startup_time else 0
        ),
        "components": {
            "api": "ok",
            "config": "validated"
        }
    }


@app.get("/debug/status")
async def debug_status():
    """
    Comprehensive debug/status endpoint.

    Shows:
    - System state per symbol
    - Candle counts and training readiness
    - Champion model existence
    - Last training time and result
    - Signal thresholds
    - Environment readiness
    """
    from app.core.system_state import get_state_manager
    from app.core.data_store import DataStore
    from app.models.registry.model_registry import ModelRegistry

    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    result = {
        "timestamp": datetime.utcnow().isoformat(),
        "version": "2.1.0",
        "uptime_seconds": (
            (datetime.utcnow() - _startup_time).total_seconds()
            if _startup_time else 0
        ),
    }

    # Environment readiness
    result["environment"] = {
        "firebase_credentials": bool(
            os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
            or os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
            or os.getenv("K_SERVICE")
        ),
        "binance_api_key": bool(settings.binance.api_key),
        "binance_testnet": settings.binance.testnet,
        "encryption_secret": bool(settings.encryption_secret),
    }

    # Signal thresholds
    result["signal_thresholds"] = {
        "confidence_threshold": settings.signal.confidence_threshold,
        "min_expected_return": settings.signal.min_expected_return,
        "max_volatility_score": settings.signal.max_volatility_score,
    }

    # Per-symbol status
    symbol_status = {}
    try:
        state_manager = get_state_manager()
        data_store = DataStore()
        registry = ModelRegistry()

        for symbol in symbols:
            try:
                # System state
                state = state_manager.get_state(symbol)

                # Data stats
                stats = data_store.get_data_stats(symbol)

                # Champion model
                champion = registry.get_champion(symbol)

                symbol_status[symbol] = {
                    "system_state": state.state.value,
                    "state_updated_at": state.updated_at.isoformat(),
                    "candle_count": stats["candle_count"],
                    "sufficient_for_training": stats["sufficient_for_training"],
                    "first_candle": stats.get("first_candle"),
                    "last_candle": stats.get("last_candle"),
                    "champion_model_exists": champion is not None,
                    "champion_version": champion.version if champion else None,
                    "champion_promoted_at": (
                        champion.promoted_at.isoformat()
                        if champion and champion.promoted_at else None
                    ),
                }
            except Exception as e:
                symbol_status[symbol] = {"error": str(e)}

    except Exception as e:
        symbol_status = {"error": f"Failed to query components: {e}"}

    result["symbols"] = symbol_status

    # Training status (from module-level state)
    try:
        from app.api.training import _training_status
        result["training"] = _training_status
    except Exception:
        result["training"] = {"error": "Could not load training status"}

    return {
        "success": True,
        "data": result
    }


@app.post("/admin/reset-registry")
async def reset_registry():
    """
    Clear stale model registry from Firestore.
    Use this when registry points to non-existent model files.
    After reset, trigger training again.
    """
    from app.firebase_config import get_firestore
    db = get_firestore()
    
    # Clear registry
    try:
        db.collection("system").document("model_registry").delete()
        logger.info("Cleared model_registry from Firestore")
    except Exception as e:
        logger.error(f"Failed to clear registry: {e}")
    
    # Clear model binaries
    try:
        docs = db.collection("model_binaries").stream()
        count = 0
        for doc in docs:
            doc.reference.delete()
            count += 1
        logger.info(f"Cleared {count} model binaries from Firestore")
    except Exception as e:
        logger.error(f"Failed to clear binaries: {e}")
    
    return {
        "success": True,
        "message": "Registry and model binaries cleared from Firestore. Trigger training to create fresh models."
    }

@app.get("/api/architecture")
async def get_architecture():
    """Get system architecture details."""
    return {
        "success": True,
        "data": {
            "model_stack": {
                "representation": "Engineered features (37 causal indicators)",
                "decision": "XGBoost (multi-target)",
                "targets": ["prob_up", "prob_down", "expected_return", "volatility"]
            },
            "timeframes": {
                "decision": "1H (strict)",
                "visualization": ["15m", "1H", "4H"]
            },
            "safety": {
                "capital_controller": True,
                "kill_switch": True,
                "drawdown_throttle": True,
                "baseline_gates": True
            },
            "evaluation": {
                "forward_engine": True,
                "champion_challenger": True,
                "walk_forward_validation": True
            }
        }
    }
