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
        headers={"Content-Type": "application/json",
                 "x-api-key": ANTHROPIC_API_KEY,
                 "anthropic-version": "2023-06-01"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())["content"][0]["text"]

def fetch_market():
    results = []
    try:
        with urllib.request.urlopen("https://api.alternative.me/fng/?limit=1", timeout=10) as r:
            d = json.loads(r.read())["data"][0]
            results.append(f"Fear&Greed: {d['value']} ({d['value_classification']})")
    except: results.append("Fear&Greed: unavailable")
    try:
        url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,solana&vs_currencies=usd&include_24hr_change=true"
        with urllib.request.urlopen(url, timeout=10) as r:
            p = json.loads(r.read())
            btc = p.get("bitcoin",{}).get("usd_24h_change",0)
            eth = p.get("ethereum",{}).get("usd_24h_change",0)
            regime = "BULL" if btc > 3 else "BEAR" if btc < -3 else "SIDEWAYS"
            results.append(f"BTC:{btc:.1f}% ETH:{eth:.1f}% Regime:{regime}")
    except: results.append("Prices: unavailable")
    return " | ".join(results)

def retrain(db, bot_id):
    trades = db.get_trades(bot_id, limit=200)
    weights = db.get_strategy_weights(bot_id)
    equity = db.get_equity_history(bot_id, limit=20)
    if not trades: print(f"[AI] No trades for {bot_id}"); return weights
    sells = [t for t in trades if t.get("action") == "SELL"]
    wins = [t for t in sells if (t.get("profit") or 0) > 0]
    total = len(sells)
    wr = len(wins)/total*100 if total > 0 else 0
    pnl = sum(t.get("profit",0) for t in sells)
    sym_pnl = {}
    for t in sells:
        s = t.get("symbol","")
        sym_pnl[s] = sym_pnl.get(s,0) + (t.get("profit") or 0)
    best = max(sym_pnl, key=sym_pnl.get) if sym_pnl else "N/A"
    worst = min(sym_pnl, key=sym_pnl.get) if sym_pnl else "N/A"
    cur_eq = equity[-1]["equity"] if equity else 200
    market = fetch_market()
    prompt = f"""You are an AI trading bot optimizer.
BOT: {bot_id} | EQUITY: ${cur_eq:.2f}
TRADES: {total} | WIN RATE: {wr:.1f}% | PNL: ${pnl:.2f}
BEST: {best} (${sym_pnl.get(best,0):.2f}) | WORST: {worst} (${sym_pnl.get(worst,0):.2f})
WEIGHTS: {json.dumps(weights)} | MARKET: {market}
Respond ONLY with valid JSON no markdown:
{{"momentum_weight":1.0,"mean_rev_weight":1.0,"trend_weight":1.0,"risk_multiplier":1.0,"reasoning":"one sentence","market_regime":"BULL|BEAR|SIDEWAYS|VOLATILE"}}"""
    try:
        resp = call_claude(prompt, 300)
        new_w = json.loads(resp.replace("```json","").replace("```","").strip())
        print(f"[AI] {bot_id}: {new_w.get('reasoning','')}")
        db.save_strategy_weights(bot_id, new_w)
        return new_w
    except Exception as e:
        print(f"[AI] Failed {bot_id}: {e}")
        return weights

def report(db):
    all_data = {}
    for bot_id in BOT_IDS:
        trades = db.get_trades(bot_id, limit=100)
        equity = db.get_equity_history(bot_id, limit=5)
        sells = [t for t in trades if t.get("action") == "SELL"]
        wins = [t for t in sells if (t.get("profit") or 0) > 0]
        all_data[bot_id] = {
            "trades": len(sells),
            "win_rate": len(wins)/len(sells)*100 if sells else 0,
            "pnl": sum(t.get("profit",0) for t in sells),
            "equity": equity[-1]["equity"] if equity else 200
        }
    market = fetch_market()
    prompt = f"""Weekly trading report. Date: {datetime.now(timezone.utc).strftime('%B %d, %Y')}
Market: {market}
Bots: {json.dumps(all_data, indent=2)}
Write a concise report: performance summary, best bot, top 2 improvements, next week strategy. Under 250 words."""
    try:
        text = call_claude(prompt, 400)
        db.save_report({"week_ending": datetime.now(timezone.utc).isoformat(),
                        "report_text": text, "bot_data": all_data})
        print("\n" + "="*50)
        print("WEEKLY REPORT")
        print("="*50)
        print(text)
        print("="*50)
    except Exception as e:
        print(f"[AI] Report failed: {e}")

def run_ai_brain():
    print(f"\n[AI BRAIN] {datetime.now(timezone.utc).isoformat()}")
    if not ANTHROPIC_API_KEY:
        print("[AI] No API key"); return
    db = Database()
    print("\n[1/3] Market data...")
    print(f"  {fetch_market()}")
    print("\n[2/3] Retraining bots...")
    for bot_id in BOT_IDS:
        retrain(db, bot_id)
    print("\n[3/3] Generating report...")
    report(db)
    print("\n[AI BRAIN] Complete")

if __name__ == "__main__":
    run_ai_brain()
