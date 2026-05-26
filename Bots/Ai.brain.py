import os, json, urllib.request
from datetime import datetime, timezone
from database.db import Database

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
BOT_IDS = ["BOT1_MOMENTUM", "BOT2_MEAN_REV", "BOT3_TREND"]

def call_claude(prompt, max_tokens=1000):
    payload = json.dumps({
        "model": "claude-sonnet-4-20250514",
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}]
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01"
        }
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read())
    return data["content"][0]["text"]

def fetch_market_news():
    try:
        url = "https://api.alternative.me/fng/?limit=1"
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read())
        fg = data["data"][0]
        fear_greed = f"Fear & Greed: {fg['value']} ({fg['value_classification']})"
    except:
        fear_greed = "Fear & Greed: unavailable"
    try:
        url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,solana&vs_currencies=usd&include_24hr_change=true"
        with urllib.request.urlopen(url, timeout=10) as r:
            prices = json.loads(r.read())
        btc_chg = prices.get("bitcoin", {}).get("usd_24h_change", 0)
        eth_chg = prices.get("ethereum", {}).get("usd_24h_change", 0)
        regime = "BULL" if btc_chg > 3 else "BEAR" if btc_chg < -3 else "SIDEWAYS"
        market = f"BTC 24h: {btc_chg:.1f}%, ETH 24h: {eth_chg:.1f}%, Regime: {regime}"
    except:
        market = "Market data unavailable"
    return f"{fear_greed} | {market}"

def retrain_bot(db, bot_id):
    trades = db.get_trades(bot_id, limit=200)
    stats = db.get_bot_stats(bot_id)
    weights = db.get_strategy_weights(bot_id)
    equity = db.get_equity_history(bot_id, limit=20)
    if not trades:
        print(f"[AI] No trades for {bot_id}")
        return weights
    sells = [t for t in trades if t.get("action") == "SELL"]
    wins = [t for t in sells if (t.get("profit") or 0) > 0]
    losses = [t for t in sells if (t.get("profit") or 0) <= 0]
    total = len(wins) + len(losses)
    wr = len(wins) / total * 100 if total > 0 else 0
    total_pnl = sum(t.get("profit", 0) for t in sells)
    sym_pnl = {}
    for t in sells:
        s = t.get("symbol", "")
        sym_pnl[s] = sym_pnl.get(s, 0) + (t.get("profit") or 0)
    best = max(sym_pnl, key=sym_pnl.get) if sym_pnl else "N/A"
    worst = min(sym_pnl, key=sym_pnl.get) if sym_pnl else "N/A"
    eq_vals = [e["equity"] for e in equity] if equity else [200]
    current_eq = eq_vals[-1] if eq_vals else 200
    news = fetch_market_news()
    prompt = f"""You are the AI brain of an autonomous crypto trading bot.
Analyze this data and output optimized strategy weights.

BOT: {bot_id}
EQUITY: ${current_eq:.2f}
TRADES: {total} total, {wr:.1f}% win rate
TOTAL PNL: ${total_pnl:.2f}
BEST SYMBOL: {best} (${sym_pnl.get(best,0):.2f})
WORST SYMBOL: {worst} (${sym_pnl.get(worst,0):.2f})
CURRENT WEIGHTS: {json.dumps(weights)}
MARKET: {news}

Respond ONLY with valid JSON, no markdown:
{{"momentum_weight":1.0,"mean_rev_weight":1.0,"trend_weight":1.0,"risk_multiplier":1.0,"reasoning":"one sentence","market_regime":"BULL|BEAR|SIDEWAYS|VOLATILE"}}"""
    try:
        response = call_claude(prompt, max_tokens=300)
        clean = response.replace("```json","").replace("```","").strip()
        new_weights = json.loads(clean)
        print(f"[AI] {bot_id}: {new_weights.get('reasoning','')}")
        db.save_strategy_weights(bot_id, new_weights)
        return new_weights
    except Exception as e:
        print(f"[AI] Retrain failed for {bot_id}: {e}")
        return weights

def generate_report(db):
    all_data = {}
    for bot_id in BOT_IDS:
        trades = db.get_trades(bot_id, limit=100)
        equity = db.get_equity_history(bot_id, limit=5)
        sells = [t for t in trades if t.get("action") == "SELL"]
        wins = [t for t in sells if (t.get("profit") or 0) > 0]
        all_data[bot_id] = {
            "trades": len(sells),
            "win_rate": len(wins)/len(sells)*100 if sells else 0,
            "total_pnl": sum(t.get("profit",0) for t in sells),
            "current_equity": equity[-1]["equity"] if equity else 200
        }
    news = fetch_market_news()
    prompt = f"""Generate a brief weekly trading report.
DATE: {datetime.now(timezone.utc).strftime('%B %d, %Y')}
MARKET: {news}
BOTS: {json.dumps(all_data, indent=2)}

Write a concise report covering:
1. Overall performance summary
2. Which bot is performing best and why
3. Top 2 improvements needed
4. Strategy for next week
Keep under 300 words."""
    try:
        report_text = call_claude(prompt, max_tokens=500)
        db.save_report({
            "week_ending": datetime.now(timezone.utc).isoformat(),
            "report_text": report_text,
            "bot_data": all_data,
            "news_context": news
        })
        print("\n" + "="*50)
        print("WEEKLY AI REPORT")
        print("="*50)
        print(report_text)
        print("="*50 + "\n")
    except Exception as e:
        print(f"[AI] Report failed: {e}")

def run_ai_brain():
    print(f"\n[AI BRAIN] {datetime.now(timezone.utc).isoformat()}")
    if not ANTHROPIC_API_KEY:
        print("[AI] No API key set"); return
    db = Database()
    print("\n[1/3] Fetching market data...")
    news = fetch_market_news()
    print(f"  {news}")
    print("\n[2/3] Retraining bots...")
    for bot_id in BOT_IDS:
        retrain_bot(db, bot_id)
    print("\n[3/3] Generating report...")
    generate_report(db)
    print("\n[AI BRAIN] Complete")

if __name__ == "__main__":
    run_ai_brain()
