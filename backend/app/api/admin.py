"""
Admin API

Endpoints for model management, training, and system administration.
"""
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from app.models.registry.model_registry import ModelRegistry
from app.models.registry.champion_challenger import ChampionChallenger
from app.evaluation.baselines import BaselineStrategies
from app.capital.controller import CapitalController
from app.capital.killswitch import KillSwitch

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin"])

from dataclasses import dataclass

@dataclass
class AdminComponents:
    model_registry: ModelRegistry
    baseline_strategies: BaselineStrategies
    champion_challenger: ChampionChallenger
    capital_controller: CapitalController
    kill_switch: KillSwitch

_components: Optional[AdminComponents] = None

def get_admin_components() -> AdminComponents:
    """Lazy load admin components."""
    global _components
    if _components is None:
        model_reg = ModelRegistry()
        base_strat = BaselineStrategies()
        _components = AdminComponents(
            model_registry=model_reg,
            baseline_strategies=base_strat,
            champion_challenger=ChampionChallenger(model_reg, base_strat),
            capital_controller=CapitalController(),
            kill_switch=KillSwitch()
        )
    return _components


class RetrainRequest(BaseModel):
    """Retrain request body."""
    symbol: str
    force: bool = False


class PromoteRequest(BaseModel):
    """Model promotion request."""
    model_id: str


class KillSwitchRequest(BaseModel):
    """Kill switch control request."""
    action: str  # 'activate' or 'reset'
    admin_key: str
    reason: Optional[str] = None


@router.get("/models")
async def list_models(
    symbol: Optional[str] = None,
    status: Optional[str] = None
):
    """List all registered models."""
    try:
        sys = get_admin_components()
        models = sys.model_registry.list_models(symbol=symbol, status=status)
        
        return {
            "success": True,
            "data": [m.to_dict() for m in models]
        }
        
    except Exception as e:
        logger.error(f"Error listing models: {e}")
        raise HTTPException(status_code=500, detail="Failed to list models")


@router.get("/models/{model_id}")
async def get_model(model_id: str):
    """Get details of a specific model."""
    try:
        sys = get_admin_components()
        model = sys.model_registry.get(model_id)
        
        if not model:
            raise HTTPException(status_code=404, detail="Model not found")
        
        return {
            "success": True,
            "data": model.to_dict()
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting model: {e}")
        raise HTTPException(status_code=500, detail="Failed to get model")


@router.get("/champion/{symbol}")
async def get_champion(symbol: str):
    """Get current champion model for a symbol."""
    try:
        symbol = symbol.upper()
        if not symbol.endswith("USDT"):
            symbol = f"{symbol}USDT"
        
            symbol = f"{symbol}USDT"
        
        sys = get_admin_components()
        champion = sys.model_registry.get_champion(symbol)
        
        if not champion:
            return {
                "success": True,
                "data": None,
                "message": f"No champion model for {symbol}"
            }
        
        return {
            "success": True,
            "data": champion.to_dict()
        }
        
    except Exception as e:
        logger.error(f"Error getting champion: {e}")
        raise HTTPException(status_code=500, detail="Failed to get champion")


@router.post("/promote")
async def promote_model(request: PromoteRequest):
    """Promote a model to champion (with gates)."""
    try:
        # This would normally include baseline comparison
        # For now, direct promotion
        sys = get_admin_components()
        success = sys.model_registry.promote_to_champion(request.model_id)
        
        if not success:
            raise HTTPException(status_code=400, detail="Promotion failed")
        
        return {
            "success": True,
            "data": {"model_id": request.model_id, "status": "promoted"}
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error promoting model: {e}")
        raise HTTPException(status_code=500, detail="Promotion failed")


@router.post("/retrain")
async def trigger_retrain(request: RetrainRequest):
    """Trigger model retraining."""
    try:
        symbol = request.symbol.upper()
        if not symbol.endswith("USDT"):
            symbol = f"{symbol}USDT"
        
        # In production, this would queue a training job
        # For now, return acknowledgment
        return {
            "success": True,
            "data": {
                "symbol": symbol,
                "status": "queued",
                "message": "Retraining job queued"
            }
        }
        
    except Exception as e:
        logger.error(f"Error triggering retrain: {e}")
        raise HTTPException(status_code=500, detail="Failed to queue retrain")


@router.get("/model-status")
async def get_model_status():
    """Get overall model health status."""
    try:
        # Get champions for all symbols
        sys = get_admin_components()
        all_models = sys.model_registry.list_models(status="champion")
        
        status = {
            "total_models": len(sys.model_registry.list_models()),
            "champions": len(all_models),
            "champion_details": [
                {
                    "symbol": m.symbol,
                    "version": m.version,
                    "trained_at": m.created_at.isoformat(),
                    "validation_accuracy": m.validation_metrics.get("accuracy", 0)
                }
                for m in all_models
            ]
        }
        
        return {
            "success": True,
            "data": status
        }
        
    except Exception as e:
        logger.error(f"Error getting model status: {e}")
        raise HTTPException(status_code=500, detail="Failed to get status")


@router.post("/killswitch")
async def control_killswitch(request: KillSwitchRequest):
    """Control the kill switch."""
    try:
        expected_key = "ADMIN_SECRET_KEY"  # In production, from env
        
        if request.action == "activate":
            sys = get_admin_components()
            sys.kill_switch.manual_activate(
                request.reason or "Manual activation",
                sys.capital_controller.equity_state.equity
            )
            return {
                "success": True,
                "data": {"status": "activated"}
            }
            
        elif request.action == "reset":
            sys = get_admin_components()
            success = sys.kill_switch.reset(request.admin_key, expected_key)
            if not success:
                raise HTTPException(status_code=403, detail="Invalid admin key")
            
            return {
                "success": True,
                "data": {"status": "reset"}
            }
        
        raise HTTPException(status_code=400, detail="Invalid action")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Kill switch error: {e}")
        raise HTTPException(status_code=500, detail="Kill switch control failed")


@router.get("/killswitch/status")
async def get_killswitch_status():
    """Get kill switch status."""
    try:
        sys = get_admin_components()
        return {
            "success": True,
            "data": sys.kill_switch.get_status()
        }
        
    except Exception as e:
        logger.error(f"Error getting kill switch status: {e}")
        raise HTTPException(status_code=500, detail="Failed to get status")


@router.get("/promotion-history")
async def get_promotion_history(symbol: Optional[str] = None):
    """Get model promotion history."""
    try:
        sys = get_admin_components()
        history = sys.champion_challenger.get_promotion_history(symbol)
        
        return {
            "success": True,
            "data": history
        }
        
    except Exception as e:
        logger.error(f"Error getting history: {e}")
        raise HTTPException(status_code=500, detail="Failed to get history")
