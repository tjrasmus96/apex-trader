import os, sys, traceback
from datetime import datetime, timezone

def main():
    print("\n" + "="*60)
    print(f"APEX — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("="*60)
    required = ["ALPACA_API_KEY", "ALPACA_SECRET_KEY"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        print(f"Missing: {missing}"); sys.exit(1)
    paper = os.environ.get("ALPACA_PAPER", "true").lower() == "true"
    print(f"Mode: {'PAPER' if paper else 'LIVE'}")
    results = {}
    print("\n--- BOT 1: MOMENTUM ---")
    try:
        sys.path.insert(0, ".")
        from bots.bot1_momentum import run_bot as r1
        results["bot1"] = {"status":"ok","trades":len(r1())}
    except Exception as e:
        print(f"Bot 1 error: {e}"); traceback.print_exc()
        results["bot1"] = {"status":"error","error":str(e)}
    print("\n--- BOT 2: MEAN REVERSION ---")
    try:
        from bots.bot2_mean_rev import run_bot as r2
        results["bot2"] = {"status":"ok","trades":len(r2())}
    except Exception as e:
        print(f"Bot 2 error: {e}"); traceback.print_exc()
        results["bot2"] = {"status":"error","error":str(e)}
    print("\n--- BOT 3: TREND ---")
    try:
        from bots.bot3_trend import run_bot as r3
        results["bot3"] = {"status":"ok","trades":len(r3())}
    except Exception as e:
        print(f"Bot 3 error: {e}"); traceback.print_exc()
        results["bot3"] = {"status":"error","error":str(e)}
    print("\nSUMMARY:")
    for bot, r in results.items():
        print(f"  {bot}: {r['status']} — {r.get('trades',0)} trades")
    run_ai = os.environ.get("RUN_AI_BRAIN","false").lower() == "true"
    if run_ai or datetime.now(timezone.utc).weekday() == 6:
        print("\n--- AI BRAIN ---")
        try:
            from bots.ai_brain import run_ai_brain
            run_ai_brain()
        except Exception as e:
            print(f"AI error: {e}")
    print("\nDone\n")

if __name__ == "__main__":
    main()
