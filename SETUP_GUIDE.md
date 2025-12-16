# CryptoBot Setup Guide

This guide will help you set up and run the CryptoBot application.

## Prerequisites

Before starting, ensure you have:

- Node.js 18+ installed
- Python 3.9+ installed
- pip (Python package manager)
- A Supabase account (database already configured)

## Quick Start

### 1. Backend Setup

Open a terminal and follow these steps:

```bash
# Navigate to the backend directory
cd backend

# Create a Python virtual environment
python -m venv venv

# Activate the virtual environment
# On macOS/Linux:
source venv/bin/activate
# On Windows:
venv\Scripts\activate

# Install Python dependencies
pip install -r requirements.txt

# Start the FastAPI server
uvicorn app.main:app --reload --port 8000
```

The backend API will be available at `http://localhost:8000`

### 2. Frontend Setup

Open a new terminal window and follow these steps:

```bash
# Navigate to the frontend directory
cd frontend

# Install Node.js dependencies (if not already done)
npm install

# Start the development server
npm run dev
```

The frontend will be available at `http://localhost:5173`

## Using the Application

### First Time Setup

1. **Create an Account**
   - Navigate to `http://localhost:5173`
   - Click "Sign up"
   - Enter your email and password
   - Click "Sign Up"

2. **Login**
   - Enter your email and password
   - Click "Sign In"

3. **View Market Dashboard**
   - After login, you'll see the top 100 cryptocurrencies
   - Use the search bar to filter coins
   - Click "Analyze" on any coin to get trading signals

### Optional: Configure API Keys

By default, the app runs in **Simulated Mode** using pre-trained models and mock data.

To use **Live Mode** with real-time data:

1. **Get API Keys**
   - Binance API: Sign up at https://www.binance.com and create API keys
   - OpenAI API: Get your key from https://platform.openai.com

2. **Add Keys to Settings**
   - Click "Settings" in the sidebar
   - Enter your Binance API Key
   - Enter your Binance Secret Key
   - Enter your OpenAI API Key
   - Click "Save Settings"

3. **Analyze Coins**
   - Go back to Dashboard
   - Click "Analyze" on any coin
   - The app will now use your API keys for live data

## Features Overview

### Dashboard
- View top 100 cryptocurrencies by trading volume
- See current price and 24-hour price change
- Quick search and filter functionality
- Click "Analyze" to get detailed trading signals

### Analysis View
- **Final AI Signal**: LONG, SHORT, or HOLD recommendation
- **LSTM Model Signal**: Machine learning prediction with confidence score
- **Technical Indicators**:
  - RSI (Relative Strength Index)
  - MACD (Moving Average Convergence Divergence)
  - EMA (Exponential Moving Averages: 9, 21, 50)
- **Support & Resistance Levels**: Key price levels to watch
- **Trade Setup**: Entry price, stop loss, and 3 take-profit targets
- **Breaker Blocks**: Supply and demand zones
- **Mode Indicator**: Shows if you're in Live or Simulated mode

### Settings
- Manage your API keys securely
- Toggle between Simulated and Live modes
- API keys are encrypted and stored securely in the database

## API Endpoints

The backend exposes these endpoints:

- `GET /api/market/top-coins?limit=100` - Get top coins
- `GET /api/market/analyze/{symbol}` - Get analysis for a specific symbol
- `GET /api/user/settings` - Get user API keys
- `POST /api/user/settings` - Update user API keys

## Troubleshooting

### Backend Issues

**Issue**: `ModuleNotFoundError`
```bash
# Make sure you're in the virtual environment
source venv/bin/activate  # macOS/Linux
venv\Scripts\activate     # Windows

# Reinstall dependencies
pip install -r requirements.txt
```

**Issue**: Port 8000 already in use
```bash
# Use a different port
uvicorn app.main:app --reload --port 8001

# Then update frontend/.env:
# VITE_API_URL=http://localhost:8001
```

### Frontend Issues

**Issue**: `npm install` fails
```bash
# Clear npm cache
npm cache clean --force

# Remove node_modules and reinstall
rm -rf node_modules package-lock.json
npm install
```

**Issue**: Port 5173 already in use
```bash
# Vite will automatically use the next available port
# Just follow the URL shown in the terminal
```

### Database Issues

**Issue**: Authentication errors
- Make sure your `.env` files have the correct Supabase credentials
- Check that the database migration was applied successfully

## Production Build

### Frontend
```bash
cd frontend
npm run build
```

The built files will be in `frontend/dist/`

### Backend
```bash
cd backend
# Use a production ASGI server like gunicorn
pip install gunicorn
gunicorn app.main:app -w 4 -k uvicorn.workers.UvicornWorker
```

## Security Notes

1. **Never commit API keys** to version control
2. The `.env` files contain sensitive information
3. API keys are encrypted in the database
4. Use environment variables for production deployments
5. Always use HTTPS in production

## Support

If you encounter any issues:

1. Check this guide for solutions
2. Review the error messages in the terminal
3. Ensure all dependencies are installed correctly
4. Verify your `.env` files are properly configured

## Project Structure

```
/project
├── backend/
│   ├── app/
│   │   ├── models/         # LSTM model
│   │   ├── services/       # Business logic
│   │   ├── routes/         # API endpoints
│   │   └── main.py         # FastAPI app
│   ├── requirements.txt    # Python dependencies
│   └── .env               # Backend environment variables
├── frontend/
│   ├── src/
│   │   ├── components/    # Reusable UI components
│   │   ├── pages/         # Page components
│   │   ├── store/         # State management
│   │   └── lib/           # Utilities and API client
│   ├── package.json       # Node dependencies
│   └── .env              # Frontend environment variables
├── README.md             # Project documentation
└── SETUP_GUIDE.md        # This file
```

Happy Trading! 🚀
