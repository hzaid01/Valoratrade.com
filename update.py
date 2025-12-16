#!/usr/bin/env python3
"""
Sanitized copy of the trading bot. Secrets are loaded from environment variables.
This file is a cleaned version intended for publishing. Do NOT commit real secrets.
"""
import requests
import os
import re
import random
import pandas as pd
import numpy as np
import ta
import time
import threading
import torch
import torch.nn as nn
import torch.optim as optim
from sklearn.preprocessing import MinMaxScaler
from binance.client import Client
import discord
from discord.ext import commands
import joblib

# --------- CONFIG (loaded from env) ---------
api_key = os.getenv('BINANCE_API_KEY')
api_secret = os.getenv('BINANCE_API_SECRET')
DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')

if not api_key or not api_secret:
    print('Warning: BINANCE_API_KEY or BINANCE_API_SECRET not set. Binance client will fail until set.')

client = Client(api_key, api_secret)

# ---- Utilities to safely store/load scalers ----
MODEL_DIR = "models"
os.makedirs(MODEL_DIR, exist_ok=True)

def save_model_and_scaler(model, scaler, symbol):
    torch.save(model.state_dict(), f"{MODEL_DIR}/model_{symbol}.pt")
    joblib.dump(scaler, f"{MODEL_DIR}/scaler_{symbol}.pkl")

def load_model_and_scaler(symbol, device="cpu"):
    model_path = f"{MODEL_DIR}/model_{symbol}.pt"
    scaler_path = f"{MODEL_DIR}/scaler_{symbol}.pkl"
    model = LSTMModel()
    if not os.path.exists(model_path) or not os.path.exists(scaler_path):
        raise FileNotFoundError(f"Model or scaler not found for {symbol}")
    try:
        state = torch.load(model_path, map_location=device, weights_only=True)
    except TypeError:
        # Fallback for older torch versions
        state = torch.load(model_path, map_location=device)
    model.load_state_dict(state)
    model.eval()
    scaler = joblib.load(scaler_path)
    return model, scaler

# --------- LSTM MODEL ---------
class LSTMModel(nn.Module):
    def __init__(self):
        super(LSTMModel, self).__init__()
        self.lstm1 = nn.LSTM(input_size=1, hidden_size=50, batch_first=True)
        self.lstm2 = nn.LSTM(input_size=50, hidden_size=50, batch_first=True)
        self.fc = nn.Linear(50, 1)

    def forward(self, x):
        out, _ = self.lstm1(x)
        out, _ = self.lstm2(out)
        out = self.fc(out[:, -1, :])
        return out

# --------- DATA COLLECTION ---------
def get_top_100_symbols():
    try:
        tickers = client.get_ticker()
        sorted_tickers = sorted(tickers, key=lambda x: float(x['quoteVolume']), reverse=True)
        usdt_pairs = [t['symbol'] for t in sorted_tickers if t['symbol'].endswith('USDT')]
        return usdt_pairs[:100]
    except Exception as e:
        print("Error fetching symbols:", e)
        return ['BTCUSDT', 'ETHUSDT']

SYMBOLS = get_top_100_symbols()

# --------- LSTM UTILS ---------
def collect_lstm_data(symbol):
    klines = client.get_klines(symbol=symbol, interval=Client.KLINE_INTERVAL_1DAY, limit=100)
    df = pd.DataFrame(klines, columns=['Open Time','Open','High','Low','Close','Volume','Close Time','Quote Volume','Trades','Taker Buy Base','Taker Buy Quote','Ignore'])
    df['Close'] = pd.to_numeric(df['Close'])
    return df[['Close']]

def preprocess_lstm(df):
    scaler = MinMaxScaler()
    scaled = scaler.fit_transform(df)
    X, y = [], []
    for i in range(len(scaled) - 60):
        X.append(scaled[i:i+60])
        y.append(scaled[i+60])
    return np.array(X), np.array(y), scaler

def train_model(X, y, scaler, symbol, epochs=20, device="cpu"):
    X_tensor = torch.tensor(X, dtype=torch.float32).to(device)
    y_tensor = torch.tensor(y, dtype=torch.float32).squeeze(-1).to(device)
    model = LSTMModel().to(device)
    criterion = nn.MSELoss()
    optimizer = optim.Adam(model.parameters(), lr=1e-3)
    model.train()
    for epoch in range(epochs):
        optimizer.zero_grad()
        output = model(X_tensor).squeeze(-1)
        loss = criterion(output, y_tensor)
        loss.backward()
        optimizer.step()
    # save model and scaler for later
    save_model_and_scaler(model, scaler, symbol)
    model.eval()
    return model

