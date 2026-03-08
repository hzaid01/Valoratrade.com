"""
Production Trading System Configuration

All configuration loaded from environment variables with sensible defaults.
Strict validation at startup — fails loudly on missing critical config.
"""
import os
import sys
import logging
from dataclasses import dataclass, field
from typing import Optional, List
from functools import lru_cache
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


class ConfigurationError(Exception):
    """Raised when required configuration is missing or invalid."""
    pass


@dataclass(frozen=True)
class DatabaseConfig:
    """Firebase/Firestore configuration."""
    project_id: str = field(default_factory=lambda: os.getenv("FIREBASE_PROJECT_ID", ""))
    credentials_path: str = field(default_factory=lambda: os.getenv("GOOGLE_APPLICATION_CREDENTIALS", ""))


@dataclass(frozen=True)
class BinanceConfig:
    """Binance API configuration."""
    api_key: Optional[str] = field(default_factory=lambda: os.getenv("BINANCE_API_KEY"))
    api_secret: Optional[str] = field(default_factory=lambda: os.getenv("BINANCE_API_SECRET"))
    testnet: bool = field(default_factory=lambda: os.getenv("BINANCE_TESTNET", "false").lower() == "true")


@dataclass(frozen=True)
class ModelConfig:
    """Model training and inference configuration."""
    # PatchTST settings
    patch_len: int = 16
    stride: int = 8
    d_model: int = 64
    n_heads: int = 4
    n_layers: int = 2
    seq_len: int = 168  # 1 week of hourly candles

    # XGBoost settings
    xgb_n_estimators: int = 200
    xgb_max_depth: int = 6
    xgb_learning_rate: float = 0.05

    # Training settings
    walk_forward_splits: int = 5
    train_ratio: float = 0.7
    val_ratio: float = 0.15
    test_ratio: float = 0.15


@dataclass(frozen=True)
class CapitalConfig:
    """Capital controller configuration."""
    max_exposure: float = field(default_factory=lambda: float(os.getenv("MAX_EXPOSURE", "0.30")))
    max_concurrent_trades: int = field(default_factory=lambda: int(os.getenv("MAX_CONCURRENT", "3")))
    max_drawdown: float = field(default_factory=lambda: float(os.getenv("MAX_DRAWDOWN", "0.15")))
    correlation_limit: float = field(default_factory=lambda: float(os.getenv("CORRELATION_LIMIT", "0.70")))
    confidence_floor: float = field(default_factory=lambda: float(os.getenv("CONFIDENCE_FLOOR", "0.60")))
    drawdown_throttle_threshold: float = 0.80  # Start throttling at 80% of max DD


@dataclass(frozen=True)
class SignalConfig:
    """Signal generation thresholds — configurable via env vars."""
    confidence_threshold: float = field(
        default_factory=lambda: float(os.getenv("SIGNAL_CONFIDENCE_THRESHOLD", "0.60"))
    )
    min_expected_return: float = field(
        default_factory=lambda: float(os.getenv("SIGNAL_MIN_EXPECTED_RETURN", "0.005"))
    )
    max_volatility_score: float = field(
        default_factory=lambda: float(os.getenv("SIGNAL_MAX_VOLATILITY", "0.80"))
    )


@dataclass(frozen=True)
class TargetConfig:
    """Target engineering configuration."""
    # Triple barrier settings
    profit_target: float = 0.02  # 2% take profit
    stop_loss: float = 0.01      # 1% stop loss
    max_holding_periods: int = 24  # 24 hours max hold

    # Multi-horizon targets
    horizons: tuple = (4, 8, 12, 24)  # Candles to look ahead
    min_move_threshold: float = 0.005  # 0.5% minimum move for label


@dataclass(frozen=True)
class KillCriteriaConfig:
    """Kill criteria thresholds."""
    min_sharpe_vs_baseline: float = 0.10  # Must beat baseline by 10%
    max_allowed_drawdown: float = 0.15    # 15% max drawdown
    min_profit_factor: float = 1.2        # Minimum profit factor
    forward_degradation_threshold: float = 0.20  # 20% forward degradation allowed


@dataclass(frozen=True)
class StorageConfig:
    """Cloud storage configuration."""
    gcs_bucket: str = field(default_factory=lambda: os.getenv("GCS_BUCKET", "trading-system-data"))
    model_registry_prefix: str = "models/"
    feature_store_prefix: str = "features/"
    predictions_prefix: str = "predictions/"
    snapshots_prefix: str = "snapshots/"


