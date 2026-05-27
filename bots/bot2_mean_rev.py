"""
APEX BOT 1 — MOMENTUM v2 (Fixed timeframe)
Uses 15-min bars to get sufficient data on free Alpaca tier
"""
import os, sys
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
BOT_ID     = "BOT2_MEAN_REV"
SYMBOLS    = ["ETH/USD", "BCH/USD", "LTC/USD", "AAVE/USD", "UNI/USD"]

def calc_atr(closes, period=14):
    if len(closes) < 2: return closes[-1]*0.02
    trs = [abs(closes[i]-closes[i-1]) for i in range(1,len(closes))]
    return sum(trs[-period:])/min(len(trs),period)

def kelly_size(wr, avg_win, avg_loss):
    if avg_loss == 0: return 0.05
    b = avg_win/avg_loss; p = wr/100; q = 1-p
    return max(0.01, min((b*p-q)/b*0.5, 0.20))

def get_bars(data_client, symbol):
    """Fetch bars using timeframe that works with free Alpaca tier"""
    for timeframe, limit in [
        (TimeFrame.Minute, 200),   # 1-min bars, last 200
        (TimeFrame(5, "Min"), 100), # 5-min bars
        (TimeFrame(15, "Min"), 60), # 15-min bars
    ]:
        try:
            req = CryptoBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=timeframe,
                limit=limit
            )
            resp = data_client.get_crypto_bars(req)
            # Handle response format
            bars = []
            try:
                if hasattr(resp, '__iter__'):
                    for sym, bar_list in resp:
                        if sym == symbol:
                            bars = list(bar_list)
                            break
                if not bars and hasattr(resp, 'data'):
                    bars = list(resp.data.get(symbol, []))
            except Exception:
                try:
                    bars = list(resp[symbol])
                except Exception:
                    bars = []

            if len(bars) >= 30:
                print(f"    Got {len(bars)} bars for {symbol} ({timeframe})")
                return bars
        except Exception as e:
            print(f"    Timeframe {timeframe} failed: {e}")
            continue
    return []

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

    print(f"  [DATA] Fetching market intelligence...")
    fear_greed    = get_fear_greed()
    market_data   = get_crypto_prices_and_metrics()
    btc_dominance = get_btc_dominance()
    fg_val = fear_greed.get("value", 50) if isinstance(fear_greed, dict) else 50
    print(f"  [DATA] F&G: {fg_val} | BTC Dom: {btc_dominance:.1f}%")

    peak = stats.get("peak_equity", equity)
    if equity > peak: db.update_stat(BOT_ID, "peak_equity", equity); peak = equity
    dd = (peak-equity)/peak if peak>0 else 0
    base_risk = 0.3 if dd>0.15 else 0.5 if dd>0.10 else 0.7 if dd>0.06 else 1.0

    btc_change = market_data.get("bitcoin",{}).get("usd_24h_change",0) or 0 if isinstance(market_data,dict) else 0
    macro = get_macro_regime(btc_dominance, fg_val, btc_change)
    final_risk = base_risk * macro["risk_multiplier"]
    print(f"  [RISK] DD:{dd*100:.1f}% | Macro:{macro['regime']} | Risk:{final_risk:.2f}x")

    tt = stats.get("wins",0)+stats.get("losses",0)
    wr = stats.get("wins",0)/tt*100 if tt>5 else 52.0
    k  = kelly_size(wr, stats.get("avg_win_pct",0.065), stats.get("avg_loss_pct",0.033))

    positions = {p.symbol:p for p in trading.get_all_positions()}
    trades = []

    for symbol in SYMBOLS:
        try:
            bar_list = get_bars(data_c, symbol)
            if len(bar_list) < 30:
                print(f"  {symbol}: insufficient data ({len(bar_list)} bars) — skipping")
                continue

            closes  = [float(b.close)  for b in bar_list]
            volumes = [float(b.volume) for b in bar_list]
            cur     = closes[-1]
            atr     = calc_atr(closes)

            fg_dict = fear_greed if isinstance(fear_greed, dict) else {"value":fg_val,"score":0,"days_extreme_fear":0}
            md_dict = market_data if isinstance(market_data, dict) else {}

            sig = get_composite_signal(symbol, closes, volumes, md_dict, fg_dict, btc_dominance, weights)
            held = symbol in positions

            print(f"  {symbol}: ${cur:.2f} | {sig['action']} (score:{sig['score']:.2f} conf:{sig['confidence']:.0%})")
            if sig['reasons']: print(f"    {' | '.join(sig['reasons'][:2])}")

            if sig["action"] == "BUY" and not held and cash > 50:
                spend = min(equity*k*final_risk, equity*0.05, cash*0.30, 500)
                if spend > 20:
                    qty = round(spend/cur, 6)
                    try:
                        trading.submit_order(MarketOrderRequest(
                            symbol=symbol, qty=qty,
                            side=OrderSide.BUY, time_in_force=TimeInForce.GTC))
                        t = {"bot_id":BOT_ID,"symbol":symbol,"action":"BUY",
                             "price":cur,"qty":qty,"value":spend,
                             "reason":" | ".join(sig["reasons"][:2]),
                             "strategy":"MEAN_REV_v2","signal_score":sig["score"],
                             "timestamp":datetime.now(timezone.utc).isoformat()}
                        db.save_trade(t); trades.append(t); cash -= spend
                        print(f"    ✅ BUY {qty:.6f} {symbol} @ ${cur:.2f}")
                    except Exception as e: print(f"    ❌ {e}")

            elif held and sig["action"] == "SELL":
                pos = positions[symbol]
                qty = float(pos.qty); entry = float(pos.avg_entry_price)
                profit = (cur-entry)*qty
                try:
                    trading.submit_order(MarketOrderRequest(
                        symbol=symbol, qty=qty,
                        side=OrderSide.SELL, time_in_force=TimeInForce.GTC))
                    t = {"bot_id":BOT_ID,"symbol":symbol,"action":"SELL",
                         "price":cur,"qty":qty,"value":cur*qty,"profit":profit,
                         "reason":" | ".join(sig["reasons"][:2]),
                         "strategy":"MEAN_REV_v2",
                         "timestamp":datetime.now(timezone.utc).isoformat()}
                    db.save_trade(t)
                    if profit>0: db.increment_stat(BOT_ID,"wins")
                    else: db.increment_stat(BOT_ID,"losses")
                    trades.append(t)
                    print(f"    {'✅' if profit>0 else '❌'} SELL profit: ${profit:.2f}")
                except Exception as e: print(f"    ❌ {e}")

            elif held:
                entry = float(positions[symbol].avg_entry_price)
                if cur < entry - atr*2.5:
                    qty = float(positions[symbol].qty)
                    profit = (cur-entry)*qty
                    try:
                        trading.submit_order(MarketOrderRequest(
                            symbol=symbol, qty=qty,
                            side=OrderSide.SELL, time_in_force=TimeInForce.GTC))
                        db.increment_stat(BOT_ID,"losses")
                        t = {"bot_id":BOT_ID,"symbol":symbol,"action":"SELL",
                             "price":cur,"value":cur*qty,"profit":profit,
                             "reason":"ATR stop-loss","strategy":"MEAN_REV_v2",
                             "timestamp":datetime.now(timezone.utc).isoformat()}
                        db.save_trade(t); trades.append(t)
                        print(f"    ⛔ STOP LOSS {symbol}")
                    except Exception as e: print(f"    ❌ {e}")

        except Exception as e: print(f"  ❌ Error {symbol}: {e}")

    db.save_equity_snapshot(BOT_ID, equity, cash)
    print(f"\n[{BOT_ID}] Done. {len(trades)} trades.")
    return trades

if __name__ == "__main__":
    run_bot()