def load_trained_model(symbol):
    model, _ = load_model_and_scaler(symbol)
    return model

def get_lstm_signal(model, scaler, df):
    # Ensure DataFrame with the same feature name the scaler was fit on
    if isinstance(df, np.ndarray):
        last_60 = pd.DataFrame(df[-60:], columns=['Close'])
    else:
        last_60 = pd.DataFrame(df[-60:], columns=['Close'])
    scaled = scaler.transform(last_60)
    X_input = torch.tensor(scaled.reshape(1, 60, 1), dtype=torch.float32)
    with torch.no_grad():
        pred = model(X_input).item()
    prev = scaled[-1][0]
    if pred > prev:
        return 'LONG'
    elif pred < prev:
        return 'SHORT'
    else:
        return 'HOLD'

# --------- INDICATORS ---------
def get_candles(symbol, interval='1h', limit=150):
    url = f'https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}'
    raw = requests.get(url, timeout=10).json()
    df = pd.DataFrame(raw, columns=[
        'OpenTime','Open','High','Low','Close','Volume','CloseTime','QuoteAssetVolume','Trades','TakerBuyBase','TakerBuyQuote','Ignore'])
    for c in ['Open','High','Low','Close','Volume','QuoteAssetVolume','Trades','TakerBuyBase','TakerBuyQuote']:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df.dropna(inplace=True)
    return df

def detect_support_resistance(df):
    support = df['Low'].rolling(20).min().iloc[-1]
    resistance = df['High'].rolling(20).max().iloc[-1]
    return support, resistance

def analyze_indicators(df):
    df = df.copy()
    df['RSI'] = ta.momentum.RSIIndicator(df['Close']).rsi()
    df['EMA20'] = ta.trend.ema_indicator(df['Close'], window=20)
    df['EMA50'] = ta.trend.ema_indicator(df['Close'], window=50)
    df['MACD_DIFF'] = ta.trend.macd_diff(df['Close'])
    df = df.dropna()
    if len(df) == 0:
        return 'HOLD'
    macd_val = df['MACD_DIFF'].iloc[-1]
    rsi = df['RSI'].iloc[-1]
    ema_cross = df['EMA20'].iloc[-1] > df['EMA50'].iloc[-1]
    if rsi < 30 and ema_cross and macd_val > 0:
        return 'LONG'
    elif rsi > 70 and not ema_cross and macd_val < 0:
        return 'SHORT'
    else:
        return 'HOLD'

# --------- TRADE LEVELS ---------
def calculate_trade_levels(signal, price, support, resistance):
    pad = (resistance - support) * 0.05
    if signal == 'LONG':
        entry = price
        sl = support - pad
        tp1 = entry + (entry - sl)
        tp2 = entry + 2.5 * (entry - sl)
        tp3 = entry + 4 * (entry - sl)
    elif signal == 'SHORT':
        entry = price
        sl = resistance + pad
        tp1 = entry - (sl - entry)
        tp2 = entry - 2.5 * (sl - entry)
        tp3 = entry - 4 * (sl - entry)
    else:
        return None
    return {'entry': round(entry, 4), 'sl': round(sl, 4), 'tp1': round(tp1, 4), 'tp2': round(tp2, 4), 'tp3': round(tp3, 4)}

# --------- OPENAI INTEGRATION ---------
_openai_last_call = 0
_OPENAI_MIN_INTERVAL = 1.2
_openai_lock = threading.Lock()

def ask_openai(symbol, lstm_sig, indicator_sig, price, max_retries=5):
    global _openai_last_call
    prompt = f"""
Symbol: {symbol}
LSTM Prediction: {lstm_sig}
Technical Indicators: {indicator_sig}
Current Price: {price}

Based on the above information, should we take a LONG or SHORT position?
Answer only with LONG or SHORT.
"""
    api_key_env = os.getenv('OPENAI_API_KEY')
    if not api_key_env:
        print('OPENAI_API_KEY not set; skipping OpenAI call and returning None')
        return None
    headers = {"Authorization": f"Bearer {api_key_env}", "Content-Type": "application/json"}
    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": "You are a crypto trading assistant."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.0,
        "max_tokens": 6,
        "user": symbol
    }

    with _openai_lock:
        elapsed = time.time() - _openai_last_call
        if elapsed < _OPENAI_MIN_INTERVAL:
            time.sleep(_OPENAI_MIN_INTERVAL - elapsed)
        _openai_last_call = time.time()

    backoff_base = 1.5
    for attempt in range(max_retries):
        try:
            resp = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload, timeout=15)
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 0))
                wait = retry_after if retry_after > 0 else min(30, backoff_base ** attempt + random.random())
                print(f"OpenAI 429. Waiting {wait:.1f}s (attempt {attempt+1})")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            content_raw = resp.json()['choices'][0]['message']['content']
            content = content_raw.strip().upper()
            if content in ("LONG", "SHORT"):
                return content
            if re.search(r"\bLONG\b", content) and not re.search(r"\bSHORT\b", content):
                return "LONG"
            if re.search(r"\bSHORT\b", content) and not re.search(r"\bLONG\b", content):
                return "SHORT"
            print("OpenAI returned unexpected content:", content_raw)
            return None
        except requests.exceptions.RequestException as e:
            wait = min(30, backoff_base ** attempt + random.random())
            print(f"OpenAI request error: {e}. Backing off {wait:.1f}s (attempt {attempt+1})")
            time.sleep(wait)
    print("OpenAI: exhausted retries, returning None")
    return None

