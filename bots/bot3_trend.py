"""
APEX BOT 3 — TREND FOLLOWING v2 (Enhanced)
Now uses: Multi-timeframe (1H+4H), EMA alignment,
          ADX strength filter, Macro regime,
          Fear & Greed trend confirmation,
          BTC dominance, Volume confirmation
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
                               get_macro_regime, calc_adx)

API_KEY    = os.environ.get("ALPACA_API_KEY")
API_SECRET = os.environ.get("ALPACA_SECRET_KEY")
PAPER      = os.environ.get("ALPACA_PAPER", "true").lower() == "true"
BOT_ID     = "BOT3_TREND"
SYMBOLS    = ["BTC/USD", "ETH/USD", "SOL/USD"]

def calc_ema(closes, period):
    if len(closes)<period: return closes[-1]
    k=2/(period+1); ema=sum(closes[:period])/period
    for p in closes[period:]: ema=p*k+ema*(1-k)
    return ema

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

    peak = stats.get("peak_equity", equity)
    if equity > peak: db.update_stat(BOT_ID,"peak_equity",equity); peak=equity
    dd = (peak-equity)/peak if peak>0 else 0

    btc_change = market_data.get("bitcoin",{}).get("usd_24h_change",0) or 0
    macro = get_macro_regime(btc_dominance, fear_greed["value"], btc_change)
    base_risk = 0.2 if dd>0.12 else 0.5 if dd>0.08 else 0.8 if dd>0.04 else 1.0
    final_risk = base_risk * macro["risk_multiplier"]

    # Trend bot works BETTER in trending markets
    if macro["regime"] == "BULL": final_risk *= 1.2; print(f"  [RISK] Bull market — boosting trend risk")
    elif macro["regime"] == "BEAR": final_risk *= 0.5; print(f"  [RISK] Bear market — reducing trend risk")

    print(f"  [DATA] F&G: {fear_greed['value']} | BTC Dom: {btc_dominance:.1f}% | Macro: {macro['regime']}")

    positions = {p.symbol:p for p in trading.get_all_positions()}
    trades = []

    for symbol in SYMBOLS:
        try:
            bars_1h = data_c.get_crypto_bars(CryptoBarsRequest(
                symbol_or_symbols=symbol, timeframe=TimeFrame.Hour, limit=80))
            bars_4h = data_c.get_crypto_bars(CryptoBarsRequest(
                symbol_or_symbols=symbol, timeframe=TimeFrame(4,"Hour"), limit=80))

            list_1h = bars_1h.get(symbol, [])
            list_4h = bars_4h.get(symbol, [])
            if len(list_4h)<30 or len(list_1h)<20: continue

            closes_1h = [float(b.close) for b in list_1h]
            closes_4h = [float(b.close) for b in list_4h]
            vols_1h   = [float(b.volume) for b in list_1h]
            cur = closes_1h[-1]

            # Primary signal from composite engine
            sig = get_composite_signal(symbol, closes_1h, vols_1h,
                                        market_data, fear_greed,
                                        btc_dominance, weights)

            # 4H trend confirmation (trend bot requires this)
            e20_4h = calc_ema(closes_4h, 20)
            e50_4h = calc_ema(closes_4h, 50)
            cur_4h = closes_4h[-1]
            adx_4h = calc_adx(closes_4h)

            four_h_bull = cur_4h > e20_4h > e50_4h and adx_4h > 20
            four_h_bear = cur_4h < e20_4h < e50_4h and adx_4h > 20

            # Trend bot ONLY trades when 4H confirms
            action = sig["action"]
            if action == "BUY" and not four_h_bull:
                action = "HOLD"
                print(f"    4H trend not confirmed — skipping BUY")
            if action == "SELL" and not four_h_bear and symbol in positions:
                # Keep holding if 4H still bullish
                if four_h_bull: action = "HOLD"

            held = symbol in positions
            print(f"  {symbol}: ${cur:.2f} | {action} | 4H:{e20_4h:.0f}>{e50_4h:.0f} ADX4H:{adx_4h:.0f}")

            if action == "BUY" and not held and cash > 40:
                spend = min(equity*0.20*final_risk, equity*0.30, cash*0.95)
                if spend > 20:
                    qty = round(spend/cur, 6)
                    try:
                        trading.submit_order(MarketOrderRequest(
                            symbol=symbol, qty=qty,
                            side=OrderSide.BUY, time_in_force=TimeInForce.GTC))
                        t = {"bot_id":BOT_ID,"symbol":symbol,"action":"BUY",
                             "price":cur,"qty":qty,"value":spend,
                             "reason":f"4H confirmed | {' | '.join(sig['reasons'][:2])}",
                             "strategy":"TREND_v2","signal_score":sig["score"],
                             "timestamp":datetime.now(timezone.utc).isoformat()}
                        db.save_trade(t); trades.append(t); cash -= spend
                        print(f"    ✅ BUY {qty:.6f} {symbol} @ ${cur:.2f}")
                    except Exception as e: print(f"    ❌ {e}")

            elif held and action == "SELL":
                pos    = positions[symbol]
                qty    = float(pos.qty)
                entry  = float(pos.avg_entry_price)
                profit = (cur-entry)*qty
                try:
                    trading.submit_order(MarketOrderRequest(
                        symbol=symbol, qty=qty,
                        side=OrderSide.SELL, time_in_force=TimeInForce.GTC))
                    t = {"bot_id":BOT_ID,"symbol":symbol,"action":"SELL",
                         "price":cur,"qty":qty,"value":cur*qty,"profit":profit,
                         "reason":' | '.join(sig['reasons'][:2]),
                         "strategy":"TREND_v2",
                         "timestamp":datetime.now(timezone.utc).isoformat()}
                    db.save_trade(t)
                    if profit>0: db.increment_stat(BOT_ID,"wins")
                    else: db.increment_stat(BOT_ID,"losses")
                    trades.append(t)
                    print(f"    {'✅' if profit>0 else '❌'} SELL profit: ${profit:.2f}")
                except Exception as e: print(f"    ❌ {e}")

        except Exception as e: print(f"  ❌ Error {symbol}: {e}")

    db.save_equity_snapshot(BOT_ID, equity, cash)
    print(f"\n[{BOT_ID}] Done. {len(trades)} trades.")
    return trades

if __name__ == "__main__":
    run_bot()
