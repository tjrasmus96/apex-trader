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
BOT_ID = "BOT1_MOMENTUM"
SYMBOLS = ["BTC/USD", "ETH/USD", "SOL/USD", "AVAX/USD", "LINK/USD"]

def calc_rsi(closes, period=14):
    if len(closes) < period + 1: return 50.0
    gains = [max(closes[i]-closes[i-1], 0) for i in range(1, len(closes))]
    losses = [max(closes[i-1]-closes[i], 0) for i in range(1, len(closes))]
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    if al == 0: return 100.0
    return 100 - 100 / (1 + ag / al)

def calc_ema(closes, period):
    if len(closes) < period: return closes[-1]
    k = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for p in closes[period:]: ema = p * k + ema * (1 - k)
    return ema

def calc_atr(closes, period=14):
    if len(closes) < period + 1: return closes[-1] * 0.02
    return sum(abs(closes[i]-closes[i-1]) for i in range(1, len(closes))) / (len(closes)-1)

def kelly_size(wr, avg_win, avg_loss):
    if avg_loss == 0: return 0.05
    b = avg_win / avg_loss
    p = wr / 100
    return max(0.01, min((b*p-(1-p))/b * 0.5, 0.20))

def signal(closes, weights):
    if len(closes) < 30: return 0.0, "HOLD", "Gathering data"
    rsi = calc_rsi(closes)
    e9 = calc_ema(closes, 9)
    e21 = calc_ema(closes, 21)
    e50 = calc_ema(closes, min(50, len(closes)))
    macd = calc_ema(closes, 12) - calc_ema(closes, 26)
    score = 0.0
    reasons = []
    if rsi < 30: score += 2.5; reasons.append(f"RSI oversold {rsi:.0f}")
    elif rsi < 40: score += 1.2; reasons.append(f"RSI low {rsi:.0f}")
    elif rsi > 70: score -= 2.5; reasons.append(f"RSI overbought {rsi:.0f}")
    elif rsi > 60: score -= 1.2; reasons.append(f"RSI high {rsi:.0f}")
    if e9 > e21 > e50: score += 2.0; reasons.append("Bullish EMA stack")
    elif e9 < e21 < e50: score -= 2.0; reasons.append("Bearish EMA stack")
    if macd > 0: score += 1.0; reasons.append("MACD positive")
    else: score -= 1.0; reasons.append("MACD negative")
    score *= weights.get("momentum_weight", 1.0)
    if score >= 2.5: return score, "BUY", " | ".join(reasons[:2])
    elif score <= -2.0: return score, "SELL", " | ".join(reasons[:2])
    return score, "HOLD", reasons[0] if reasons else "No signal"

def run_bot():
    db = Database()
    stats = db.get_bot_stats(BOT_ID)
    weights = db.get_strategy_weights(BOT_ID)
    trading = TradingClient(API_KEY, API_SECRET, paper=PAPER)
    data = CryptoHistoricalDataClient(API_KEY, API_SECRET)
    account = trading.get_account()
    equity = float(account.equity)
    cash = float(account.cash)
    print(f"\n[{BOT_ID}] Equity: ${equity:.2f} | Cash: ${cash:.2f}")
    peak = stats.get("peak_equity", equity)
    if equity > peak: db.update_stat(BOT_ID, "peak_equity", equity); peak = equity
    dd = (peak - equity) / peak if peak > 0 else 0
    risk = 0.3 if dd > 0.15 else 0.5 if dd > 0.10 else 0.7 if dd > 0.06 else 1.0
    tt = stats.get("wins", 0) + stats.get("losses", 0)
    wr = stats.get("wins", 0) / tt * 100 if tt > 5 else 52.0
    k = kelly_size(wr, stats.get("avg_win_pct", 0.065), stats.get("avg_loss_pct", 0.033))
    positions = {p.symbol: p for p in trading.get_all_positions()}
    trades = []
    for symbol in SYMBOLS:
        try:
            req = CryptoBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame.Hour, limit=100)
            bars = data.get_crypto_bars(req)
            bar_list = bars.get(symbol, [])
            if len(bar_list) < 30: continue
            closes = [float(b.close) for b in bar_list]
            cur = closes[-1]
            atr = calc_atr(closes)
            score, sig, reason = signal(closes, weights)
            held = symbol in positions
            print(f"  {symbol}: ${cur:.2f} | {sig} | {reason}")
            if sig == "BUY" and not held and cash > 50:
                spend = min(equity * k * risk, equity * 0.20, cash * 0.95)
                if spend > 20:
                    qty = round(spend / cur, 6)
                    try:
                        trading.submit_order(MarketOrderRequest(
                            symbol=symbol, qty=qty,
                            side=OrderSide.BUY, time_in_force=TimeInForce.GTC))
                        trade = {"bot_id": BOT_ID, "symbol": symbol, "action": "BUY",
                                 "price": cur, "qty": qty, "value": spend,
                                 "reason": reason, "strategy": "MOMENTUM",
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
                             "profit": profit, "reason": reason, "strategy": "MOMENTUM",
                             "timestamp": datetime.now(timezone.utc).isoformat()}
                    db.save_trade(trade)
                    if profit > 0: db.increment_stat(BOT_ID, "wins")
                    else: db.increment_stat(BOT_ID, "losses")
                    trades.append(trade)
                    print(f"    SELL {symbol} profit: ${profit:.2f}")
                except Exception as e: print(f"    Sell failed: {e}")
            elif held:
                entry = float(positions[symbol].avg_entry_price)
                if cur < entry - atr * 2.5:
                    qty = float(positions[symbol].qty)
                    try:
                        trading.submit_order(MarketOrderRequest(
                            symbol=symbol, qty=qty,
                            side=OrderSide.SELL, time_in_force=TimeInForce.GTC))
                        db.increment_stat(BOT_ID, "losses")
                        print(f"    STOP LOSS {symbol}")
                    except Exception as e: print(f"    Stop failed: {e}")
        except Exception as e: print(f"  Error {symbol}: {e}")
    db.save_equity_snapshot(BOT_ID, equity, cash)
    print(f"\n[{BOT_ID}] Done. {len(trades)} trades.")
    return trades

if __name__ == "__main__":
    run_bot()
