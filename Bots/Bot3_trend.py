import os, math
from datetime import datetime, timezone
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.data.historical import CryptoHistoricalDataClient
from alpaca.data.requests import CryptoBarsRequest
from alpaca.data.timeframe import TimeFrame
from database.db import Database

API_KEY = os.environ.get("ALPACA_API_KEY")
API_SECRET = os.environ.get("ALPACA_SECRET_KEY")
PAPER = os.environ.get("ALPACA_PAPER", "true").lower() == "true"
BOT_ID = "BOT3_TREND"
SYMBOLS = ["BTC/USD", "ETH/USD", "SOL/USD"]

def calc_ema(closes, period):
    if len(closes) < period: return closes[-1]
    k = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for p in closes[period:]: ema = p * k + ema * (1 - k)
    return ema

def calc_rsi(closes, period=14):
    if len(closes) < period + 1: return 50.0
    gains = [max(closes[i]-closes[i-1], 0) for i in range(1, len(closes))]
    losses = [max(closes[i-1]-closes[i], 0) for i in range(1, len(closes))]
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    if al == 0: return 100.0
    return 100 - 100 / (1 + ag / al)

def signal(closes_1h, closes_4h, weights):
    if len(closes_4h) < 50: return 0.0, "HOLD", "Gathering 4H data"
    e20 = calc_ema(closes_4h, 20)
    e50 = calc_ema(closes_4h, 50)
    e100 = calc_ema(closes_4h, min(100, len(closes_4h)))
    cur = closes_4h[-1]
    rsi = calc_rsi(closes_4h)
    slope = (closes_4h[-1] - closes_4h[-20]) / closes_4h[-20] if len(closes_4h) >= 20 else 0
    e9_1h = calc_ema(closes_1h, 9)
    e21_1h = calc_ema(closes_1h, 21)
    score = 0.0
    reasons = []
    if cur > e20 > e50 > e100:
        score += 3.0; reasons.append("Strong 4H bullish trend")
    elif cur < e20 < e50 < e100:
        score -= 3.0; reasons.append("Strong 4H bearish trend")
    elif cur > e20 > e50:
        score += 1.5; reasons.append("4H bullish trend")
    elif cur < e20 < e50:
        score -= 1.5; reasons.append("4H bearish trend")
    if slope > 0.03: score += 1.5; reasons.append(f"Strong upslope +{slope*100:.1f}%")
    elif slope > 0.01: score += 0.7
    elif slope < -0.03: score -= 1.5; reasons.append(f"Strong downslope {slope*100:.1f}%")
    elif slope < -0.01: score -= 0.7
    if e9_1h > e21_1h and rsi < 65: score += 1.0; reasons.append("1H confirms")
    elif e9_1h < e21_1h and rsi > 35: score -= 1.0
    if rsi > 75: score -= 1.5; reasons.append("Overbought - wait")
    if rsi < 25: score += 1.5; reasons.append("Oversold - opportunity")
    score *= weights.get("trend_weight", 1.0)
    if score >= 3.0: return score, "BUY", " | ".join(reasons[:2])
    elif score <= -2.5: return score, "SELL", " | ".join(reasons[:2])
    return score, "HOLD", reasons[0] if reasons else "No clear trend"

def run_bot():
    db = Database()
    stats = db.get_bot_stats(BOT_ID)
    weights = db.get_strategy_weights(BOT_ID)
    trading = TradingClient(API_KEY, API_SECRET, paper=PAPER)
    data_client = CryptoHistoricalDataClient(API_KEY, API_SECRET)
    account = trading.get_account()
    equity = float(account.equity)
    cash = float(account.cash)
    print(f"\n[{BOT_ID}] Equity: ${equity:.2f} | Cash: ${cash:.2f}")
    peak = stats.get("peak_equity", equity)
    if equity > peak: db.update_stat(BOT_ID, "peak_equity", equity); peak = equity
    dd = (peak - equity) / peak if peak > 0 else 0
    risk = 0.2 if dd > 0.12 else 0.5 if dd > 0.08 else 0.8 if dd > 0.04 else 1.0
    positions = {p.symbol: p for p in trading.get_all_positions()}
    trades = []
    for symbol in SYMBOLS:
        try:
            req_1h = CryptoBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame.Hour, limit=60)
            req_4h = CryptoBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame(4, "Hour"), limit=80)
            bars_1h = data_client.get_crypto_bars(req_1h)
            bars_4h = data_client.get_crypto_bars(req_4h)
            closes_1h = [float(b.close) for b in bars_1h.get(symbol, [])]
            closes_4h = [float(b.close) for b in bars_4h.get(symbol, [])]
            if len(closes_4h) < 30 or len(closes_1h) < 20: continue
            cur = closes_1h[-1]
            score, sig, reason = signal(closes_1h, closes_4h, weights)
            held = symbol in positions
            print(f"  {symbol}: ${cur:.2f} | {sig} | {reason}")
            if sig == "BUY" and not held and cash > 40:
                spend = min(equity * 0.20 * risk, equity * 0.30, cash * 0.95)
                if spend > 20:
                    qty = round(spend / cur, 6)
                    try:
                        trading.submit_order(MarketOrderRequest(
                            symbol=symbol, qty=qty,
                            side=OrderSide.BUY, time_in_force=TimeInForce.GTC))
                        trade = {"bot_id": BOT_ID, "symbol": symbol, "action": "BUY",
                                 "price": cur, "qty": qty, "value": spend,
                                 "reason": reason, "strategy": "TREND",
                                 "timestamp": datetime.now(timezone.utc).isoformat()}
                        db.save_trade(trade); trades.append(trade)
                        print(f"    BUY {qty:.6f} {symbol} @ ${cur:.2f}")
                    except Exception as e: print(f"    Order failed: {e}")
            elif held and sig == "SELL":
                pos = positions[symbol]
                qty = float(pos.qty)
                entry = float(pos.avg_entry_price)
                profit = (cur - entry) * qty
                try:
                    trading.submit_order(MarketOrderRequest(
                        symbol=symbol, qty=qty,
                        side=OrderSide.SELL, time_in_force=TimeInForce.GTC))
                    trade = {"bot_id": BOT_ID, "symbol": symbol, "action": "SELL",
                             "price": cur, "qty": qty, "value": cur * qty,
                             "profit": profit, "reason": reason, "strategy": "TREND",
                             "timestamp": datetime.now(timezone.utc).isoformat()}
                    db.save_trade(trade)
                    if profit > 0: db.increment_stat(BOT_ID, "wins")
                    else: db.increment_stat(BOT_ID, "losses")
                    trades.append(trade)
                    print(f"    SELL {symbol} profit: ${profit:.2f}")
                except Exception as e: print(f"    Sell failed: {e}")
        except Exception as e: print(f"  Error {symbol}: {e}")
    db.save_equity_snapshot(BOT_ID, equity, cash)
    print(f"\n[{BOT_ID}] Done. {len(trades)} trades.")
    return trades

if __name__ == "__main__":
    run_bot()
