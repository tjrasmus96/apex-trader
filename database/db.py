import os, json, time
from datetime import datetime, timezone
from pathlib import Path

try:
    from supabase import create_client
    SUPABASE_URL = os.environ.get("SUPABASE_URL")
    SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
    USE_SUPABASE = bool(SUPABASE_URL and SUPABASE_KEY)
except ImportError:
    USE_SUPABASE = False

LOCAL_DB_PATH = Path("data/db.json")

class Database:
    def __init__(self):
        if USE_SUPABASE:
            self.client = create_client(SUPABASE_URL, SUPABASE_KEY)
            self.mode = "supabase"
        else:
            LOCAL_DB_PATH.parent.mkdir(exist_ok=True)
            self.mode = "local"
        print(f"[DB] Mode: {self.mode}")

    def _load(self):
        if LOCAL_DB_PATH.exists():
            return json.loads(LOCAL_DB_PATH.read_text())
        return {"trades":[],"stats":{},"weights":{},"equity":[],"reports":[]}

    def _save(self, data):
        LOCAL_DB_PATH.write_text(json.dumps(data, indent=2, default=str))

    def save_trade(self, trade):
        trade["id"] = f"{trade['bot_id']}_{int(time.time()*1000)}"
        if USE_SUPABASE:
            try:
                self.client.table("trades").insert(trade).execute()
                return
            except Exception as e:
                print(f"[DB] Error: {e}")
        data = self._load()
        data["trades"].append(trade)
        data["trades"] = data["trades"][-2000:]
        self._save(data)

    def get_trades(self, bot_id=None, limit=500):
        if USE_SUPABASE:
            try:
                q = self.client.table("trades").select("*").order("timestamp",desc=True).limit(limit)
                if bot_id: q = q.eq("bot_id", bot_id)
                return q.execute().data
            except Exception as e:
                print(f"[DB] Error: {e}")
        data = self._load()
        trades = data.get("trades", [])
        if bot_id: trades = [t for t in trades if t.get("bot_id") == bot_id]
        return trades[-limit:]

    def get_bot_stats(self, bot_id):
        if USE_SUPABASE:
            try:
                r = self.client.table("bot_stats").select("*").eq("bot_id",bot_id).execute()
                if r.data: return r.data[0]
            except Exception as e:
                print(f"[DB] Error: {e}")
        data = self._load()
        return data.get("stats",{}).get(bot_id,{
            "wins":0,"losses":0,"peak_equity":0,
            "avg_win_pct":0.065,"avg_loss_pct":0.033})

    def update_stat(self, bot_id, key, value):
        if USE_SUPABASE:
            try:
                ex = self.client.table("bot_stats").select("*").eq("bot_id",bot_id).execute()
                if ex.data:
                    self.client.table("bot_stats").update({key:value}).eq("bot_id",bot_id).execute()
                else:
                    self.client.table("bot_stats").insert({"bot_id":bot_id,key:value}).execute()
                return
            except Exception as e:
                print(f"[DB] Error: {e}")
        data = self._load()
        data.setdefault("stats",{}).setdefault(bot_id,{})[key] = value
        self._save(data)

    def increment_stat(self, bot_id, key):
        stats = self.get_bot_stats(bot_id)
        self.update_stat(bot_id, key, stats.get(key,0)+1)

    def get_strategy_weights(self, bot_id):
        if USE_SUPABASE:
            try:
                r = self.client.table("strategy_weights").select("*").eq("bot_id",bot_id).execute()
                if r.data: return r.data[0].get("weights",{})
            except Exception as e:
                print(f"[DB] Error: {e}")
        data = self._load()
        return data.get("weights",{}).get(bot_id,{
            "momentum_weight":1.0,"mean_rev_weight":1.0,
            "trend_weight":1.0,"risk_multiplier":1.0})

    def save_strategy_weights(self, bot_id, weights):
        if USE_SUPABASE:
            try:
                ex = self.client.table("strategy_weights").select("*").eq("bot_id",bot_id).execute()
                if ex.data:
                    self.client.table("strategy_weights").update({"weights":weights}).eq("bot_id",bot_id).execute()
                else:
                    self.client.table("strategy_weights").insert({"bot_id":bot_id,"weights":weights}).execute()
                return
            except Exception as e:
                print(f"[DB] Error: {e}")
        data = self._load()
        data.setdefault("weights",{})[bot_id] = weights
        self._save(data)

    def save_equity_snapshot(self, bot_id, equity, cash):
        snap = {"bot_id":bot_id,"equity":equity,"cash":cash,
                "timestamp":datetime.now(timezone.utc).isoformat()}
        if USE_SUPABASE:
            try:
                self.client.table("equity_history").insert(snap).execute()
                return
            except Exception as e:
                print(f"[DB] Error: {e}")
        data = self._load()
        data.setdefault("equity",[]).append(snap)
        data["equity"] = data["equity"][-5000:]
        self._save(data)

    def get_equity_history(self, bot_id, limit=200):
        if USE_SUPABASE:
            try:
                r = self.client.table("equity_history").select("*")\
                    .eq("bot_id",bot_id).order("timestamp",desc=True).limit(limit).execute()
                return list(reversed(r.data))
            except Exception as e:
                print(f"[DB] Error: {e}")
        data = self._load()
        snaps = [s for s in data.get("equity",[]) if s.get("bot_id")==bot_id]
        return snaps[-limit:]

    def save_report(self, report):
        report["timestamp"] = datetime.now(timezone.utc).isoformat()
        if USE_SUPABASE:
            try:
                self.client.table("reports").insert(report).execute()
                return
            except Exception as e:
                print(f"[DB] Error: {e}")
        data = self._load()
        data.setdefault("reports",[]).append(report)
        self._save(data)
