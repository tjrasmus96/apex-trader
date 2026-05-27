"""
APEX BOT 1 — MOMENTUM v2 (Enhanced)
Now uses: RSI, EMA, MACD, BB, ADX, StochRSI, VWAP, OBV,
          Volume Profile, Fear & Greed, BTC Dominance,
          Macro Regime, Volatility Regime, Multi-timeframe
"""
import os, sys, math
from datetime import datetime, timezone
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.data.historical import CryptoHistoricalDataClient
from alpaca.data.requests import CryptoBarsRequest
from alpaca.data.timeframe import TimeFrame
sys.path.insert(0, ".")
from database.db import Database
from enhanced_signals import (get_fear_greed, get_crypto_prices_and_metrics,
                               get_btc_dominance, get_composite_signal,
                               get_macro_regime)

API_KEY    = os.environ.get("ALPACA_API_KEY")
API_SECRET = os.environ.get("ALPACA_SECRET_KEY")
PAPER      = os.environ.get("ALPACA_PAPER", "true").lower() == "true"
BOT_ID     = "BOT1_MOMENTUM"
SYMBOLS    = ["BTC/USD", "ETH/USD", "SOL/USD", "AVAX/USD", "LINK/USD"]

def kelly_size(wr, avg_win, avg_loss):
    if avg_loss == 0: return 0.05
    b = avg_win / avg_loss; p = wr/100; q = 1-p
    return max(0.01, min((b*p-q)/b*0.5, 0.20))

def calc_atr(closes, period=14):
    if len(closes) < 2: return closes[-1]*0.02
    trs = [abs(closes[i]-closes[i-1]) for i in range(1,len(closes))]
    return sum(trs[-period:])/min(len(trs),period)