# --------- TRAINING THREAD ---------
def train_all_models():
    while True:
        for symbol in SYMBOLS:
            try:
                df = collect_lstm_data(symbol)
                X, y, scaler = preprocess_lstm(df)
                train_model(X, y, scaler, symbol)
                print(f"Trained LSTM for {symbol}")
            except Exception as e:
                print(f"Error training model for {symbol}: {e}")
        print("Completed training all models, sleeping 1 hour...")
        time.sleep(3600)

# NOTE: Training runs were started in the original file. To avoid accidental heavy work
# the training thread is NOT started automatically here. Uncomment the following line
# to enable background training (only if you understand the resource cost):
# threading.Thread(target=train_all_models, daemon=True).start()

# --------- DISCORD BOT SETUP ---------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

_active_signals = {}
_active_lock = threading.Lock()
DEBOUNCE_SECONDS = 3.0

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')

@bot.command()
async def signal(ctx, symbol: str):
    symbol = symbol.upper()
    if not symbol.endswith("USDT"):
        symbol += "USDT"

    with _active_lock:
        last = _active_signals.get(symbol)
        now = time.time()
        if last and now - last < DEBOUNCE_SECONDS:
            await ctx.send(f"Signal for {symbol} is already being processed. Try again in a few seconds.")
            return
        _active_signals[symbol] = now

    try:
        await ctx.send(f"Fetching data and analyzing for {symbol}...")
        df = get_candles(symbol)
        support, resistance = detect_support_resistance(df)
        price = float(df['Close'].iloc[-1])

        # Load LSTM model and scaler
        try:
            model, scaler = load_model_and_scaler(symbol)
        except Exception as e:
            print("Model load error:", e)
            await ctx.send(f"No trained model available for {symbol}.")
            return

        lstm_sig = get_lstm_signal(model, scaler, df[['Close']].values)
        indicator_sig = analyze_indicators(df)
        openai_sig = ask_openai(symbol, lstm_sig, indicator_sig, price)

        # Decide final signal using majority vote with fallback
        signals = [s for s in [openai_sig, lstm_sig, indicator_sig] if s in ['LONG', 'SHORT']]
        if signals:
            long_count = signals.count('LONG')
            short_count = signals.count('SHORT')
            if long_count > short_count:
                final_sig = 'LONG'
            elif short_count > long_count:
                final_sig = 'SHORT'
            else:
                final_sig = indicator_sig if indicator_sig in ['LONG', 'SHORT'] else (lstm_sig if lstm_sig in ['LONG', 'SHORT'] else 'HOLD')
        else:
            final_sig = 'HOLD'

        print(f"Signals -> LSTM: {lstm_sig}, IND: {indicator_sig}, AI: {openai_sig}, FINAL: {final_sig}")

        levels = calculate_trade_levels(final_sig, price, support, resistance)

        if not levels:
            await ctx.send(f"No trade signal for {symbol}. Hold position.")
            return

        msg = (
            f"🔱 Trade: #{symbol}\n"
            f"🟢 Signal: {final_sig}\n"
            f"⚡ Entry Zone: {levels['entry']}\n"
            f"⛔ Stop-Loss: {levels['sl']}\n"
            f"🎯 Take Profits:\n"
            f"    TP1: {levels['tp1']}\n"
            f"    TP2: {levels['tp2']}\n"
            f"    TP3: {levels['tp3']}\n"
            f"🀄 Leverage: 20x"
        )
        await ctx.send(msg)

    except Exception as e:
        await ctx.send(f"Error processing signal for {symbol}: {e}")
    finally:
        with _active_lock:
            _active_signals.pop(symbol, None)


def main():
    if not DISCORD_BOT_TOKEN:
        print('DISCORD_BOT_TOKEN not set. Bot will not start.')
        return
    bot.run(DISCORD_BOT_TOKEN)

if __name__ == '__main__':
    main()
