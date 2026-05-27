"""
APEX AI BRAIN v2 — Enhanced with market intelligence
Now researches: Fear & Greed, BTC dominance, on-chain,
                trending coins, macro regime
Rewrites strategy weights based on ALL signal categories
Generates detailed weekly performance report
"""
import os, json, urllib.request
from datetime import datetime, timezone
import sys
sys.path.insert(0,".")
from database.db import Database
from enhanced_signals import (get_fear_greed, get_crypto_prices_and_metrics,
                               get_btc_dominance, get_btc_onchain,
                               get_funding_rates, get_trending_coins,
                               get_macro_regime)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
BOT_IDS = ["BOT1_MOMENTUM","BOT2_MEAN_REV","BOT3_TREND"]

def call_claude(prompt, max_tokens=1000):
    payload = json.dumps({"model":"claude-sonnet-4-20250514","max_tokens":max_tokens,
                           "messages":[{"role":"user","content":prompt}]}).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages",
        data=payload, headers={"Content-Type":"application/json",
                                "x-api-key":ANTHROPIC_API_KEY,
                                "anthropic-version":"2023-06-01"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())["content"][0]["text"]

def retrain_bot(db, bot_id, market_context):
    trades  = db.get_trades(bot_id, limit=300)
    stats   = db.get_bot_stats(bot_id)
    weights = db.get_strategy_weights(bot_id)
    equity  = db.get_equity_history(bot_id, limit=30)

    if not trades: print(f"[AI] No trades for {bot_id}"); return weights

    sells = [t for t in trades if t.get("action")=="SELL"]
    wins  = [t for t in sells if (t.get("profit") or 0)>0]
    total = len(sells)
    wr    = len(wins)/total*100 if total>0 else 0
    pnl   = sum(t.get("profit",0) for t in sells)

    # Strategy breakdown
    by_strat = {}
    for t in sells:
        s = t.get("strategy","UNKNOWN")
        if s not in by_strat: by_strat[s]={"wins":0,"losses":0,"pnl":0}
        p = t.get("profit",0) or 0
        if p>0: by_strat[s]["wins"]+=1
        else: by_strat[s]["losses"]+=1
        by_strat[s]["pnl"]+=p

    # Symbol breakdown
    by_sym = {}
    for t in sells:
        s = t.get("symbol","")
        by_sym[s] = by_sym.get(s,0) + (t.get("profit",0) or 0)

    best_sym  = max(by_sym, key=by_sym.get) if by_sym else "N/A"
    worst_sym = min(by_sym, key=by_sym.get) if by_sym else "N/A"
    cur_eq    = equity[-1]["equity"] if equity else 200

    prompt = f"""You are the AI brain of an autonomous crypto trading system. 
Analyze performance data and market conditions to optimize strategy weights.

BOT: {bot_id}
CURRENT EQUITY: ${cur_eq:.2f}
TOTAL TRADES: {total} | WIN RATE: {wr:.1f}% | TOTAL PNL: ${pnl:.2f}

STRATEGY PERFORMANCE:
{json.dumps(by_strat, indent=2)}

SYMBOL PERFORMANCE:
Best: {best_sym} (${by_sym.get(best_sym,0):.2f}) | Worst: {worst_sym} (${by_sym.get(worst_sym,0):.2f})

CURRENT WEIGHTS: {json.dumps(weights)}

MARKET CONTEXT:
{json.dumps(market_context, indent=2)}

Analyze what's working and what isn't. Consider:
1. Which signal categories are generating wins vs losses
2. Current market regime and how it affects each strategy
3. Fear & Greed level and its implications
4. BTC dominance and altcoin strength

Respond ONLY with valid JSON (no markdown, no explanation):
{{
  "technical_weight": 0.1-0.5,
  "momentum_weight": 0.1-0.4,
  "volume_weight": 0.05-0.3,
  "sentiment_weight": 0.05-0.3,
  "macro_weight": 0.05-0.2,
  "onchain_weight": 0.02-0.15,
  "risk_multiplier": 0.3-1.5,
  "reasoning": "2-3 sentences explaining key changes",
  "market_regime": "BULL|BEAR|SIDEWAYS|VOLATILE",
  "focus_symbols": ["BTC/USD","ETH/USD"],
  "avoid_symbols": []
}}"""

    try:
        resp = call_claude(prompt, 500)
        new_w = json.loads(resp.replace("```json","").replace("```","").strip())
        print(f"[AI] {bot_id}: {new_w.get('reasoning','')}")
        print(f"[AI] Focus: {new_w.get('focus_symbols',[])} | Avoid: {new_w.get('avoid_symbols',[])}")
        db.save_strategy_weights(bot_id, new_w)
        return new_w
    except Exception as e:
        print(f"[AI] Retrain failed {bot_id}: {e}")
        return weights