def run_bot():
    db = Database()
    stats   = db.get_bot_stats(BOT_ID)
    weights = db.get_strategy_weights(BOT_ID)

    trading = TradingClient(API_KEY, API_SECRET, paper=PAPER)
    data_c  = CryptoHistoricalDataClient(API_KEY, API_SECRET)

    account = trading.get_account()
    equity  = float(account.equity)
    cash    = float(account.cash)
    print(f"\n[{BOT_ID}] Equity: ${equity:.2f} | Cash: ${cash:.2f}")

    # ── Fetch global market data ONCE ────────────────────
    print(f"  [DATA] Fetching market intelligence...")
    fear_greed   = get_fear_greed()
    market_data  = get_crypto_prices_and_metrics()
    btc_dominance= get_btc_dominance()
    print(f"  [DATA] F&G: {fear_greed['value']} ({fear_greed['label']}) | BTC Dom: {btc_dominance:.1f}%")

    # ── Dynamic risk ──────────────────────────────────────
    peak = stats.get("peak_equity", equity)
    if equity > peak: db.update_stat(BOT_ID,"peak_equity",equity); peak=equity
    dd = (peak-equity)/peak if peak>0 else 0
    base_risk = 0.3 if dd>0.15 else 0.5 if dd>0.10 else 0.7 if dd>0.06 else 1.0

    # Macro regime adjusts risk further
    btc_change = market_data.get("bitcoin",{}).get("usd_24h_change",0) or 0
    macro = get_macro_regime(btc_dominance, fear_greed["value"], btc_change)
    final_risk = base_risk * macro["risk_multiplier"]
    print(f"  [RISK] Drawdown: {dd*100:.1f}% | Macro: {macro['regime']} | Risk mult: {final_risk:.2f}")

    tt  = stats.get("wins",0)+stats.get("losses",0)
    wr  = stats.get("wins",0)/tt*100 if tt>5 else 52.0
    k   = kelly_size(wr, stats.get("avg_win_pct",0.065), stats.get("avg_loss_pct",0.033))

    positions = {p.symbol:p for p in trading.get_all_positions()}
    trades = []

    for symbol in SYMBOLS:
        try:
            # Get 1H bars (primary) + 4H bars (confirmation)
            bars_1h = data_c.get_crypto_bars(CryptoBarsRequest(
                symbol_or_symbols=symbol, timeframe=TimeFrame.Hour, limit=120))
            bar_list = bars_1h.get(symbol, [])
            if len(bar_list) < 30: continue

            closes  = [float(b.close)  for b in bar_list]
            volumes = [float(b.volume) for b in bar_list]
            cur     = closes[-1]
            atr     = calc_atr(closes)

            # ── COMPOSITE SIGNAL ──────────────────────────
            sig = get_composite_signal(symbol, closes, volumes,
                                        market_data, fear_greed,
                                        btc_dominance, weights)

            held = symbol in positions
            print(f"  {symbol}: ${cur:.2f} | {sig['action']} (score:{sig['score']:.2f} conf:{sig['confidence']:.0%}) | {' | '.join(sig['reasons'][:2])}")
            print(f"    Regime: {sig['regime']} | ADX: {sig['adx']:.0f} | StochRSI: {sig['stoch_rsi']:.0f} | F&G: {sig['fear_greed']}")

            # ── BUY ───────────────────────────────────────
            if sig["action"] == "BUY" and not held and cash > 50:
                spend = min(equity*k*final_risk, equity*0.20, cash*0.95)
                if spend > 20:
                    qty = round(spend/cur, 6)
                    try:
                        trading.submit_order(MarketOrderRequest(
                            symbol=symbol, qty=qty,
                            side=OrderSide.BUY, time_in_force=TimeInForce.GTC))
                        t = {"bot_id":BOT_ID,"symbol":symbol,"action":"BUY",
                             "price":cur,"qty":qty,"value":spend,
                             "reason":f"{' | '.join(sig['reasons'][:2])} | F&G:{sig['fear_greed']}",
                             "strategy":"MOMENTUM_v2","signal_score":sig["score"],
                             "confidence":sig["confidence"],"regime":sig["regime"],
                             "timestamp":datetime.now(timezone.utc).isoformat()}
                        db.save_trade(t); trades.append(t); cash -= spend
                        print(f"    ✅ BUY {qty:.6f} {symbol} @ ${cur:.2f} (${spend:.2f})")
                    except Exception as e: print(f"    ❌ Order failed: {e}")

            # ── SELL ──────────────────────────────────────
            elif held and sig["action"] == "SELL":
                pos   = positions[symbol]
                qty   = float(pos.qty)
                entry = float(pos.avg_entry_price)
                profit= (cur-entry)*qty
                try:
                    trading.submit_order(MarketOrderRequest(
                        symbol=symbol, qty=qty,
                        side=OrderSide.SELL, time_in_force=TimeInForce.GTC))
                    t = {"bot_id":BOT_ID,"symbol":symbol,"action":"SELL",
                         "price":cur,"qty":qty,"value":cur*qty,"profit":profit,
                         "reason":f"{' | '.join(sig['reasons'][:2])}",
                         "strategy":"MOMENTUM_v2",
                         "timestamp":datetime.now(timezone.utc).isoformat()}
                    db.save_trade(t)
                    if profit>0: db.increment_stat(BOT_ID,"wins")
                    else: db.increment_stat(BOT_ID,"losses")
                    trades.append(t)
                    print(f"    {'✅' if profit>0 else '❌'} SELL profit: ${profit:.2f}")
                except Exception as e: print(f"    ❌ Sell failed: {e}")

            # ── ATR STOP LOSS ─────────────────────────────
            elif held:
                entry = float(positions[symbol].avg_entry_price)
                stop  = entry - atr*2.5
                if cur < stop:
                    try:
                        trading.submit_order(MarketOrderRequest(
                            symbol=symbol, qty=float(positions[symbol].qty),
                            side=OrderSide.SELL, time_in_force=TimeInForce.GTC))
                        db.increment_stat(BOT_ID,"losses")
                        profit = (cur-entry)*float(positions[symbol].qty)
                        t = {"bot_id":BOT_ID,"symbol":symbol,"action":"SELL",
                             "price":cur,"value":cur*float(positions[symbol].qty),
                             "profit":profit,"reason":"ATR stop-loss triggered",
                             "strategy":"MOMENTUM_v2",
                             "timestamp":datetime.now(timezone.utc).isoformat()}
                        db.save_trade(t); trades.append(t)
                        print(f"    ⛔ STOP LOSS {symbol} @ ${cur:.2f} (stop was ${stop:.2f})")
                    except Exception as e: print(f"    ❌ Stop failed: {e}")

        except Exception as e: print(f"  ❌ Error {symbol}: {e}")

    db.save_equity_snapshot(BOT_ID, equity, cash)
    print(f"\n[{BOT_ID}] Done. {len(trades)} trades. F&G: {fear_greed['value']} | Macro: {macro['regime']}")
    return trades

if __name__ == "__main__":
    run_bot()
