from typing import Dict

def calculate_trade_setup(
    signal: str,
    current_price: float,
    support: float,
    resistance: float,
    risk_percent: float = 2.0
) -> Dict:
    if signal == "LONG":
        entry = current_price
        stop_loss = support * 0.98

        risk = entry - stop_loss
        tp1 = entry + (risk * 1.5)
        tp2 = entry + (risk * 2.5)
        tp3 = entry + (risk * 4.0)

        return {
            "entry_price": round(entry, 2),
            "stop_loss": round(stop_loss, 2),
            "take_profit_1": round(tp1, 2),
            "take_profit_2": round(tp2, 2),
            "take_profit_3": round(tp3, 2),
            "risk_reward_ratio": "1:1.5, 1:2.5, 1:4.0"
        }

    elif signal == "SHORT":
        entry = current_price
        stop_loss = resistance * 1.02

        risk = stop_loss - entry
        tp1 = entry - (risk * 1.5)
        tp2 = entry - (risk * 2.5)
        tp3 = entry - (risk * 4.0)

        return {
            "entry_price": round(entry, 2),
            "stop_loss": round(stop_loss, 2),
            "take_profit_1": round(tp1, 2),
            "take_profit_2": round(tp2, 2),
            "take_profit_3": round(tp3, 2),
            "risk_reward_ratio": "1:1.5, 1:2.5, 1:4.0"
        }

    else:
        return {
            "entry_price": round(current_price, 2),
            "stop_loss": 0,
            "take_profit_1": 0,
            "take_profit_2": 0,
            "take_profit_3": 0,
            "risk_reward_ratio": "N/A"
        }