def generate_report(db, market_context):
    all_data = {}
    for bot_id in BOT_IDS:
        trades = db.get_trades(bot_id, limit=100)
        equity = db.get_equity_history(bot_id, limit=10)
        sells  = [t for t in trades if t.get("action")=="SELL"]
        wins   = [t for t in sells if (t.get("profit") or 0)>0]
        all_data[bot_id] = {
            "trades": len(sells),
            "win_rate": len(wins)/len(sells)*100 if sells else 0,
            "pnl": sum(t.get("profit",0) for t in sells),
            "equity": equity[-1]["equity"] if equity else 200,
            "weights": db.get_strategy_weights(bot_id)
        }

    prompt = f"""Generate a comprehensive weekly trading report for an autonomous 3-bot crypto system.

DATE: {datetime.now(timezone.utc).strftime('%B %d, %Y')}

MARKET INTELLIGENCE:
- Fear & Greed: {market_context.get('fear_greed',{}).get('value',50)} ({market_context.get('fear_greed',{}).get('label','Neutral')})
- BTC Dominance: {market_context.get('btc_dominance',50):.1f}%
- Market Regime: {market_context.get('macro',{}).get('regime','UNKNOWN')}
- Trending Coins: {', '.join(market_context.get('trending',[])[:5])}
- On-chain Activity: {market_context.get('onchain',{}).get('signal','NEUTRAL')}

BOT PERFORMANCE:
{json.dumps(all_data, indent=2)}

Write a professional report covering:
1. EXECUTIVE SUMMARY — overall system performance this week
2. MARKET CONDITIONS — how current market affected each bot
3. BOT ANALYSIS — what each bot did well/poorly and why
4. SIGNAL QUALITY — which signals (technical/sentiment/macro) worked best
5. RISK ASSESSMENT — current risk level and any concerns
6. NEXT WEEK STRATEGY — specific adjustments recommended
7. GOAL PROGRESS — progress toward prop firm challenge and $150K/year income goal

Keep it specific, data-driven, and under 500 words."""

    try:
        text = call_claude(prompt, 700)
        db.save_report({
            "week_ending": datetime.now(timezone.utc).isoformat(),
            "report_text": text,
            "bot_data": all_data,
            "news_context": json.dumps(market_context)
        })
        print("\n" + "="*60)
        print("WEEKLY AI REPORT")
        print("="*60)
        print(text)
        print("="*60 + "\n")
    except Exception as e:
        print(f"[AI] Report failed: {e}")

def run_ai_brain():
    print(f"\n[AI BRAIN v2] {datetime.now(timezone.utc).isoformat()}")
    if not ANTHROPIC_API_KEY:
        print("[AI] No API key set"); return

    db = Database()

    # ── Gather comprehensive market intelligence ──────
    print("\n[1/4] Gathering market intelligence...")
    fear_greed    = get_fear_greed()
    market_data   = get_crypto_prices_and_metrics()
    btc_dominance = get_btc_dominance()
    onchain       = get_btc_onchain()
    funding       = get_funding_rates()
    trending      = get_trending_coins()
    btc_change    = market_data.get("bitcoin",{}).get("usd_24h_change",0) or 0
    macro         = get_macro_regime(btc_dominance, fear_greed["value"], btc_change)

    market_context = {
        "fear_greed": fear_greed,
        "btc_dominance": btc_dominance,
        "btc_24h_change": btc_change,
        "macro": macro,
        "onchain": onchain,
        "funding": funding,
        "trending": trending,
        "prices": {k: v.get("usd",0) for k,v in market_data.items()}
    }

    print(f"  F&G: {fear_greed['value']} ({fear_greed['label']})")
    print(f"  BTC Dominance: {btc_dominance:.1f}%")
    print(f"  Macro Regime: {macro['regime']} (score: {macro['score']})")
    print(f"  On-chain: {onchain.get('signal','N/A')}")
    print(f"  Funding: {funding.get('signal','N/A')}")
    print(f"  Trending: {', '.join(trending[:5])}")

    # ── Retrain each bot ──────────────────────────────
    print("\n[2/4] Retraining bots with full market context...")
    for bot_id in BOT_IDS:
        print(f"\n  Analyzing {bot_id}...")
        retrain_bot(db, bot_id, market_context)

    # ── Generate report ───────────────────────────────
    print("\n[3/4] Generating AI performance report...")
    generate_report(db, market_context)

    # ── Save market snapshot ──────────────────────────
    print("\n[4/4] Saving market snapshot...")
    db.save_report({
        "week_ending": datetime.now(timezone.utc).isoformat(),
        "report_text": f"Market snapshot: F&G {fear_greed['value']}, BTC Dom {btc_dominance:.1f}%, Regime {macro['regime']}",
        "bot_data": {},
        "news_context": json.dumps({"snapshot": market_context})
    })

    print("\n[AI BRAIN v2] Complete ✅")

if __name__ == "__main__":
    run_ai_brain()
