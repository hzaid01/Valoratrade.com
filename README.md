# CryptoBot - AI-Powered Trading Signals

A modern full-stack web application for cryptocurrency trading analysis using LSTM models, technical indicators, and AI-powered decision making.

## Tech Stack

### Frontend
- React (Vite)
- Tailwind CSS
- Zustand (State Management)
- Supabase (Authentication & Database)
- React Router
- Lucide Icons

### Backend
- FastAPI (Python)
- PyTorch (LSTM Model)
- Binance API
- OpenAI API
- Technical Analysis (TA-Lib)
- Supabase (Database)

## Features

- **Authentication System**: Secure user login and registration
- **User Settings**: Manage API keys for Binance and OpenAI
- **Market Dashboard**: View top 100 cryptocurrencies by volume
- **Signal Analysis**: Get AI-powered trading signals with:
  - LSTM model predictions
  - Technical indicators (RSI, MACD, EMA)
  - Support/Resistance levels
  - Breaker blocks detection
  - Trade setup with entry, stop loss, and take profit levels
- **Dual Mode**: Live mode with user API keys or simulated mode with pre-trained models

## Project Structure

```
/project
├── frontend/              # React frontend
│   ├── src/
│   │   ├── components/    # Reusable components
│   │   ├── pages/         # Page components
│   │   ├── store/         # Zustand store
│   │   ├── lib/           # Utilities and API client
│   │   └── ...
│   └── ...
├── backend/               # FastAPI backend
│   ├── app/
│   │   ├── models/        # LSTM model
│   │   ├── services/      # Business logic
│   │   ├── routes/        # API endpoints
│   │   └── main.py        # FastAPI app
│   └── requirements.txt
└── README.md
```

## Setup Instructions

### Prerequisites
- Node.js 18+
- Python 3.9+
- Supabase account

### Frontend Setup

1. Navigate to frontend directory:
   ```bash
   cd frontend
   ```

2. Install dependencies:
   ```bash
   npm install
   ```

3. Create `.env` file with Supabase credentials (already configured)

4. Start development server:
   ```bash
   npm run dev
   ```

The frontend will be available at `http://localhost:5173`

### Backend Setup

1. Navigate to backend directory:
   ```bash
   cd backend
   ```

2. Create virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Create `.env` file with Supabase credentials (already configured)

5. Start FastAPI server:
   ```bash
   uvicorn app.main:app --reload --port 8000
   ```

The backend will be available at `http://localhost:8000`

## Usage

1. **Sign Up**: Create a new account on the signup page
2. **Login**: Sign in with your credentials
3. **Dashboard**: Browse top 100 cryptocurrencies
4. **Analyze**: Click "Analyze" on any coin to get AI-powered trading signals
5. **Settings**: Optionally add your Binance and OpenAI API keys for live mode

## API Endpoints

### Market Endpoints
- `GET /api/market/top-coins?limit=100` - Get top coins by volume
- `GET /api/market/analyze/{symbol}` - Get analysis for a specific symbol

### User Endpoints
- `GET /api/user/settings` - Get user API keys
- `POST /api/user/settings` - Update user API keys

## Environment Variables

### Frontend (.env)
```
VITE_SUPABASE_URL=your_supabase_url
VITE_SUPABASE_ANON_KEY=your_supabase_anon_key
VITE_API_URL=http://localhost:8000
```

### Backend (.env)
```
SUPABASE_URL=your_supabase_url
SUPABASE_ANON_KEY=your_supabase_anon_key
```

## Database Schema

The application uses Supabase PostgreSQL database with the following table:

- **user_api_keys**: Stores encrypted user API keys
  - `id` (uuid, primary key)
  - `user_id` (uuid, foreign key to auth.users)
  - `binance_api_key` (text)
  - `binance_secret_key` (text)
  - `openai_api_key` (text)
  - `created_at` (timestamptz)
  - `updated_at` (timestamptz)

Row Level Security (RLS) is enabled to ensure users can only access their own API keys.

## License

MIT