@dataclass(frozen=True)
class APIConfig:
    """API server configuration."""
    host: str = field(default_factory=lambda: os.getenv("API_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(os.getenv("API_PORT", "8000")))
    debug: bool = field(default_factory=lambda: os.getenv("DEBUG", "false").lower() == "true")
    allowed_origins: list = field(default_factory=lambda: os.getenv(
        "ALLOWED_ORIGINS",
        "http://localhost:5173,http://localhost:3000,https://valoratrade.web.app"
    ).replace(";", ",").split(","))
    rate_limit: str = "100/minute"


@dataclass(frozen=True)
class Settings:
    """Main settings container."""
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    binance: BinanceConfig = field(default_factory=BinanceConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    capital: CapitalConfig = field(default_factory=CapitalConfig)
    signal: SignalConfig = field(default_factory=SignalConfig)
    target: TargetConfig = field(default_factory=TargetConfig)
    kill_criteria: KillCriteriaConfig = field(default_factory=KillCriteriaConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    api: APIConfig = field(default_factory=APIConfig)

    # Encryption key for API keys
    encryption_secret: str = field(default_factory=lambda: os.getenv("ENCRYPTION_SECRET", ""))


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


# Timeframe constants
class Timeframes:
    """Timeframe constants for the system."""
    VISUALIZATION_15M = "15m"
    DECISION_1H = "1h"  # STRICT decision timeframe
    CONTEXT_4H = "4h"

    ALLOWED = (VISUALIZATION_15M, DECISION_1H, CONTEXT_4H)

    @classmethod
    def is_decision_timeframe(cls, tf: str) -> bool:
        """Check if timeframe is the decision timeframe."""
        return tf == cls.DECISION_1H


def validate_startup_config() -> List[str]:
    """
    Validate all critical configuration at startup.
    Returns list of warnings. Raises ConfigurationError on critical failures.
    """
    errors = []
    warnings = []
    settings = get_settings()

    # ── Firebase / Firestore ──────────────────────────────────────────
    cred_path = settings.database.credentials_path
    service_account_json = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")

    has_cred_file = cred_path and os.path.exists(cred_path)
    has_inline_json = bool(service_account_json)
    is_cloud_run = bool(os.getenv("K_SERVICE"))  # Cloud Run sets K_SERVICE

    if not has_cred_file and not has_inline_json and not is_cloud_run:
        errors.append(
            "FIREBASE CREDENTIALS MISSING: Set GOOGLE_APPLICATION_CREDENTIALS "
            "to a valid service account JSON file path, or set "
            "FIREBASE_SERVICE_ACCOUNT_JSON with inline JSON, or deploy on Cloud Run."
        )
    elif cred_path and not os.path.exists(cred_path) and not has_inline_json and not is_cloud_run:
        errors.append(
            f"FIREBASE CREDENTIALS FILE NOT FOUND: '{cred_path}' does not exist. "
            f"Download it from Firebase Console → Project Settings → Service Accounts."
        )

    # ── Binance API ───────────────────────────────────────────────────
    if not settings.binance.api_key or not settings.binance.api_secret:
        errors.append(
            "BINANCE API KEYS MISSING: Set BINANCE_API_KEY and BINANCE_API_SECRET "
            "in your .env file. Get keys from https://www.binance.com/en/my/settings/api-management"
        )

    if settings.binance.testnet:
        warnings.append("Binance TESTNET mode is enabled — using testnet API.")

    # ── Encryption ────────────────────────────────────────────────────
    if not settings.encryption_secret:
        warnings.append("ENCRYPTION_SECRET not set — API key encryption will fail.")

    # ── Signal thresholds ─────────────────────────────────────────────
    if settings.signal.confidence_threshold < 0.5 or settings.signal.confidence_threshold > 0.99:
        warnings.append(
            f"SIGNAL_CONFIDENCE_THRESHOLD={settings.signal.confidence_threshold} "
            f"is outside recommended range [0.50, 0.99]."
        )

    # ── Report ────────────────────────────────────────────────────────
    if errors:
        error_msg = "\n".join(f"  ✗ {e}" for e in errors)
        warning_msg = "\n".join(f"  ⚠ {w}" for w in warnings) if warnings else ""
        full_msg = (
            f"\n{'='*60}\n"
            f"  STARTUP FAILED — CONFIGURATION ERRORS\n"
            f"{'='*60}\n"
            f"{error_msg}\n"
            f"{warning_msg}\n"
            f"{'='*60}\n"
        )
        raise ConfigurationError(full_msg)

    # Log warnings
    for w in warnings:
        logger.warning(f"CONFIG WARNING: {w}")

    # Log success summary
    logger.info("="*60)
    logger.info("  CONFIGURATION VALIDATED SUCCESSFULLY")
    logger.info(f"  Firebase: {'file' if has_cred_file else 'inline-json' if has_inline_json else 'cloud-default'}")
    logger.info(f"  Binance:  {'testnet' if settings.binance.testnet else 'production'}")
    logger.info(f"  Signal thresholds: confidence≥{settings.signal.confidence_threshold}, "
                f"return≥{settings.signal.min_expected_return}, "
                f"volatility≤{settings.signal.max_volatility_score}")
    logger.info("="*60)

    return warnings
