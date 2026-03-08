# Production Trading System - Backend

## Architecture: PatchTST + XGBoost

A capital-safe, leakage-free crypto trading platform with strict 1H decision timeframe.

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Set environment variables
export ENCRYPTION_SECRET="your-secret"
export FIREBASE_PROJECT_ID="your-project"

# Run development server
uvicorn app.main:app --reload --port 8000
```

## Key Principles

1. **Decision Timeframe**: All model logic runs on **1H data only**
2. **No Leakage**: Causal features, proper time-series splits
3. **Forward Evaluation**: Predictions locked, resolved later
4. **Baseline Gates**: Must beat Buy&Hold, EMA, Breakout, Random
5. **Capital First**: Kill switch, drawdown throttle, exposure limits

## Project Structure

```
app/
├── api/                  # REST endpoints
│   ├── signals.py       # Signal generation
│   ├── market.py        # Market data (multi-TF)
│   ├── backtest.py      # Backtesting
│   └── admin.py         # Model management
├── core/                 # Business logic
│   ├── data_pipeline.py # Multi-TF data
│   ├── feature_engine.py# Causal features
│   ├── regime_detector.py
│   └── target_engineer.py
├── capital/              # Capital survival
│   ├── controller.py    # Limits & throttling
│   └── killswitch.py    # Emergency stop
├── evaluation/           # Testing
│   ├── baselines.py     # Naive strategies
│   ├── backtest_engine.py
│   └── forward_engine.py
├── governance/           # Data governance
│   ├── versioning.py
│   └── lineage.py
├── models/               # ML models
│   ├── patch_tst.py     # Temporal embeddings
│   ├── xgboost_model.py # Decision model
│   ├── training/        # Training pipeline
│   └── registry/        # Champion/Challenger
├── strategy/             # Strategy engine
│   ├── signal_generator.py
│   ├── position_sizer.py
│   └── trade_levels.py
└── main.py              # FastAPI app
```

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/signals/{symbol}` | Get trading signal |
| `GET /api/market/klines/{symbol}?interval=1h` | Get candles (15m/1h/4h) |
| `GET /api/market/regime/{symbol}` | Get market regime |
| `GET /api/backtest/baselines/{symbol}` | Run all baselines |
| `GET /api/admin/champion/{symbol}` | Get champion model |
| `POST /api/admin/killswitch` | Control kill switch |

## Model Stack

- **PatchTST**: Temporal embedding extraction (64-dim)
- **XGBoost**: Multi-target decision (prob_up, prob_down, expected_return, volatility)
- **Weekly Retrain**: Automatic with champion/challenger promotion

## Safety Controls

- **Max Exposure**: 30%
- **Max Drawdown**: 15% (triggers kill)
- **Drawdown Throttle**: Starts at 12%
- **Confidence Floor**: 0.55
- **Baseline Gates**: Must beat all before promotion
