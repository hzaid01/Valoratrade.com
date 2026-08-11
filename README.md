# Valoratrade.com — AI-Powered Crypto Trading Signals

<p align="center">
  <strong>Capital-safe, leakage-free cryptocurrency trading platform with PatchTST + XGBoost model stack, real-time Binance data, and production-grade risk controls.</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/React-19.2-61DAFB?logo=react" alt="React" />
  <img src="https://img.shields.io/badge/FastAPI-0.109-009688?logo=fastapi" alt="FastAPI" />
  <img src="https://img.shields.io/badge/PyTorch-2.1-EE4C2C?logo=pytorch" alt="PyTorch" />
  <img src="https://img.shields.io/badge/XGBoost-2.0-blue" alt="XGBoost" />
  <img src="https://img.shields.io/badge/Firebase-Auth%20%2B%20Firestore-FFCA28?logo=firebase" alt="Firebase" />
  <img src="https://img.shields.io/badge/Binance-API-F0B90B?logo=binance" alt="Binance" />
  <img src="https://img.shields.io/badge/Cloud%20Run-Deployed-4285F4?logo=google-cloud" alt="GCP" />
</p>

---

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Architecture](#architecture)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Getting Started](#getting-started)
- [Environment Variables](#environment-variables)
- [Usage](#usage)
- [API Reference](#api-reference)
- [Model Stack](#model-stack)
- [Capital & Risk Management](#capital--risk-management)
- [Deployment](#deployment)
- [Discord Bot](#discord-bot)
- [License](#license)

---

## Overview

Valoratrade is a full-stack crypto trading signals platform that combines gradient boosting (XGBoost decision model) and 37 engineered causal technical features to generate LONG / SHORT / NO_TRADE signals for cryptocurrency pairs on Binance.

The system enforces a **strict 1-hour decision timeframe**, uses **triple-barrier labeling** for target generation, and runs a **champion/challenger model promotion pipeline** with forward-only evaluation—no lookahead bias, no data leakage.

**Live champions:** BTCUSDT · ETHUSDT · SOLUSDT

---

## Features

### Trading Intelligence
- **AI-Powered Signals** — XGBoost multi-target predictions (prob_up, prob_down, expected_return, volatility)
- **37 Causal Features** — Momentum, trend, volatility, volume, market structure, and price action indicators with no lookahead
- **Market Regime Detection** — Trending Up/Down, Ranging, High/Low Volatility classification with position sizing multipliers
- **Triple Barrier Targets** — Take-profit, stop-loss, and time-based exit labels at multiple horizons (4, 8, 12, 24 candles)
- **Dynamic Trade Levels** — ATR-based stop-loss and three take-profit targets with risk/reward ratios

### Risk Management
- **Capital Controller** — Global survival layer with max 30% exposure, max 3 concurrent positions, 15% max drawdown
- **Kill Switch** — 4-state emergency control (ACTIVE → THROTTLED → BASELINE_ONLY → KILLED) with manual reset only
- **Drawdown Throttle** — Automatic position size reduction above 12% drawdown
- **Correlation Limits** — Max 70% correlation between concurrent positions

### Model Lifecycle
- **Champion/Challenger Promotion** — New models must beat all baselines (Buy & Hold, EMA Crossover, Breakout, Random Entry) AND the current champion
- **Forward-Only Evaluation** — Predictions locked at signal time, resolved after holding period with no recomputation
- **Automatic Demotion** — Champions degraded on Sharpe drop, win-rate decline, or drawdown breach
- **Walk-Forward Cross-Validation** — 5-split time-series validation during training
- **Full Lineage Tracking** — Dataset version → features → training params → model artifacts → forward metrics

### Real-Time Market Data
- **Live Price Streams** — Binance WebSocket aggregated trades (millisecond-level) and kline streams
- **Multi-Timeframe Charts** — 15-minute (visualization), 1-hour (decision), 4-hour (context) candlestick charts
- **Whale Monitor** — Real-time $50K+ transaction alerts
- **Dashboard** — Top 100 coins by 24h volume with 5-second auto-refresh

### Platform
- **Firebase Authentication** — Email/password sign-up with token-based API auth
- **Encrypted Key Storage** — Fernet (AES-128-CBC + HMAC) encryption for user Binance and OpenAI API keys
- **System Health Dashboard** — Capital status, equity curve, model health, forward performance metrics
- **Responsive UI** — Glass-effect dark theme with mobile-friendly sidebar

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      FRONTEND (React)                       │
│  Dashboard · Analysis · System Status · Settings            │
│  Binance WebSocket ──→ Live Charts + Whale Monitor          │
│  Firebase Auth ──→ ID Token ──→ API Requests                │
└────────────────────────┬────────────────────────────────────┘
                         │ HTTPS (Firebase ID Token)
┌────────────────────────▼────────────────────────────────────┐
│                   BACKEND (FastAPI on Cloud Run)             │
│                                                             │
│  ┌──────────┐  ┌──────────────┐  ┌───────────────────────┐ │
│  │ Signals  │  │   Training   │  │   Admin / Capital     │ │
│  │ API      │  │   Pipeline   │  │   Controller          │ │
│  └────┬─────┘  └──────┬───────┘  └───────────┬───────────┘ │
│       │               │                      │              │
│  ┌────▼───────────────▼──────────────────────▼───────────┐  │
│  │              CORE ENGINE                              │  │
│  │  Data Pipeline → Feature Engine → Regime Detector     │  │
│  │  Target Engineer → PatchTST → XGBoost → Signal Gen   │  │
│  │  Position Sizer → Trade Levels → Capital Controller   │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                             │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────────┐   │
│  │  Model      │  │  Forward     │  │  Champion/       │   │
│  │  Registry   │  │  Engine      │  │  Challenger      │   │
│  └──────┬──────┘  └──────┬───────┘  └───────┬──────────┘   │
└─────────┼────────────────┼──────────────────┼───────────────┘
          │                │                  │
┌─────────▼────────────────▼──────────────────▼───────────────┐
│                   EXTERNAL SERVICES                         │
│  Binance API · Firebase/Firestore · Google Cloud Storage    │
│  Google Cloud Scheduler · OpenAI (optional)                 │
└─────────────────────────────────────────────────────────────┘
```

---

## Tech Stack

### Frontend
| Technology | Version | Purpose |
|---|---|---|
| React | 19.2 | UI framework |
| Vite | 7.2 | Build tool & dev server |
| React Router | 7.10 | Client-side routing |
| Zustand | 5.0 | State management |
| Firebase | 10.7 | Authentication |
| Lightweight Charts | 5.1 | Candlestick charting |
| Recharts | 3.6 | Equity curve charts |
| Tailwind CSS | 3.4 | Utility-first styling |
| Lucide React | 0.561 | Icon library |

### Backend
| Technology | Version | Purpose |
|---|---|---|
| FastAPI | 0.109 | REST API framework |
| PyTorch | 2.1 | PatchTST deep learning model |
| XGBoost | 2.0 | Decision model |
| scikit-learn | 1.4 | Preprocessing & StandardScaler |
| Firebase Admin | 6.2+ | Auth, Firestore, persistence |
| python-binance | 1.0 | Binance market data |
| ta | 0.11 | Technical indicators |
| cryptography | 41.0 | Fernet encryption |
| Google Cloud Storage | 2.14 | Model artifact storage |
| slowapi | 0.1 | Rate limiting |

### Infrastructure
| Service | Purpose |
|---|---|
| Google Cloud Run | Backend hosting (auto-scaling, 2Gi RAM, 2 CPU) |
| Google Cloud Scheduler | Automated data ingestion (15 min) & training (6 hours) |
| Firebase Hosting | Frontend deployment |
| Firestore | Database (candles, state, models, predictions, user data) |
| Google Cloud Storage | Model binary storage |

---

## Project Structure

```
project/
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── Layout.jsx              # Sidebar + header shell
│   │   │   ├── ProtectedRoute.jsx      # Auth guard
│   │   │   ├── CandlestickChart.jsx    # Multi-timeframe live chart
│   │   │   ├── EquityChart.jsx         # Account equity curve
│   │   │   ├── RegimeIndicator.jsx     # Market regime display
│   │   │   ├── SystemHealth.jsx        # Capital + model health
│   │   │   └── WhaleMonitor.jsx        # Large transaction alerts
│   │   ├── pages/
│   │   │   ├── Dashboard.jsx           # Top 100 coins overview
│   │   │   ├── Analysis.jsx            # Symbol deep-dive analysis
│   │   │   ├── SystemStatus.jsx        # System monitoring
│   │   │   ├── Settings.jsx            # API key management
│   │   │   ├── Login.jsx               # Sign in
│   │   │   └── Signup.jsx              # Registration
│   │   ├── store/
│   │   │   └── authStore.js            # Zustand auth state
│   │   └── lib/
│   │       ├── firebase.js             # Firebase config & auth
│   │       ├── api.js                  # REST API client
│   │       └── binanceWebSocket.js     # Real-time price streams
│   ├── package.json
│   ├── vite.config.js
│   └── tailwind.config.js
│
├── backend/
│   ├── app/
│   │   ├── main.py                     # FastAPI entry + startup validation
│   │   ├── config.py                   # Centralized configuration
│   │   ├── firebase_config.py          # Firebase Admin SDK
│   │   ├── api/
│   │   │   ├── signals.py              # GET /api/signals/{symbol}
│   │   │   ├── market.py               # Top coins, klines, regime
│   │   │   ├── training.py             # Data ingestion & model training
│   │   │   ├── backtest.py             # Strategy backtesting
│   │   │   └── admin.py                # Model management & kill switch
│   │   ├── core/
│   │   │   ├── data_pipeline.py        # Multi-TF Binance data fetching
│   │   │   ├── feature_engine.py       # 48 causal features
│   │   │   ├── regime_detector.py      # Market regime classification
│   │   │   ├── target_engineer.py      # Triple barrier labeling
│   │   │   ├── system_state.py         # Per-symbol state machine
│   │   │   ├── data_store.py           # Firestore candle accumulation
│   │   │   └── job_lock.py             # Distributed job locking
│   │   ├── models/
│   │   │   ├── patch_tst.py            # PatchTST temporal embeddings
│   │   │   ├── xgboost_model.py        # XGBoost decision model
│   │   │   ├── training/
│   │   │   │   ├── trainer.py          # End-to-end training pipeline
│   │   │   │   ├── walk_forward.py     # Walk-forward CV
│   │   │   │   └── training_run.py     # Artifact & metadata capture
│   │   │   └── registry/
│   │   │       ├── model_registry.py   # Central model registry
│   │   │       ├── champion_challenger.py  # Promotion gates
│   │   │       └── demotion.py         # Auto-demotion logic
│   │   ├── evaluation/
│   │   │   ├── baselines.py            # Benchmark strategies
│   │   │   ├── forward_engine.py       # Forward-only evaluation
│   │   │   └── metrics.py              # Sharpe, Sortino, Calmar, etc.
│   │   ├── strategy/
│   │   │   ├── signal_generator.py     # Signal pipeline + filters
│   │   │   ├── position_sizer.py       # Dynamic position sizing
│   │   │   └── trade_levels.py         # ATR-based SL/TP levels
│   │   ├── capital/
│   │   │   ├── controller.py           # Capital survival layer
│   │   │   └── killswitch.py           # Emergency stop mechanism
│   │   ├── governance/
│   │   │   ├── versioning.py           # Dataset versioning (SHA256)
│   │   │   └── lineage.py              # Full model lineage tracking
│   │   ├── services/                   # Binance, indicators, OpenAI
│   │   ├── routes/                     # User settings & profile
│   │   └── utils/
│   │       └── encryption.py           # Fernet encryption for API keys
│   ├── jobs/
│   │   └── retrain.py                  # Scheduled retraining job
│   ├── model_registry/
│   │   └── registry.json               # Model registry snapshot
│   ├── runs/                           # Training run artifacts
│   ├── Dockerfile
│   └── requirements.txt
│
├── update.py                           # Discord trading bot
├── firebase.json                       # Firebase Hosting config
└── README.md
```

---

## Getting Started

### Prerequisites

- **Node.js** 18+
- **Python** 3.9+
- **Firebase** project with Authentication (Email/Password) and Firestore enabled
- **Binance** API key and secret
- A Firebase service account JSON file

### 1. Clone the Repository

```bash
git clone https://github.com/hzaid01/Valoratrade.com.git
cd Valoratrade.com
```

### 2. Backend Setup

```bash
cd backend

# Create and activate virtual environment
python -m venv venv
source venv/bin/activate        # macOS/Linux
venv\Scripts\activate           # Windows

# Install dependencies
pip install -r requirements.txt

# Create .env file (see Environment Variables section)
cp .env.example .env
# Edit .env with your credentials

# Start the server
uvicorn app.main:app --reload --port 8000
```

The API will be available at **http://localhost:8000**

### 3. Frontend Setup

```bash
cd frontend

# Install dependencies
npm install

# Create .env file
# Add your Firebase web config and API URL (see below)

# Start dev server
npm run dev
```

The app will be available at **http://localhost:5173**

---

## Environment Variables

### Backend (`backend/.env`)

| Variable | Required | Description |
|---|---|---|
| `GOOGLE_APPLICATION_CREDENTIALS` | Yes* | Path to Firebase service account JSON |
| `FIREBASE_SERVICE_ACCOUNT_JSON` | Yes* | Inline service account JSON (Cloud Run) |
| `BINANCE_API_KEY` | Yes | Binance API key |
| `BINANCE_API_SECRET` | Yes | Binance API secret |
| `ENCRYPTION_SECRET` | Yes | Fernet key for encrypting user API keys |
| `ALLOWED_ORIGINS` | Yes | Comma-separated CORS origins |
| `BINANCE_TESTNET` | No | Use Binance testnet (default: false) |
| `SIGNAL_CONFIDENCE_THRESHOLD` | No | Min confidence for signals (default: 0.60) |
| `MAX_EXPOSURE` | No | Max capital exposure (default: 0.30) |
| `MAX_CONCURRENT` | No | Max concurrent trades (default: 3) |
| `MAX_DRAWDOWN` | No | Max drawdown threshold (default: 0.15) |

*One of these is required depending on the environment.

### Frontend (`frontend/.env`)

```env
VITE_FIREBASE_API_KEY=your_api_key
VITE_FIREBASE_AUTH_DOMAIN=your_project.firebaseapp.com
VITE_FIREBASE_PROJECT_ID=your_project_id
VITE_FIREBASE_STORAGE_BUCKET=your_project.appspot.com
VITE_FIREBASE_MESSAGING_SENDER_ID=your_sender_id
VITE_FIREBASE_APP_ID=your_app_id
VITE_API_URL=http://localhost:8000
```

---

## Usage

1. **Sign Up** — Create an account at the signup page
2. **Login** — Authenticate with your email and password
3. **Dashboard** — Browse the top 100 cryptocurrencies by 24h trading volume
4. **Analyze** — Click any coin to get an AI-powered signal with:
   - Live candlestick chart (15m / 1h / 4h timeframes)
   - Market regime indicator (Trending, Ranging, Volatile)
   - Signal recommendation (LONG / SHORT / NO_TRADE) with confidence score
   - Trade levels (entry, stop-loss, 3 take-profit targets)
   - Technical indicators (RSI, MACD, EMA)
   - Whale activity monitor ($50K+ transactions)
5. **System Status** — Monitor capital health, model performance, and forward evaluation metrics
6. **Settings** — Optionally add your Binance and OpenAI API keys for live mode

---

## API Reference

### Signal Endpoints
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/signals/{symbol}` | Get AI trading signal for a symbol |

### Market Endpoints
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/market/top-coins?limit=100` | Top coins by 24h volume |
| `GET` | `/api/market/klines/{symbol}?interval=1h&limit=500` | OHLCV candlestick data |
| `GET` | `/api/market/regime/{symbol}` | Market regime classification |

### Training Endpoints
| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/training/ingest-data` | Fetch and store latest candles |
| `POST` | `/api/training/trigger` | Start model training |
| `GET` | `/api/training/status` | Training job status |
| `GET` | `/api/training/data-stats` | Candle count and readiness |

### Admin Endpoints
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/admin/models` | List all registered models |
| `GET` | `/api/admin/champion/{symbol}` | Current champion model |
| `POST` | `/api/admin/killswitch` | Activate/reset kill switch |
| `GET` | `/api/admin/capital-state` | Equity, drawdown, exposure |

### User Endpoints
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/user/settings` | Get user API key status (masked) |
| `POST` | `/api/user/settings` | Update encrypted API keys |

### Backtest Endpoints
| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/backtest/run` | Run strategy backtest with baselines |

### System Endpoints
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Health check with uptime |
| `GET` | `/debug/status` | Comprehensive system debug info |

---

## Model Stack

### PatchTST (Temporal Embeddings)

The PatchTST model processes 168 hourly candles (1 week) as patched time-series:

- **Input:** (batch, 168, 5) — OHLCV candles
- **Patches:** 16 candles per patch, stride 8
- **Architecture:** 2-layer Transformer encoder, 4 attention heads, 64-dim embeddings
- **Output:** 64-dimensional temporal embeddings per patch
- **Training:** Reconstructive loss (MSE on patch reconstruction)

> PatchTST extracts temporal patterns — it does **not** predict prices directly.

### XGBoost (Decision Model)

Takes PatchTST embeddings + 48 engineered features (~120 total inputs) and produces multi-target predictions:

| Output | Description |
|---|---|
| `prob_up` | Probability of profitable upward move (0–1) |
| `prob_down` | Probability of profitable downward move (0–1) |
| `expected_return` | Expected return magnitude |
| `volatility_score` | Risk estimate (0–1) |

**Config:** 200 estimators, max depth 6, learning rate 0.05, walk-forward CV with 5 splits.

### Signal Pipeline

```
1H Candles → Feature Engine (48 features) → Regime Detector
     │                                            │
     └──→ PatchTST → Embeddings ──┐               │
                                  ├──→ XGBoost → Predictions
     Features ────────────────────┘               │
                                                  ▼
                                        Signal Generator
                                   (confidence ≥ 60%, vol ≤ 80%)
                                                  │
                                                  ▼
                                        Capital Controller
                                   (exposure, drawdown, kill switch)
                                                  │
                                                  ▼
                                    LONG / SHORT / NO_TRADE
```

---

## Capital & Risk Management

| Control | Threshold | Behavior |
|---|---|---|
| Max Exposure | 30% | No new trades above limit |
| Max Concurrent | 3 positions | Queue new signals |
| Max Drawdown | 15% | Kill switch activates |
| Drawdown Throttle | 12% | Position sizes halved |
| Confidence Floor | 60% | Signals below are rejected |
| Correlation Limit | 70% | No overlapping positions |
| Base Risk | 2% per trade | Adjusted by confidence, volatility, regime |
| Max Position | 10% of capital | Hard cap per trade |

### Kill Switch States

| State | Trading | Description |
|---|---|---|
| **ACTIVE** | ✅ Full | Normal operations |
| **THROTTLED** | ⚠️ Reduced | 50% position sizes |
| **BASELINE_ONLY** | ⚠️ Limited | Only baseline strategies |
| **KILLED** | ❌ None | All trading stopped, manual reset only |

---

## Deployment

### Cloud Run (Backend)

```bash
cd backend
gcloud run deploy trading-api \
  --source . \
  --region asia-south1 \
  --memory 2Gi \
  --cpu 2 \
  --min-instances 0 \
  --max-instances 3 \
  --concurrency 80
```

### Firebase Hosting (Frontend)

```bash
cd frontend
npm run build
firebase deploy --only hosting
```

### Cloud Scheduler Jobs

| Job | Schedule | Endpoint |
|---|---|---|
| Data Ingestion | Every 15 minutes | `POST /api/training/ingest-data` |
| Model Training | Every 6 hours | `POST /api/training/trigger` |

---

## Discord Bot

The `update.py` file includes a standalone Discord bot that provides trading signals via the `!signal <symbol>` command. It uses the same LSTM model, technical indicators, and optional OpenAI integration with a majority-vote system.

```bash
# Set environment variables
export BINANCE_API_KEY=your_key
export BINANCE_API_SECRET=your_secret
export DISCORD_BOT_TOKEN=your_token
export OPENAI_API_KEY=your_key  # optional

# Run the bot
python update.py
```

---

## Security

- 🔒 **API keys encrypted** with Fernet (AES-128-CBC + HMAC) before Firestore storage
- 🔒 **Firebase ID tokens** validated on every API request
- 🔒 **Security headers** (X-Content-Type-Options, X-Frame-Options) on all responses
- 🔒 **Rate limiting** at 100 requests/minute
- 🔒 **CORS** restricted to allowed origins
- 🔒 **No secrets in code** — all credentials via environment variables
- 🔒 **Strict startup validation** — server refuses to start with missing config

---

## License

MIT
