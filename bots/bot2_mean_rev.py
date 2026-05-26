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
BOT_ID = "BOT2_MEAN_REV"
SYMBOLS = ["ETH/USD", "BCH/USD", "LTC/USD", "AAVE/USD", "UNI/USD"]

def calc_rsi(closes, period=14):
    if len(closes) < period + 1: return 50.0
    gains = [max(closes[i]-closes[i-1], 0) for i in range(1, len(closes))]
    losses = [max(closes[i-1]-closes[i], 0) for i in range(1, len(closes))]
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    if al == 0: return 100.0
    return 100 - 100 / (1 + ag / al)

def calc_bb(closes, period=20):
    if len(closes) < period:
        c = closes[-1]; return c*1.02, c, c*0.98
    sl = closes[-period:]
    mid = sum(sl) / period
    std = math.sqrt(sum((x-mid)**2 for x in sl) / period)
    return mid+2*std, mid, mid-2*std

def calc_zscore(closes, period=20):
    if len(closes) < period: return 0.0
    sl = closes[-period:]
    mid = sum(sl) / period
    std = math.sqrt(sum((x-mid)**2 for x in sl) / period) or 1
    return (closes[-1] - mid) / std

def calc_atr(closes, period=14):
    if len(closes) < 2: return closes[-1] * 0.02
    trs = [abs(closes[i]-closes[i-1]) for i in range(1, len(closes))]
    return sum(trs[-period:]) / min(len(trs), period)

def get_signal(closes, weights):
    if len(closes) < 25: return 0.0, "HOLD", "Gathering data"
    upper, mid, lower = calc_bb(closes)
    rsi = calc_rsi(closes)
    z = calc_zscore(closes)
    cur = closes[-1]
    score = 0.0
    reasons = []
    if cur < lower: score += 3.0; reasons.append("Below BB lower")
    elif cur < lower*1.01: score += 1.5; reasons.append("Near BB lower")
    elif cur > upper: score -= 3.0; reasons.append("Above BB upper")
    elif cur > upper*0.99: score -= 1.5; reasons.append("Near BB upper")
    if z < -2.0: score += 2.0; reasons.append(f"Z-score {z:.2f}")
    elif z < -1.5: score += 1.0
    elif z > 2.0: score -= 2.0; reasons.append(f"Z-score {z:.2f}")
    elif z > 1.5: score -= 1.0
    if rsi < 25: score += 1.5; reasons.append(f"RSI {rsi:.0f}")
    elif rsi < 35: score += 0.7
    elif rsi > 75: score -= 1.5; reasons.append(f"RSI {rsi:.0f}")
    elif rsi > 65: score -= 0.7
    score *= weights.get("mean_rev_weight", 1.0)
    if score >= 3.0: return score, "BUY", " | ".join(reasons[:2])
    elif score <= -2.5: return score, "SELL", " | ".join(reasons[:2])
    return score, "HOLD", reasons[0] if reasons else "Inside bands"

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
    risk = 0.25 if dd > 0.15 else 0.5 if dd > 0.10 else 0.75 if dd > 0.06 else 1.0
    positions = {p.symbol: p for p in trading.get_all_positions()}
    trades = []
    for symbol in SYMBOLS:
        try:
            bars = data.get_crypto_bars(CryptoBarsRequest(
                symbol_or_symbols=symbol, timeframe=TimeFrame.Hour, limit=80))
            bar_list = bars.get(symbol, [])
            if len(bar_list) < 25: continue
            closes = [float(b.close) for b in bar_list]
            cur = closes[-1]
            atr = calc_atr(closes)
            score, sig, reason = get_signal(closes, weights)
            held = symbol in positions
            print(f"  {symbol}: ${cur:.4f} | {sig} | {reason}")
            if sig == "BUY" and not held and cash > 30:
                spend = min(equity*0.15*risk, equity*0.18, cash*0.95)
                if spend > 15:
                    qty = round(spend/cur, 6)
                    try:
                        trading.submit_order(MarketOrderRequest(
                            symbol=symbol, qty=qty,
                            side=OrderSide.BUY, time_in_force=TimeInForce.GTC))
                        t = {"bot_id":BOT_ID,"symbol":symbol,"action":"BUY",
                             "price":cur,"qty":qty,"value":spend,"reason":reason,
                             "strategy":"MEAN_REV","timestamp":datetime.now(timezone.utc).isoformat()}
                        db.save_trade(t); trades.append(t)
                        print(f"    BUY {qty:.6f} {symbol} @ ${cur:.4f}")
                    except Exception as e: print(f"    Order failed: {e}")
            elif held and (sig == "SELL" or
                           cur < float(positions[symbol].avg_entry_price) - atr*2.0):
                pos = positions[symbol]
                qty = float(pos.qty)
                profit = (cur - float(pos.avg_entry_price)) * qty
                try:
                    trading.submit_order(MarketOrderRequest(
                        symbol=symbol, qty=qty,
                        side=OrderSide.SELL, time_in_force=TimeInForce.GTC))
                    t = {"bot_id":BOT_ID,"symbol":symbol,"action":"SELL",
                         "price":cur,"qty":qty,"value":cur*qty,"profit":profit,
                         "reason":reason,"strategy":"MEAN_REV",
                         "timestamp":datetime.now(timezone.utc).isoformat()}
                    db.save_trade(t)
                    if profit > 0: db.increment_stat(BOT_ID, "wins")
                    else: db.increment_stat(BOT_ID, "losses")
                    trades.append(t)
                    print(f"    SELL profit: ${profit:.2f}")
                except Exception as e: print(f"    Sell failed: {e}")
        except Exception as e: print(f"  Error {symbol}: {e}")
    db.save_equity_snapshot(BOT_ID, equity, cash)
    print(f"\n[{BOT_ID}] Done. {len(trades)} trades.")
    return trades

if __name__ == "__main__":
    run_bot()
