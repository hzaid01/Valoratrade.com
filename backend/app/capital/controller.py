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
from app.capital.killswitch import (
    KillSwitch, KillReason, KillSwitchState, KillSwitchEvent,
    IllegalKillSwitchTransition, next_trading_mode, killswitch_auto_mode,
)
from app.core.fs_tx import run_transaction

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

        # Trading mode: Firestore field trading_mode is the source of truth.
        # _mode is only the last value read, used if a read fails.
        self._mode: KillSwitchState = KillSwitchState.ACTIVE
        self.killswitch = KillSwitch(mode_provider=self.get_mode)
        self.no_trade_zones: List[Tuple[datetime, datetime]] = []

        # Firestore
        self.db = get_firestore()
        self.doc_ref = self.db.collection('system_state').document('capital_controller')

        # Daily counters (reset when the UTC date changes, see _roll_day)
        self.trades_today = 0
        self.rejections_today = 0
        self.last_reset = datetime.utcnow()

        # Load state
        self._load_state()
        self.killswitch.start_new_day(self.equity_state.equity)

        # History
        self.equity_history: List[Dict] = []
        self._record_equity(self.equity_state.equity)

    # ── trading mode (single source of truth) ──────────────────────────

    @staticmethod
    def _mode_from_doc(data: Optional[Dict], fallback: KillSwitchState) -> KillSwitchState:
        """Read trading_mode; migrate legacy is_killed/is_forced_flat docs once."""
        if not data:
            return KillSwitchState.ACTIVE
        if "trading_mode" in data:
            try:
                return KillSwitchState(data["trading_mode"])
            except ValueError:
                logger.error(f"Unknown trading_mode {data['trading_mode']!r}; keeping last known {fallback.value}")
                return fallback
        # Legacy doc (pre-state-machine): kill wins over forced-flat.
        if data.get("is_killed"):
            return KillSwitchState.KILLED
        if data.get("is_forced_flat"):
            return KillSwitchState.FORCED_FLAT
        return KillSwitchState.ACTIVE

    def get_mode(self) -> KillSwitchState:
        """
        Current trading mode, read from Firestore on every call so a kill
        issued on any instance (or the admin API's controller) is seen here.
        On read failure, the last known mode is used (logged).
        """
        try:
            doc = self.doc_ref.get()
            self._mode = self._mode_from_doc(doc.to_dict() if doc.exists else None, self._mode)
        except Exception as e:
            logger.error(f"Trading mode read failed, using last known {self._mode.value}: {e}")
        return self._mode

    @property
    def is_killed(self) -> bool:
        """Read-only view of the trading mode (kept for API compatibility)."""
        return self.get_mode() == KillSwitchState.KILLED

    @property
    def is_forced_flat(self) -> bool:
        """Read-only view of the trading mode (kept for API compatibility)."""
        return self.get_mode() == KillSwitchState.FORCED_FLAT

    def is_kill_active(self) -> bool:
        """True if trading is halted by the kill switch."""
        return self.get_mode() == KillSwitchState.KILLED

    def apply_mode_event(self, event: KillSwitchEvent, actor: str, reason: str = "") -> KillSwitchState:
        """
        The ONLY way the trading mode changes. Read-check-write in one
        Firestore transaction. Idempotent for duplicate events (no write).
        Raises IllegalKillSwitchTransition for pairs not in the table.
        """
        def _tx(transaction):
            snap = self.doc_ref.get(transaction=transaction)
            data = snap.to_dict() if snap.exists else {}
            current = self._mode_from_doc(data, self._mode)
            resume = None
            if data.get("resume_mode"):
                try:
                    resume = KillSwitchState(data["resume_mode"])
                except ValueError:
                    resume = None
            new = next_trading_mode(current, event, resume)
            if new == current and "trading_mode" in data:
                return current, new, False
            now = datetime.utcnow()
            update = {
                "trading_mode": new.value,
                "mode_updated_at": now.isoformat(),
                "mode_updated_by": actor,
                "mode_reason": reason,
                # Read-only mirrors for old readers; never read back as state.
                "is_killed": new == KillSwitchState.KILLED,
                "is_forced_flat": new == KillSwitchState.FORCED_FLAT,
            }
            if event == KillSwitchEvent.KILL and current != KillSwitchState.KILLED:
                update["resume_mode"] = current.value
            if event == KillSwitchEvent.RESET and current == KillSwitchState.KILLED:
                update["resume_mode"] = None
            transaction.set(self.doc_ref, update, merge=True)
            transaction.set(self.db.collection("capital_mode_transitions").document(), {
                "from_mode": current.value, "to_mode": new.value, "event": event.value,
                "actor": actor, "reason": reason, "timestamp": now.isoformat(),
            })
            return current, new, True

        try:
            current, new, changed = run_transaction(self.db, _tx)
        except IllegalKillSwitchTransition as e:
            logger.error(f"Illegal trading-mode transition by {actor}: {e}")
            raise

        self._mode = new
        if not changed:
            logger.info(f"Trading mode {event.value} by {actor}: already {new.value} (no-op)")
            return new

        logger.warning(f"Trading mode {current.value} -> {new.value} ({event.value}) by {actor}: {reason}")
        if new in (KillSwitchState.KILLED, KillSwitchState.FORCED_FLAT):
            self.close_all_positions()
        if event == KillSwitchEvent.RESET:
            self.killswitch.on_reset()
        return new

    # ── persistence (equity only; never the mode) ──────────────────────

    def _load_state(self):
        """Load equity from Firestore; the mode is always read live."""
        try:
            doc = self.doc_ref.get()
            if doc.exists:
                data = doc.to_dict()
                self.equity_state.equity = data.get('equity', 100000.0)
                self.equity_state.peak_equity = data.get('peak_equity', 100000.0)
                self.equity_state.drawdown = data.get('drawdown', 0.0)
                self.equity_state.drawdown_pct = data.get('drawdown_pct', 0.0)
                self._mode = self._mode_from_doc(data, self._mode)
                logger.info("Capital controller state loaded from Firestore")
        except Exception as e:
            logger.error(f"Failed to load state: {e}")

    def _save_state(self):
        """
        Save equity fields only. The trading mode is written exclusively by
        apply_mode_event (previously this wrote is_killed from memory, so an
        instance recording equity could un-kill another instance's kill).
        """
        try:
            state = {
                "equity": self.equity_state.equity,
                "peak_equity": self.equity_state.peak_equity,
                "drawdown": self.equity_state.drawdown,
                "drawdown_pct": self.equity_state.drawdown_pct,
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

    def _roll_day(self) -> None:
        """Reset daily counters when the UTC date changes since last_reset."""
        now = datetime.utcnow()
        if now.date() != self.last_reset.date():
            logger.info(
                f"New trading day: resetting trades_today={self.trades_today}, "
                f"rejections_today={self.rejections_today}"
            )
            self.trades_today = 0
            self.rejections_today = 0
            self.last_reset = now
            self.killswitch.start_new_day(self.equity_state.equity)

    # ── trade approval ─────────────────────────────────────────────────

    def can_trade(
        self,
        symbol: str,
        direction: str,
        confidence: float,
        proposed_size: Optional[float],
        regime_tradeable: bool = True,
        price: Optional[float] = None
    ) -> TradeApproval:
        """
        Check if a trade is allowed.

        This is the FINAL GATE before execution.

        Args:
            symbol: Trading pair
            direction: 'long' or 'short'
            confidence: Model confidence 0-1
            proposed_size: Proposed position size in base currency, or None
                when the size is not known yet (size-dependent checks, i.e.
                drawdown size throttle and exposure, are skipped; call again
                with the real size)
            regime_tradeable: Whether current regime allows trading
            price: Current price for the exposure check; fetched from the
                exchange only if not given

        Returns:
            TradeApproval with decision and adjusted size
        """
        self._roll_day()
        approval = self._evaluate_trade(symbol, direction, confidence, proposed_size, regime_tradeable, price)
        if not approval.approved:
            self.rejections_today += 1
        return approval

    def _evaluate_trade(
        self,
        symbol: str,
        direction: str,
        confidence: float,
        proposed_size: Optional[float],
        regime_tradeable: bool,
        price: Optional[float] = None
    ) -> TradeApproval:
        mode = self.get_mode()
        if mode == KillSwitchState.KILLED:
            return TradeApproval(
                approved=False,
                reason=RejectionReason.KILL_SWITCH_ACTIVE,
                message="Trading is disabled by kill switch"
            )

        if mode == KillSwitchState.FORCED_FLAT:
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
        
        # Check drawdown (automatic protection: governed by KILLSWITCH_AUTO)
        if self.equity_state.drawdown_pct >= self.config.max_drawdown:
            if self._auto_kill(
                KillReason.MAX_DRAWDOWN,
                f"Drawdown {self.equity_state.drawdown_pct:.1%} >= {self.config.max_drawdown:.1%}"
            ):
                return TradeApproval(
                    approved=False,
                    reason=RejectionReason.MAX_DRAWDOWN_EXCEEDED,
                    message=f"Drawdown {self.equity_state.drawdown_pct:.1%} exceeds limit"
                )
            logger.warning(
                f"KILLSWITCH_AUTO log_only: WOULD HAVE REJECTED {symbol} for max drawdown "
                f"{self.equity_state.drawdown_pct:.1%}"
            )
        
        # Check drawdown throttle
        throttle_threshold = self.config.max_drawdown * self.config.drawdown_throttle_threshold
        if proposed_size is not None and self.equity_state.drawdown_pct >= throttle_threshold:
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
        
        # Check correlation (before sizing: it does not depend on size)
        if not self._check_correlation(symbol):
            return TradeApproval(
                approved=False,
                reason=RejectionReason.CORRELATION_LIMIT,
                message="Position would exceed correlation limit with existing positions"
            )

        if proposed_size is None:
            return TradeApproval(
                approved=True,
                adjusted_size=0.0,
                message="Approved before sizing; size-dependent checks pending"
            )

        # Price: caller-supplied (e.g. the candle close) or fetched securely
        current_price = price if (price is not None and price > 0) else None
        if current_price is None:
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
        self.equity_state.drawdown_pct = (
            self.equity_state.drawdown / self.equity_state.peak_equity
            if self.equity_state.peak_equity > 0 else 0.0
        )

        self._record_equity(new_equity)

        # Check for automatic kill
        if self.equity_state.drawdown_pct >= self.config.max_drawdown:
            self._auto_kill(KillReason.MAX_DRAWDOWN, "Automatic kill: drawdown limit exceeded")

    def apply_trade_pnl(self, pnl: float) -> float:
        """
        Add realized PnL to the equity stored in Firestore atomically (so two
        instances resolving trades don't overwrite each other), then refresh
        drawdown. Returns the new equity.
        """
        def _tx(transaction):
            snap = self.doc_ref.get(transaction=transaction)
            data = snap.to_dict() if snap.exists else {}
            equity = data.get("equity", self.equity_state.equity) + pnl
            transaction.set(self.doc_ref, {"equity": equity}, merge=True)
            return equity

        try:
            stored_peak = (self.doc_ref.get().to_dict() or {}).get("peak_equity")
            if stored_peak:
                self.equity_state.peak_equity = max(self.equity_state.peak_equity, stored_peak)
            new_equity = run_transaction(self.db, _tx)
        except Exception as e:
            logger.error(f"Atomic equity update failed, applying in memory: {e}")
            new_equity = self.equity_state.equity + pnl
        self.update_equity(new_equity)
        return new_equity

    def add_position(self, position: Position) -> None:
        """Register a new position."""
        self._roll_day()
        self.positions[position.symbol] = position
        self.trades_today += 1
        logger.info(f"Position opened: {position.symbol} {position.direction} @ {position.entry_price}")

    @staticmethod
    def _pnl(direction: str, entry_price: float, exit_price: float, size: float) -> float:
        if direction == 'long':
            return (exit_price - entry_price) * size
        return (entry_price - exit_price) * size

    def close_position(self, symbol: str, exit_price: float) -> Optional[float]:
        """Close a position and return realized PnL."""
        if symbol not in self.positions:
            return None

        position = self.positions.pop(symbol)
        pnl = self._pnl(position.direction, position.entry_price, exit_price, position.size)
        logger.info(f"Position closed: {symbol} PnL: {pnl:.2f}")
        return pnl

    def record_closed_trade(
        self,
        symbol: str,
        direction: str,
        entry_price: float,
        exit_price: float,
        size: float,
        entry_time: datetime
    ) -> Optional[float]:
        """
        Record a trade that has already completed (the forward engine's
        resolved paper trades: there is no live execution in this system).

        Goes through add_position/close_position under a unique key so the
        same PnL math and counters apply, but the position never stays open,
        so it cannot trip the concurrency/correlation/exposure checks and
        silence later signals. Then updates equity and runs the automatic
        kill checks (governed by KILLSWITCH_AUTO).
        """
        if direction not in ("long", "short") or not size or size <= 0:
            return None
        key = f"{symbol}#closed#{entry_time.isoformat()}"
        self.add_position(Position(
            symbol=key, direction=direction, entry_price=entry_price,
            size=size, entry_time=entry_time
        ))
        pnl = self.close_position(key, exit_price)
        new_equity = self.apply_trade_pnl(pnl)
        self.check_and_activate(current_equity=new_equity, last_trade_pnl=pnl)
        return pnl

    def close_all_positions(self) -> None:
        """Emergency close all positions."""
        for symbol in list(self.positions.keys()):
            # In real implementation, would execute market orders
            self.close_position(symbol, self._get_price(symbol))
        logger.warning("All positions closed")

    # ── kill switch control ────────────────────────────────────────────

    def check_and_activate(
        self,
        current_equity: float,
        last_trade_pnl: Optional[float] = None,
        model_performance_ratio: Optional[float] = None,
        api_error: bool = False,
        data_error: bool = False
    ) -> bool:
        """Evaluate automatic kill conditions. Returns True if a kill was applied."""
        trigger = self.killswitch.evaluate(
            current_equity=current_equity,
            last_trade_pnl=last_trade_pnl,
            model_performance_ratio=model_performance_ratio,
            api_error=api_error,
            data_error=data_error
        )
        if trigger is None:
            return False
        return self._auto_kill(*trigger)

    def _auto_kill(self, reason: KillReason, details: str) -> bool:
        """
        Automatic activation. Applied only when KILLSWITCH_AUTO=enforce;
        in log_only (default) it logs what it would have done.
        Returns True if trading is now killed by this call (or already was).
        """
        if self.get_mode() == KillSwitchState.KILLED:
            return True
        if killswitch_auto_mode() != "enforce":
            logger.warning(f"KILLSWITCH_AUTO log_only: WOULD HAVE KILLED ({reason.value}): {details}")
            return False
        self._trigger_kill(details, kill_reason=reason, actor="auto")
        return True

    def _trigger_kill(self, reason: str, kill_reason: KillReason = KillReason.MANUAL, actor: str = "manual") -> None:
        """Activate kill switch (manual activation is always enforced)."""
        before = self.get_mode()
        self.apply_mode_event(KillSwitchEvent.KILL, actor=actor, reason=reason)
        if before != KillSwitchState.KILLED:
            self.killswitch.record_activation(kill_reason, reason, self.equity_state.equity)

    def reset_kill_switch(self, admin_key: str, expected_key: str) -> bool:
        """Reset kill switch (requires admin key verification)."""
        if not admin_key or admin_key != expected_key:
            logger.warning("Kill switch reset failed: invalid or missing admin key")
            return False

        self.apply_mode_event(KillSwitchEvent.RESET, actor="admin", reason="Kill switch reset by admin")
        logger.info("Kill switch reset by admin")
        return True

    def set_forced_flat(self, enabled: bool) -> None:
        """
        Set forced flat mode (persisted). Raises IllegalKillSwitchTransition
        while KILLED: the kill must be reset first.
        """
        self.apply_mode_event(
            KillSwitchEvent.FLAT_ON if enabled else KillSwitchEvent.FLAT_OFF,
            actor="operator", reason="forced flat on" if enabled else "forced flat off"
        )

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
        self._roll_day()
        mode = self.get_mode()
        return {
            "trading_mode": mode.value,
            "is_killed": mode == KillSwitchState.KILLED,
            "is_forced_flat": mode == KillSwitchState.FORCED_FLAT,
            "equity": self.equity_state.equity,
            "drawdown_pct": self.equity_state.drawdown_pct,
            "positions_count": len(self.positions),
            "total_exposure": self._total_exposure(),
            "exposure_pct": self._total_exposure() / self.equity_state.equity,
            "trades_today": self.trades_today,
            "rejections_today": self.rejections_today
        }


_capital_controller: Optional[CapitalController] = None


def get_capital_controller() -> CapitalController:
    """Process-wide controller shared by the signals and admin APIs."""
    global _capital_controller
    if _capital_controller is None:
        _capital_controller = CapitalController()
    return _capital_controller
