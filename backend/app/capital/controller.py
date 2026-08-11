"""
Capital Survival Controller

Global capital controller that sits ABOVE all strategies.
Enforces risk limits and can stop trading entirely.

This is the final gate before any trade execution.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Tuple
from app.firebase_config import get_firestore

from app.config import get_settings, CapitalConfig
from app.capital.killswitch import KillSwitch

logger = logging.getLogger(__name__)


class RejectionReason(Enum):
    """Reasons for trade rejection."""
    KILL_SWITCH_ACTIVE = "kill_switch_active"
    MAX_DRAWDOWN_EXCEEDED = "max_drawdown_exceeded"
    DRAWDOWN_THROTTLE = "drawdown_throttle"
    MAX_EXPOSURE = "max_exposure_reached"
    MAX_CONCURRENT = "max_concurrent_trades"
    LOW_CONFIDENCE = "confidence_below_floor"
    CORRELATION_LIMIT = "correlation_limit_exceeded"
    NO_TRADE_ZONE = "no_trade_zone_active"
    FORCED_FLAT = "forced_flat_mode"
    REGIME_FILTER = "regime_not_tradeable"
    SYSTEM_STATE = "system_state_not_tradeable"
    MODEL_DEGRADED = "model_degraded"


class KillSwitchState(Enum):
    """
    Kill switch states with override hierarchy.
    
    Hierarchy (each can override those below):
    1. KILLED (no trading, no signals)
    2. BASELINE_ONLY (only baseline strategy signals)
    3. THROTTLED (reduced position sizes)
    4. ACTIVE (normal trading)
    """
    ACTIVE = "active"
    THROTTLED = "throttled"
    BASELINE_ONLY = "baseline_only"
    KILLED = "killed"


@dataclass
class Position:
    """Active position tracking."""
    symbol: str
    direction: str  # 'long' or 'short'
    entry_price: float
    size: float
    entry_time: datetime
    unrealized_pnl: float = 0.0
    
    @property
    def exposure(self) -> float:
        """Calculate position exposure."""
        return self.size * self.entry_price


@dataclass
class TradeApproval:
    """Result of trade approval check."""
    approved: bool
    reason: Optional[RejectionReason] = None
    adjusted_size: float = 0.0
    message: str = ""
    
    def to_dict(self) -> Dict:
        return {
            "approved": self.approved,
            "reason": self.reason.value if self.reason else None,
            "adjusted_size": self.adjusted_size,
            "message": self.message
        }


@dataclass
class EquityState:
    """Current equity and drawdown state."""
    equity: float
    peak_equity: float
    drawdown: float
    drawdown_pct: float
    last_update: datetime = field(default_factory=datetime.utcnow)


class CapitalController:
    """
    Global capital survival controller.
    
    Override Hierarchy (highest to lowest priority):
    1. Capital Controller (kill switch, drawdown)
    2. System State Machine (KILLED, LIVE_DEGRADED states)
    3. Strategy Engine (position sizing, signals)
    
    Enforces:
    - Portfolio exposure limits
    - Max concurrent trades
    - Correlation filters
    - Drawdown-based throttling
    - Kill-switch logic
    - Confidence decay
    - No-trade zones
    - Forced flat mode
    
    The system MUST be able to STOP trading.
    """
    
    def __init__(self, config: Optional[CapitalConfig] = None):
        self.config = config or get_settings().capital
        
        # State
        self.positions: Dict[str, Position] = {}
        self.equity_state = EquityState(
            equity=100000.0,
            peak_equity=100000.0,
            drawdown=0.0,
            drawdown_pct=0.0
        )
        
        # Control flags and integrated KillSwitch module
        self.killswitch = KillSwitch()
        self.is_killed = False
        self.is_forced_flat = False
        self.no_trade_zones: List[Tuple[datetime, datetime]] = []

        # Firestore
        self.db = get_firestore()
        self.doc_ref = self.db.collection('system_state').document('capital_controller')
        
        # Load state
        self._load_state()
        
        # Metrics
        self.trades_today = 0
        self.rejections_today = 0
        self.last_reset = datetime.utcnow()
        
        # History
        if not hasattr(self, 'equity_history'):
             self.equity_history = []
             self._record_equity(self.equity_state.equity)
        
    def _load_state(self):
        """Load state from Firestore."""
        try:
            doc = self.doc_ref.get()
            if doc.exists:
                data = doc.to_dict()
                self.equity_state.equity = data.get('equity', 100000.0)
                self.equity_state.peak_equity = data.get('peak_equity', 100000.0)
                self.equity_state.drawdown = data.get('drawdown', 0.0)
                self.equity_state.drawdown_pct = data.get('drawdown_pct', 0.0)
                self.is_killed = data.get('is_killed', False)
                if self.is_killed:
                    self.killswitch.is_active = True
                self.is_forced_flat = data.get('is_forced_flat', False)
                logger.info("Capital controller state loaded from Firestore")
        except Exception as e:
            logger.error(f"Failed to load state: {e}")

    def _save_state(self):
        """Save state to Firestore."""
        try:
            state = {
                "equity": self.equity_state.equity,
                "peak_equity": self.equity_state.peak_equity,
                "drawdown": self.equity_state.drawdown,
                "drawdown_pct": self.equity_state.drawdown_pct,
                "is_killed": self.is_killed or self.killswitch.is_active,
                "is_forced_flat": self.is_forced_flat,
                "last_update": datetime.utcnow()
            }
            self.doc_ref.set(state, merge=True)
        except Exception as e:
            logger.error(f"Failed to save state: {e}")
            
    def get_history(self, limit: int = 100) -> List[Dict]:
        """Get equity history."""
        return self.equity_history[-limit:]

    def _record_equity(self, equity: float) -> None:
        """Record equity point and save state."""
        self.equity_history.append({
            "timestamp": datetime.utcnow(),
            "equity": equity,
            "drawdown_pct": self.equity_state.drawdown_pct
        })
        self._save_state()
    
    def can_trade(
        self,
        symbol: str,
        direction: str,
        confidence: float,
        proposed_size: float,
        regime_tradeable: bool = True
    ) -> TradeApproval:
        """
        Check if a trade is allowed.
        
        This is the FINAL GATE before execution.
        
        Args:
            symbol: Trading pair
            direction: 'long' or 'short'
            confidence: Model confidence 0-1
            proposed_size: Proposed position size in base currency
            regime_tradeable: Whether current regime allows trading
            
        Returns:
            TradeApproval with decision and adjusted size
        """
        # Check kill switch first (either controller flag or KillSwitch module)
        if self.is_killed or self.killswitch.is_active:
            return TradeApproval(
                approved=False,
                reason=RejectionReason.KILL_SWITCH_ACTIVE,
                message="Trading is disabled by kill switch"
            )
        
        # Check forced flat mode
        if self.is_forced_flat:
            return TradeApproval(
                approved=False,
                reason=RejectionReason.FORCED_FLAT,
                message="System is in forced flat mode"
            )
        
        # Check no-trade zones
        if self._in_no_trade_zone():
            return TradeApproval(
                approved=False,
                reason=RejectionReason.NO_TRADE_ZONE,
                message="Currently in no-trade zone"
            )
        
        # Check regime
        if not regime_tradeable:
            return TradeApproval(
                approved=False,
                reason=RejectionReason.REGIME_FILTER,
                message="Current regime does not allow trading"
            )
        
        # Check drawdown
        if self.equity_state.drawdown_pct >= self.config.max_drawdown:
            self._trigger_kill("Max drawdown exceeded")
            return TradeApproval(
                approved=False,
                reason=RejectionReason.MAX_DRAWDOWN_EXCEEDED,
                message=f"Drawdown {self.equity_state.drawdown_pct:.1%} exceeds limit"
            )
        
        # Check drawdown throttle
        throttle_threshold = self.config.max_drawdown * self.config.drawdown_throttle_threshold
        if self.equity_state.drawdown_pct >= throttle_threshold:
            # Reduce size but allow trade
            size_multiplier = 1 - (self.equity_state.drawdown_pct / self.config.max_drawdown)
            proposed_size *= max(0.25, size_multiplier)
            logger.warning(f"Drawdown throttle active, size reduced by {1-size_multiplier:.0%}")
        
        # Check confidence floor
        if confidence < self.config.confidence_floor:
            return TradeApproval(
                approved=False,
                reason=RejectionReason.LOW_CONFIDENCE,
                message=f"Confidence {confidence:.2f} below floor {self.config.confidence_floor}"
            )
        
        # Check concurrent positions
        if len(self.positions) >= self.config.max_concurrent_trades:
            return TradeApproval(
                approved=False,
                reason=RejectionReason.MAX_CONCURRENT,
                message=f"Max concurrent trades ({self.config.max_concurrent_trades}) reached"
            )
        
        # Fetch price securely (no silent fallbacks)
        try:
            current_price = self._get_price(symbol)
        except Exception as e:
            logger.error(f"Trade rejected: price fetch failed for {symbol}: {e}")
            return TradeApproval(
                approved=False,
                reason=RejectionReason.SYSTEM_STATE,
                message=f"Price unavailable for {symbol}: unable to evaluate risk exposure"
            )

        # Check total exposure
        current_exposure = self._total_exposure()
        proposed_exposure = proposed_size * current_price
        new_exposure_pct = (current_exposure + proposed_exposure) / self.equity_state.equity
        
        if new_exposure_pct > self.config.max_exposure:
            # Try to adjust size
            available_exposure = (self.config.max_exposure * self.equity_state.equity) - current_exposure
            if available_exposure > 0:
                adjusted_size = available_exposure / current_price
                return TradeApproval(
                    approved=True,
                    adjusted_size=adjusted_size,
                    message=f"Size adjusted to {adjusted_size:.4f} due to exposure limit"
                )
            return TradeApproval(
                approved=False,
                reason=RejectionReason.MAX_EXPOSURE,
                message="Maximum portfolio exposure reached"
            )
        
        # Check correlation
        if not self._check_correlation(symbol):
            return TradeApproval(
                approved=False,
                reason=RejectionReason.CORRELATION_LIMIT,
                message="Position would exceed correlation limit with existing positions"
            )
        
        # All checks passed
        return TradeApproval(
            approved=True,
            adjusted_size=proposed_size,
            message="Trade approved"
        )
    
    def update_equity(self, new_equity: float) -> None:
        """Update equity state and check for drawdown triggers."""
        self.equity_state.equity = new_equity
        self.equity_state.last_update = datetime.utcnow()
        
        # Update peak
        if new_equity > self.equity_state.peak_equity:
            self.equity_state.peak_equity = new_equity
        
        # Calculate drawdown
        self.equity_state.drawdown = self.equity_state.peak_equity - new_equity
        self.equity_state.drawdown_pct = self.equity_state.drawdown / self.equity_state.peak_equity
        
        # Check for automatic kill
        if self.equity_state.drawdown_pct >= self.config.max_drawdown:
            self._trigger_kill("Automatic kill: drawdown limit exceeded")
            
        self._record_equity(new_equity)
    
    def add_position(self, position: Position) -> None:
        """Register a new position."""
        self.positions[position.symbol] = position
        self.trades_today += 1
        logger.info(f"Position opened: {position.symbol} {position.direction} @ {position.entry_price}")
    
    def close_position(self, symbol: str, exit_price: float) -> Optional[float]:
        """Close a position and return realized PnL."""
        if symbol not in self.positions:
            return None
        
        position = self.positions.pop(symbol)
        
        if position.direction == 'long':
            pnl = (exit_price - position.entry_price) * position.size
        else:
            pnl = (position.entry_price - exit_price) * position.size
        
        logger.info(f"Position closed: {symbol} PnL: {pnl:.2f}")
        return pnl
    
    def close_all_positions(self) -> None:
        """Emergency close all positions."""
        for symbol in list(self.positions.keys()):
            # In real implementation, would execute market orders
            self.close_position(symbol, self._get_price(symbol))
        logger.warning("All positions closed")
    
    def _trigger_kill(self, reason: str) -> None:
        """Activate kill switch."""
        self.is_killed = True
        self.killswitch.manual_activate(reason, self.equity_state.equity)
        self.close_all_positions()
        self._save_state()
        logger.critical(f"KILL SWITCH ACTIVATED: {reason}")
    
    def reset_kill_switch(self, admin_key: str, expected_key: str) -> bool:
        """Reset kill switch (requires admin key verification)."""
        if not admin_key or admin_key != expected_key:
            logger.warning("Kill switch reset failed: invalid or missing admin key")
            return False
        
        self.is_killed = False
        self.killswitch.reset(admin_key, expected_key)
        self._save_state()
        logger.info("Kill switch reset by admin")
        return True
    
    def set_forced_flat(self, enabled: bool) -> None:
        """Set forced flat mode."""
        self.is_forced_flat = enabled
        if enabled:
            self.close_all_positions()
            logger.warning("Forced flat mode activated")
    
    def add_no_trade_zone(self, start: datetime, end: datetime) -> None:
        """Add a no-trade time window."""
        self.no_trade_zones.append((start, end))
    
    def _in_no_trade_zone(self) -> bool:
        """Check if currently in a no-trade zone."""
        now = datetime.utcnow()
        return any(start <= now <= end for start, end in self.no_trade_zones)
    
    def _total_exposure(self) -> float:
        """Calculate total current exposure."""
        return sum(p.exposure for p in self.positions.values())
    
    def _get_price(self, symbol: str) -> float:
        """
        Get current market price for a symbol with retries.
        Raises RuntimeError if price cannot be fetched after retries.
        Never returns a silent $1.00 fallback.
        """
        if symbol in self.positions:
            return self.positions[symbol].entry_price
            
        import time
        import requests
        
        url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}"
        max_retries = 3
        last_err = None
        
        for attempt in range(1, max_retries + 1):
            try:
                response = requests.get(url, timeout=3)
                if response.status_code == 200:
                    data = response.json()
                    price = float(data['price'])
                    if price <= 0:
                        raise ValueError(f"Invalid non-positive price received: {price}")
                    return price
                else:
                    last_err = f"HTTP status {response.status_code}"
            except Exception as e:
                last_err = str(e)
            
            if attempt < max_retries:
                time.sleep(0.3 * attempt)
        
        err_msg = f"Failed to fetch market price for {symbol} after {max_retries} retries: {last_err}"
        logger.error(err_msg)
        raise RuntimeError(err_msg)
    
    def _check_correlation(self, symbol: str) -> bool:
        """Check if new position would exceed correlation limits."""
        # Simplified: just check if same base currency
        # Real implementation would check rolling correlation
        base = symbol.replace('USDT', '')
        for existing_symbol in self.positions:
            existing_base = existing_symbol.replace('USDT', '')
            # Block same asset
            if base == existing_base:
                return False
        return True
    
    def get_status(self) -> Dict:
        """Get current capital controller status."""
        return {
            "is_killed": self.is_killed,
            "is_forced_flat": self.is_forced_flat,
            "equity": self.equity_state.equity,
            "drawdown_pct": self.equity_state.drawdown_pct,
            "positions_count": len(self.positions),
            "total_exposure": self._total_exposure(),
            "exposure_pct": self._total_exposure() / self.equity_state.equity,
            "trades_today": self.trades_today,
            "rejections_today": self.rejections_today
        }
