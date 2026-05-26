import os, sys, traceback
from datetime import datetime, timezone

def main():
    print("\n" + "="*60)
    print(f"APEX TRADING SYSTEM — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("="*60)

    required = ["ALPACA_API_KEY", "ALPACA_SECRET_KEY"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        print(f"Missing environment variables: {missing}")
        sys.exit(1)

    paper = os.environ.get("ALPACA_PAPER", "true").lower() == "true"
    print(f"Mode: {'PAPER TRADING' if paper else 'LIVE TRADING'}")

    results = {}

    print("\n--- BOT 1: MOMENTUM ---")
    try:
        sys.path.insert(0, ".")
        from bots.bot1_momentum import run_bot as run_bot1
        trades1 = run_bot1()
        results["bot1"] = {"status": "ok", "trades": len(trades1)}
    except Exception as e:
        print(f"Bot 1 error: {e}")
        traceback.print_exc()
        results["bot1"] = {"status": "error", "error": str(e)}

    print("\n--- BOT 2: MEAN REVERSION ---")
    try:
        from bots.bot2_mean_rev import run_bot as run_bot2
        trades2 = run_bot2()
        results["bot2"] = {"status": "ok", "trades": len(trades2)}
    except Exception as e:
        print(f"Bot 2 error: {e}")
        traceback.print_exc()
        results["bot2"] = {"status": "error", "error": str(e)}

    print("\n--- BOT 3: TREND FOLLOWING ---")
    try:
        from bots.bot3_trend import run_bot as run_bot3
        trades3 = run_bot3()
        results["bot3"] = {"status": "ok", "trades": len(trades3)}
    except Exception as e:
        print(f"Bot 3 error: {e}")
        traceback.print_exc()
        results["bot3"] = {"status": "error", "error": str(e)}

    print("\n" + "="*60)
    print("RUN SUMMARY")
    print("="*60)
    for bot, r in results.items():
        status = "OK" if r["status"] == "ok" else "ERROR"
        detail = f"{r.get('trades', 0)} trades" if r["status"] == "ok" else r.get("error", "")
        print(f"  {status} {bot.upper()}: {detail}")

    run_ai = os.environ.get("RUN_AI_BRAIN", "false").lower() == "true"
    is_sunday = datetime.now(timezone.utc).weekday() == 6
    if run_ai or is_sunday:
        print("\n--- AI BRAIN ---")
        try:
            from bots.ai_brain import run_ai_brain
            run_ai_brain()
        except Exception as e:
            print(f"AI Brain error: {e}")

    print("\nAll done\n")

if __name__ == "__main__":
    main()
