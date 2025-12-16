"""
LSTM Service for cryptocurrency price prediction and trading signals.
Adapted from update.py trading bot logic, without Discord dependencies.
"""
import os
import logging
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.optim as optim
from sklearn.preprocessing import MinMaxScaler
import joblib
from typing import Tuple, Optional

logger = logging.getLogger(__name__)

# Model directory for saving trained models
MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "trained_models")
os.makedirs(MODEL_DIR, exist_ok=True)


class LSTMModel(nn.Module):
    """LSTM Neural Network for price prediction."""
    
    def __init__(self, input_size: int = 1, hidden_size: int = 50):
        super(LSTMModel, self).__init__()
        self.lstm1 = nn.LSTM(input_size=input_size, hidden_size=hidden_size, batch_first=True)
        self.lstm2 = nn.LSTM(input_size=hidden_size, hidden_size=hidden_size, batch_first=True)
        self.fc = nn.Linear(hidden_size, 1)
    
    def forward(self, x):
        out, _ = self.lstm1(x)
        out, _ = self.lstm2(out)
        out = self.fc(out[:, -1, :])
        return out


def save_model_and_scaler(model: LSTMModel, scaler: MinMaxScaler, symbol: str) -> None:
    """Save trained model and scaler to disk."""
    try:
        torch.save(model.state_dict(), os.path.join(MODEL_DIR, f"model_{symbol}.pt"))
        joblib.dump(scaler, os.path.join(MODEL_DIR, f"scaler_{symbol}.pkl"))
        logger.info(f"Saved model and scaler for {symbol}")
    except Exception as e:
        logger.error(f"Error saving model for {symbol}: {e}")


def load_model_and_scaler(symbol: str, device: str = "cpu") -> Tuple[LSTMModel, MinMaxScaler]:
    """Load trained model and scaler from disk."""
    model_path = os.path.join(MODEL_DIR, f"model_{symbol}.pt")
    scaler_path = os.path.join(MODEL_DIR, f"scaler_{symbol}.pkl")
    
    if not os.path.exists(model_path) or not os.path.exists(scaler_path):
        raise FileNotFoundError(f"Model or scaler not found for {symbol}")
    
    model = LSTMModel()
    try:
        state = torch.load(model_path, map_location=device, weights_only=True)
    except TypeError:
        # Fallback for older torch versions
        state = torch.load(model_path, map_location=device)
    
    model.load_state_dict(state)
    model.eval()
    scaler = joblib.load(scaler_path)
    
    return model, scaler


def preprocess_data(df: pd.DataFrame, sequence_length: int = 60) -> Tuple[np.ndarray, np.ndarray, MinMaxScaler]:
    """Preprocess data for LSTM training."""
    # Ensure we have Close column
    if 'Close' in df.columns:
        close_data = df[['Close']]
    elif 'close' in df.columns:
        close_data = df[['close']].rename(columns={'close': 'Close'})
    else:
        raise ValueError("DataFrame must have 'Close' or 'close' column")
    
    scaler = MinMaxScaler()
    scaled = scaler.fit_transform(close_data)
    
    X, y = [], []
    for i in range(len(scaled) - sequence_length):
        X.append(scaled[i:i + sequence_length])
        y.append(scaled[i + sequence_length])
    
    return np.array(X), np.array(y), scaler


def train_model(
    X: np.ndarray,
    y: np.ndarray,
    scaler: MinMaxScaler,
    symbol: str,
    epochs: int = 20,
    device: str = "cpu"
) -> LSTMModel:
    """Train LSTM model on provided data."""
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
        
        if (epoch + 1) % 5 == 0:
            logger.info(f"Epoch {epoch + 1}/{epochs}, Loss: {loss.item():.6f}")
    
    # Save model and scaler
    save_model_and_scaler(model, scaler, symbol)
    model.eval()
    
    return model


def get_lstm_signal(
    model: Optional[LSTMModel],
    scaler: Optional[MinMaxScaler],
    df: pd.DataFrame,
    sequence_length: int = 60
) -> Tuple[str, float]:
    """
    Get LSTM prediction signal from model.
    Returns: (signal: 'LONG'/'SHORT'/'HOLD', confidence: 0.0-1.0)
    """
    if model is None or scaler is None:
        # Return default signal if no trained model
        return "HOLD", 0.5
    
    try:
        # Get close prices
        if 'Close' in df.columns:
            close_data = df[['Close']].tail(sequence_length)
        elif 'close' in df.columns:
            close_data = df[['close']].tail(sequence_length).rename(columns={'close': 'Close'})
        else:
            return "HOLD", 0.5
        
        if len(close_data) < sequence_length:
            return "HOLD", 0.5
        
        # Scale data
        scaled = scaler.transform(close_data)
        X_input = torch.tensor(scaled.reshape(1, sequence_length, 1), dtype=torch.float32)
        
        # Predict
        with torch.no_grad():
            pred = model(X_input).item()
        
        prev = scaled[-1][0]
        diff = pred - prev
        confidence = min(abs(diff) * 10, 1.0)  # Scale difference to confidence
        
        if pred > prev * 1.001:  # 0.1% threshold
            return "LONG", confidence
        elif pred < prev * 0.999:
            return "SHORT", confidence
        else:
            return "HOLD", 0.5
            
    except Exception as e:
        logger.error(f"Error getting LSTM signal: {e}")
        return "HOLD", 0.5


def get_signal_from_features(features: np.ndarray) -> Tuple[str, float]:
    """
    Get signal from prepared feature matrix (for compatibility with existing code).
    This is a simplified version that analyzes the feature patterns.
    """
    try:
        # Extract key indicators from features
        rsi = features[-1, 0] * 100  # RSI was normalized
        macd_histogram = features[-1, 3]
        
        # Simple rule-based signal
        if rsi < 30 and macd_histogram > 0:
            return "LONG", 0.7
        elif rsi > 70 and macd_histogram < 0:
            return "SHORT", 0.7
        elif rsi < 40 and macd_histogram > 0:
            return "LONG", 0.5
        elif rsi > 60 and macd_histogram < 0:
            return "SHORT", 0.5
        else:
            return "HOLD", 0.5
            
    except Exception as e:
        logger.error(f"Error analyzing features: {e}")
        return "HOLD", 0.5
