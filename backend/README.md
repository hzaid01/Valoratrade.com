# CryptoBot Backend

FastAPI backend for cryptocurrency trading signal analysis using LSTM models, technical indicators, and OpenAI.

## Features

- **Market Data**: Fetch top 100 cryptocurrencies from Binance
- **Technical Analysis**: RSI, MACD, EMA indicators
- **LSTM Model**: PyTorch-based prediction model
- **OpenAI Integration**: AI-powered trading decisions
- **Breaker Blocks**: Supply/demand zone detection
- **Trade Calculator**: Entry, stop loss, and take profit calculations
- **User API Keys**: Secure storage of user credentials

## Tech Stack

- FastAPI - Modern Python web framework
- PyTorch - Deep learning framework
- python-binance - Binance API client
- OpenAI - AI decision making
- Supabase - Database and authentication
- Technical Analysis library (ta)
- Pandas & NumPy - Data processing

## Installation

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

## Configuration

Create a `.env` file:

```
SUPABASE_URL=your_supabase_url
SUPABASE_ANON_KEY=your_supabase_anon_key
```

## Running the Server

```bash
# Development mode with auto-reload
uvicorn app.main:app --reload --port 8000

# Production mode
gunicorn app.main:app -w 4 -k uvicorn.workers.UvicornWorker
```

## API Endpoints

### Market Endpoints

#### Get Top Coins
```http
GET /api/market/top-coins?limit=100
```

Returns top cryptocurrencies by trading volume.

**Response:**
```json
{
  "success": true,
  "data": [
    {
      "symbol": "BTCUSDT",
      "price": 43250.50,
      "change_24h": 2.45,
      "volume": 25000,
      "quote_volume": 1081263750
    }
  ]
}
```

#### Analyze Symbol
```http
GET /api/market/analyze/{symbol}
Authorization: Bearer <token>
```

Get comprehensive analysis for a cryptocurrency symbol.

**Parameters:**
- `symbol` - Trading pair (e.g., BTCUSDT)

**Response:**
```json
{
  "success": true,
  "data": {
    "symbol": "BTCUSDT",
    "current_price": 43250.50,
    "indicators": {
      "rsi": 65.5,
      "macd": {...},
      "ema": {...}
    },
    "support_resistance": {
      "support": 42000,
      "resistance": 44000
    },
    "breaker_blocks": [...],
    "lstm_signal": {
      "signal": "LONG",
      "confidence": 0.85
    },
    "ai_decision": {
      "signal": "LONG",
      "reason": "..."
    },
    "final_signal": "LONG",
    "trade_setup": {
      "entry_price": 43250,
      "stop_loss": 41580,
      "take_profit_1": 45755,
      "take_profit_2": 47425,
      "take_profit_3": 50095
    },
    "mode": "live"
  }
}
```

### User Endpoints

#### Get User Settings
```http
GET /api/user/settings
Authorization: Bearer <token>
```

Returns user's API keys.

#### Update User Settings
```http
POST /api/user/settings
Authorization: Bearer <token>
Content-Type: application/json

{
  "binance_api_key": "string",
  "binance_secret_key": "string",
  "openai_api_key": "string"
}
```

## Project Structure

```
backend/
├── app/
│   ├── models/
│   │   └── lstm_model.py      # LSTM neural network
│   ├── services/
│   │   ├── binance_service.py  # Binance API integration
│   │   ├── indicators.py       # Technical indicators
│   │   ├── openai_service.py   # OpenAI integration
│   │   └── trade_calculator.py # Trade setup calculator
│   ├── routes/
│   │   ├── market.py          # Market endpoints
│   │   └── user.py            # User endpoints
│   └── main.py                # FastAPI application
├── requirements.txt           # Python dependencies
└── .env                      # Environment variables
```

## LSTM Model

The LSTM model is a PyTorch-based neural network with:
- Input size: 10 features (indicators)
- Hidden layers: 2 layers with 50 units
- Output: 3 classes (HOLD, LONG, SHORT)
- Activation: Softmax

### Input Features:
1. RSI (normalized)
2. MACD value
3. MACD signal
4. MACD histogram
5. EMA 9
6. EMA 21
7. EMA 50
8. Close price
9. Volume
10. Price range (high - low)

## Technical Indicators

### RSI (Relative Strength Index)
- Window: 14 periods
- Overbought: > 70
- Oversold: < 30

### MACD (Moving Average Convergence Divergence)
- Fast period: 12
- Slow period: 26
- Signal period: 9

### EMA (Exponential Moving Average)
- Short: 9 periods
- Medium: 21 periods
- Long: 50 periods

## Breaker Blocks

Detects supply and demand zones by identifying:
- Bullish breaker: Current low > Previous high
- Bearish breaker: Current high < Previous low

## Trade Setup Calculator

Calculates trade parameters based on signal:

**For LONG positions:**
- Entry: Current price
- Stop Loss: 2% below support
- TP1: Entry + 1.5x risk
- TP2: Entry + 2.5x risk
- TP3: Entry + 4.0x risk

**For SHORT positions:**
- Entry: Current price
- Stop Loss: 2% above resistance
- TP1: Entry - 1.5x risk
- TP2: Entry - 2.5x risk
- TP3: Entry - 4.0x risk

## Error Handling

The API includes comprehensive error handling:
- Binance API failures → Fallback to mock data
- OpenAI API failures → Fallback to rule-based decisions
- Missing user keys → Use simulated mode
- Invalid symbols → HTTP 404 error
- Authentication errors → HTTP 401 error

## Dependencies

Key Python packages:
- fastapi==0.104.1
- uvicorn[standard]==0.24.0
- torch==2.1.0
- numpy==1.24.3
- pandas==2.1.3
- ta==0.11.0
- python-binance==1.0.19
- openai==1.3.5
- supabase==2.0.3

## Development

```bash
# Run with auto-reload
uvicorn app.main:app --reload

# Run tests (if implemented)
pytest

# Format code
black app/

# Lint code
flake8 app/
```

## Production Deployment

1. Set environment variables
2. Use production ASGI server (gunicorn)
3. Enable HTTPS
4. Configure CORS for your domain
5. Set up logging and monitoring
6. Use a reverse proxy (nginx)

## License

MIT
