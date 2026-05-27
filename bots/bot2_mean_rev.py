"""
APEX BOT 2 — MEAN REVERSION v2 (Enhanced)
Now uses: BB, Z-score, RSI, StochRSI, Volume Profile,
          Fear & Greed (contrarian), ADX filter,
          Multi-timeframe, Macro regime
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
                               get_macro_regime, calc_stoch_rsi, calc_adx)

API_KEY    = os.environ.get("ALPACA_API_KEY")
API_SECRET = os.environ.get("ALPACA_SECRET_KEY")
PAPER      = os.environ.get("ALPACA_PAPER", "true").lower() == "true"
BOT_ID     = "BOT2_MEAN_REV"
SYMBOLS    = ["ETH/USD", "BCH/USD", "LTC/USD", "AAVE/USD", "UNI/USD"]

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

    print(f"  [DATA] Fetching market intelligence...")
    fear_greed    = get_fear_greed()
    market_data   = get_crypto_prices_and_metrics()
    btc_dominance = get_btc_dominance()
    print(f"  [DATA] F&G: {fear_greed['value']} ({fear_greed['label']})")

    peak = stats.get("peak_equity", equity)
    if equity > peak: db.update_stat(BOT_ID,"peak_equity",equity); peak=equity
    dd = (peak-equity)/peak if peak>0 else 0

    btc_change = market_data.get("bitcoin",{}).get("usd_24h_change",0) or 0
    macro = get_macro_regime(btc_dominance, fear_greed["value"], btc_change)
    base_risk = 0.25 if dd>0.15 else 0.5 if dd>0.10 else 0.75 if dd>0.06 else 1.0
    final_risk = base_risk * macro["risk_multiplier"]

    # Mean reversion works BETTER in ranging/sideways markets
    # Reduce in strong trending markets
    if macro["regime"] == "BULL" or macro["regime"] == "BEAR":
        final_risk *= 0.7  # trending = mean rev less reliable
        print(f"  [RISK] Trending market — reducing mean rev size")

    # Fear & Greed contrarian bonus for mean reversion
    fg_bonus = 1.3 if fear_greed["value"] < 20 else 1.1 if fear_greed["value"] < 30 else 1.0
    final_risk = min(final_risk * fg_bonus, 1.5)

    positions = {p.symbol:p for p in trading.get_all_positions()}
    trades = []

    for symbol in SYMBOLS:
        try:
            bars = data_c.get_crypto_bars(CryptoBarsRequest(
                symbol_or_symbols=symbol, timeframe=TimeFrame.Hour, limit=80))
            bar_list = bars.get(symbol, [])
            if len(bar_list) < 25: continue

            closes  = [float(b.close)  for b in bar_list]
            volumes = [float(b.volume) for b in bar_list]
            cur     = closes[-1]
            atr     = calc_atr(closes)

            sig = get_composite_signal(symbol, closes, volumes,
                                        market_data, fear_greed,
                                        btc_dominance, weights)

            # For mean reversion: prefer ranging markets (low ADX)
            adx_penalty = 0.7 if sig["adx"] > 35 else 1.0  # reduce in strong trends

            held = symbol in positions
            print(f"  {symbol}: ${cur:.4f} | {sig['action']} | ADX:{sig['adx']:.0f} | StochRSI:{sig['stoch_rsi']:.0f}")

            if sig["action"] == "BUY" and not held and cash > 30:
                spend = min(equity*0.15*final_risk*adx_penalty, equity*0.18, cash*0.95)
                if spend > 15:
                    qty = round(spend/cur, 6)
                    try:
                        trading.submit_order(MarketOrderRequest(
                            symbol=symbol, qty=qty,
                            side=OrderSide.BUY, time_in_force=TimeInForce.GTC))
                        t = {"bot_id":BOT_ID,"symbol":symbol,"action":"BUY",
                             "price":cur,"qty":qty,"value":spend,
                             "reason":f"{' | '.join(sig['reasons'][:2])} | F&G:{sig['fear_greed']}",
                             "strategy":"MEAN_REV_v2","signal_score":sig["score"],
                             "timestamp":datetime.now(timezone.utc).isoformat()}
                        db.save_trade(t); trades.append(t); cash -= spend
                        print(f"    ✅ BUY {qty:.6f} {symbol} @ ${cur:.4f}")
                    except Exception as e: print(f"    ❌ {e}")

            elif held and (sig["action"] == "SELL" or
                           cur < float(positions[symbol].avg_entry_price) - atr*2.0):
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
                         "strategy":"MEAN_REV_v2",
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
