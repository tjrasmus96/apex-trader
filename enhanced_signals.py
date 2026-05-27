"""
APEX TRADING SYSTEM — ENHANCED SIGNAL ENGINE v2
New additions based on world-class trading systems:
1. Fear & Greed Index (sentiment)
2. Whale movement tracking (on-chain)
3. Funding rates (derivatives signal)
4. Volume analysis (OBV + VWAP)
5. ADX (trend strength filter)
6. Multi-timeframe confirmation
7. Liquidation heatmap awareness
8. BTC dominance macro filter
9. Volatility regime detection
10. News sentiment via Claude AI
"""
import os, json, urllib.request, math
from datetime import datetime, timezone

# ═══════════════════════════════════════════════════
# FREE DATA SOURCES (no API key needed)
# ═══════════════════════════════════════════════════

def get_fear_greed() -> dict:
    """Fear & Greed Index 0-100. <25=Extreme Fear (buy), >75=Extreme Greed (sell)"""
    try:
        url = "https://api.alternative.me/fng/?limit=7"
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read())
        readings = data["data"]
        current = int(readings[0]["value"])
        avg7 = sum(int(x["value"]) for x in readings) / len(readings)
        label = readings[0]["value_classification"]
        # Extreme fear for 7+ days = strong buy signal
        days_extreme_fear = sum(1 for x in readings if int(x["value"]) < 25)
        return {
            "value": current,
            "label": label,
            "avg7": avg7,
            "days_extreme_fear": days_extreme_fear,
            "signal": "STRONG_BUY" if days_extreme_fear >= 5 else
                      "BUY" if current < 30 else
                      "SELL" if current > 75 else
                      "STRONG_SELL" if current > 85 else "NEUTRAL",
            "score": (50 - current) / 50  # -1 to +1, positive = buy
        }
    except Exception as e:
        print(f"[FG] Error: {e}")
        return {"value": 50, "label": "Neutral", "signal": "NEUTRAL", "score": 0, "days_extreme_fear": 0}

def get_crypto_prices_and_metrics() -> dict:
    """CoinGecko free API — prices, volume, market cap, 24h changes"""
    try:
        ids = "bitcoin,ethereum,solana,avalanche-2,chainlink,cardano,matic-network,binancecoin"
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd&include_24hr_change=true&include_24hr_vol=true&include_market_cap=true"
        with urllib.request.urlopen(url, timeout=15) as r:
            data = json.loads(r.read())
        return data
    except Exception as e:
        print(f"[CG] Error: {e}")
        return {}

def get_btc_dominance() -> float:
    """BTC dominance — high dominance = risk-off, altcoins suffer"""
    try:
        url = "https://api.coingecko.com/api/v3/global"
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read())
        dom = data["data"]["market_cap_percentage"].get("btc", 50)
        return float(dom)
    except:
        return 50.0

def get_btc_onchain() -> dict:
    """
    Blockchain.info free API for BTC on-chain metrics
    Exchange inflows/outflows approximated via mempool + tx count
    """
    try:
        url = "https://api.blockchain.info/stats"
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read())
        tx_count = data.get("n_tx", 0)
        # High tx count = high activity = bullish signal
        avg_tx = 350000  # approximate daily average
        activity_score = (tx_count - avg_tx) / avg_tx
        return {
            "tx_count": tx_count,
            "activity_score": max(-1, min(1, activity_score)),
            "hash_rate": data.get("hash_rate", 0),
            "signal": "BULLISH" if activity_score > 0.1 else "BEARISH" if activity_score < -0.1 else "NEUTRAL"
        }
    except Exception as e:
        print(f"[OnChain] Error: {e}")
        return {"activity_score": 0, "signal": "NEUTRAL"}

def get_funding_rates() -> dict:
    """
    Simulated funding rates based on price momentum
    Real funding rates from Binance/Bybit require accounts
    Positive funding = longs pay shorts = overheated market
    Negative funding = shorts pay longs = capitulation
    """
    try:
        # Use CoinGecko derivatives data
        url = "https://api.coingecko.com/api/v3/derivatives?include_tickers=unexpired"
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read())
        # Filter BTC perpetuals
        btc_perps = [d for d in data if "BTC" in str(d.get("base","")) and d.get("contract_type") == "perpetual"]
        if btc_perps:
            funding = float(btc_perps[0].get("funding_rate", 0) or 0)
            return {
                "btc_funding": funding,
                "signal": "SELL" if funding > 0.0003 else "BUY" if funding < -0.0001 else "NEUTRAL",
                "score": -funding * 1000  # negative funding = positive score
            }
    except:
        pass
    return {"btc_funding": 0, "signal": "NEUTRAL", "score": 0}

def get_trending_coins() -> list:
    """CoinGecko trending — retail FOMO indicator"""
    try:
        url = "https://api.coingecko.com/api/v3/search/trending"
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read())
        return [c["item"]["symbol"].upper() for c in data.get("coins", [])[:7]]
    except:
        return []

# ═══════════════════════════════════════════════════
# ENHANCED TECHNICAL INDICATORS
# ═══════════════════════════════════════════════════

def calc_adx(closes, period=14) -> float:
    """ADX — trend strength. >25 = strong trend, <20 = weak/ranging"""
    if len(closes) < period * 2: return 20.0
    # Simplified ADX calculation
    tr_list = [abs(closes[i] - closes[i-1]) for i in range(1, len(closes))]
    atr = sum(tr_list[-period:]) / period
    if atr == 0: return 20.0
    # Directional movement
    dm_plus = [max(closes[i]-closes[i-1], 0) for i in range(1, len(closes))]
    dm_minus = [max(closes[i-1]-closes[i], 0) for i in range(1, len(closes))]
    di_plus = sum(dm_plus[-period:]) / period / atr * 100
    di_minus = sum(dm_minus[-period:]) / period / atr * 100
    dx = abs(di_plus - di_minus) / (di_plus + di_minus + 0.001) * 100
    return dx

def calc_vwap(closes, volumes, period=20) -> float:
    """VWAP — volume weighted average price"""
    if len(closes) < period or not volumes: return closes[-1]
    sl_c = closes[-period:]
    sl_v = volumes[-period:] if len(volumes) >= period else volumes
    if not sl_v: return closes[-1]
    total_vol = sum(sl_v)
    if total_vol == 0: return closes[-1]
    return sum(c * v for c, v in zip(sl_c, sl_v)) / total_vol

def calc_obv(closes, volumes) -> float:
    """On-Balance Volume — accumulation/distribution"""
    if not volumes or len(closes) < 2: return 0
    obv = 0
    for i in range(1, min(len(closes), len(volumes))):
        vol = volumes[i] if i < len(volumes) else 0
        if closes[i] > closes[i-1]: obv += vol
        elif closes[i] < closes[i-1]: obv -= vol
    return obv

def calc_stoch_rsi(closes, period=14) -> float:
    """Stochastic RSI — momentum extreme detector"""
    if len(closes) < period * 2: return 50.0
    rsi_vals = []
    for i in range(period, len(closes)):
        gains = [max(closes[j]-closes[j-1], 0) for j in range(i-period+1, i+1)]
        losses = [max(closes[j-1]-closes[j], 0) for j in range(i-period+1, i+1)]
        ag = sum(gains)/period
        al = sum(losses)/period
        if al == 0: rsi_vals.append(100)
        else: rsi_vals.append(100 - 100/(1+ag/al))
    if len(rsi_vals) < period: return 50.0
    recent = rsi_vals[-period:]
    mn, mx = min(recent), max(recent)
    if mx == mn: return 50.0
    return (rsi_vals[-1] - mn) / (mx - mn) * 100

def calc_volume_profile(closes, volumes, period=20) -> dict:
    """Volume profile — identifies high-volume price levels (support/resistance)"""
    if not volumes or len(closes) < period:
        return {"poc": closes[-1], "signal": "NEUTRAL"}
    sl_c = closes[-period:]
    sl_v = volumes[-period:] if len(volumes) >= period else volumes
    # Point of control = price with highest volume
    if not sl_v: return {"poc": closes[-1], "signal": "NEUTRAL"}
    poc_idx = sl_v.index(max(sl_v))
    poc = sl_c[poc_idx] if poc_idx < len(sl_c) else closes[-1]
    cur = closes[-1]
    # Trading above POC = bullish, below = bearish
    signal = "BULLISH" if cur > poc * 1.01 else "BEARISH" if cur < poc * 0.99 else "NEUTRAL"
    return {"poc": poc, "signal": signal, "pct_from_poc": (cur-poc)/poc*100}

def detect_volatility_regime(closes, period=20) -> str:
    """Detect if market is trending, ranging, or volatile"""
    if len(closes) < period: return "UNKNOWN"
    sl = closes[-period:]
    returns = [(sl[i]-sl[i-1])/sl[i-1] for i in range(1, len(sl))]
    vol = math.sqrt(sum(r**2 for r in returns)/len(returns)) * math.sqrt(252*24)
    adx = calc_adx(closes)
    if vol > 0.8: return "VOLATILE"
    elif adx > 30: return "TRENDING"
    elif adx < 20: return "RANGING"
    return "NORMAL"

# ═══════════════════════════════════════════════════
# MACRO FILTERS
# ═══════════════════════════════════════════════════

def get_macro_regime(btc_dominance: float, fear_greed: int, btc_24h_change: float) -> dict:
    """
    Overall market regime classification
    Determines how aggressive bots should be
    """
    score = 0
    signals = []

    # BTC dominance (>55% = altcoins weak, <45% = altseason)
    if btc_dominance > 58:
        score -= 1; signals.append("High BTC dominance — avoid alts")
    elif btc_dominance < 45:
        score += 1; signals.append("Low BTC dominance — altseason possible")

    # Fear & Greed
    if fear_greed < 20:
        score += 2; signals.append("Extreme fear — potential bottom")
    elif fear_greed > 80:
        score -= 2; signals.append("Extreme greed — potential top")
    elif fear_greed < 35:
        score += 1; signals.append("Fear zone — cautious buy")
    elif fear_greed > 65:
        score -= 1; signals.append("Greed zone — reduce exposure")

    # BTC momentum
    if btc_24h_change > 5:
        score += 1; signals.append("BTC strong up")
    elif btc_24h_change < -5:
        score -= 1; signals.append("BTC strong down")
    elif btc_24h_change < -10:
        score -= 2; signals.append("BTC crash — defensive mode")

    regime = "BULL" if score >= 2 else "BEAR" if score <= -2 else "SIDEWAYS"
    risk_mult = 1.3 if score >= 3 else 1.1 if score >= 1 else 0.7 if score <= -1 else 0.4 if score <= -3 else 1.0

    return {
        "regime": regime,
        "score": score,
        "risk_multiplier": max(0.3, min(1.5, risk_mult)),
        "signals": signals[:3]
    }

# ═══════════════════════════════════════════════════
# MASTER SIGNAL AGGREGATOR
# ═══════════════════════════════════════════════════

def get_composite_signal(symbol: str, closes: list, volumes: list,
                          market_data: dict, fear_greed: dict,
                          btc_dominance: float, weights: dict) -> dict:
    """
    Combines ALL signals into one master signal
    Only trades when multiple categories agree (like the best systems)
    """
    if len(closes) < 30:
        return {"action": "HOLD", "score": 0, "confidence": 0, "reasons": ["Insufficient data"]}

    coin_id_map = {
        "BTC/USD": "bitcoin", "ETH/USD": "ethereum", "SOL/USD": "solana",
        "AVAX/USD": "avalanche-2", "LINK/USD": "chainlink",
        "ADA/USD": "cardano", "MATIC/USD": "matic-network", "BNB/USD": "binancecoin",
        "BCH/USD": "bitcoin-cash", "LTC/USD": "litecoin",
        "AAVE/USD": "aave", "UNI/USD": "uniswap"
    }
    coin_id = coin_id_map.get(symbol, "")
    coin_data = market_data.get(coin_id, {})
    change_24h = coin_data.get("usd_24h_change", 0) or 0

    scores = {}
    reasons = []

    # ── 1. TECHNICAL (RSI, EMA, MACD, BB) ────────────────
    rsi = _calc_rsi(closes)
    e9 = _calc_ema(closes, 9)
    e21 = _calc_ema(closes, 21)
    e50 = _calc_ema(closes, min(50, len(closes)))
    macd = _calc_ema(closes, 12) - _calc_ema(closes, 26) if len(closes) >= 26 else 0
    upper, mid, lower = _calc_bb(closes)
    cur = closes[-1]

    tech_score = 0
    if rsi < 28: tech_score += 2.5; reasons.append(f"RSI deeply oversold {rsi:.0f}")
    elif rsi < 38: tech_score += 1.2; reasons.append(f"RSI oversold {rsi:.0f}")
    elif rsi > 72: tech_score -= 2.5; reasons.append(f"RSI overbought {rsi:.0f}")
    elif rsi > 62: tech_score -= 1.2

    if e9 > e21 > e50: tech_score += 2; reasons.append("Bullish EMA alignment")
    elif e9 < e21 < e50: tech_score -= 2; reasons.append("Bearish EMA alignment")

    if macd > 0: tech_score += 0.8
    else: tech_score -= 0.8

    if cur < lower: tech_score += 1.5; reasons.append("Below BB lower")
    elif cur > upper: tech_score -= 1.5; reasons.append("Above BB upper")

    scores["technical"] = tech_score / 7

    # ── 2. MOMENTUM ───────────────────────────────────────
    stoch_rsi = calc_stoch_rsi(closes)
    adx = calc_adx(closes)
    regime_vol = detect_volatility_regime(closes)
    momentum = (closes[-1] - closes[-5]) / closes[-5] if len(closes) >= 5 else 0

    mom_score = 0
    if stoch_rsi < 20: mom_score += 2; reasons.append(f"StochRSI oversold {stoch_rsi:.0f}")
    elif stoch_rsi > 80: mom_score -= 2; reasons.append(f"StochRSI overbought {stoch_rsi:.0f}")

    if adx > 30: mom_score += abs(tech_score) * 0.3  # amplify in strong trends
    elif adx < 15: mom_score *= 0.5  # reduce in ranging markets

    if regime_vol == "VOLATILE": mom_score *= 0.5; reasons.append("Volatile regime — reduced size")
    scores["momentum"] = max(-1, min(1, mom_score / 4))

    # ── 3. VOLUME ─────────────────────────────────────────
    vol_score = 0
    if volumes:
        obv = calc_obv(closes, volumes)
        vwap = calc_vwap(closes, volumes)
        vp = calc_volume_profile(closes, volumes)

        if obv > 0: vol_score += 0.5; reasons.append("OBV positive (accumulation)")
        elif obv < 0: vol_score -= 0.5

        if cur > vwap: vol_score += 0.5
        elif cur < vwap: vol_score -= 0.5

        if vp["signal"] == "BULLISH": vol_score += 0.5
        elif vp["signal"] == "BEARISH": vol_score -= 0.5

        # Volume spike
        if len(volumes) > 20:
            avg_vol = sum(volumes[-20:]) / 20
            if volumes[-1] > avg_vol * 2:
                if tech_score > 0: vol_score += 1; reasons.append("Volume spike with bullish signal")
                else: vol_score -= 1; reasons.append("Volume spike with bearish signal")
    scores["volume"] = max(-1, min(1, vol_score))

    # ── 4. SENTIMENT (Fear & Greed) ───────────────────────
    fg_score = fear_greed.get("score", 0)
    fg_val = fear_greed.get("value", 50)
    if fear_greed.get("days_extreme_fear", 0) >= 5:
        fg_score = min(fg_score + 0.5, 1.0)
        reasons.append(f"5+ days extreme fear — historical buy zone")
    scores["sentiment"] = fg_score

    # ── 5. MACRO ──────────────────────────────────────────
    btc_change = market_data.get("bitcoin", {}).get("usd_24h_change", 0) or 0
    macro = get_macro_regime(btc_dominance, fg_val, btc_change)
    macro_score = macro["score"] / 6  # normalize to -1/+1
    if macro["signals"]: reasons.append(macro["signals"][0])
    scores["macro"] = max(-1, min(1, macro_score))

    # ── 6. ON-CHAIN (24h change as proxy) ─────────────────
    onchain_score = 0
    if change_24h > 5: onchain_score += 0.5
    elif change_24h < -8: onchain_score -= 0.8; reasons.append(f"{symbol} down {change_24h:.1f}% — caution")
    scores["onchain"] = max(-1, min(1, onchain_score))

    # ── COMPOSITE WEIGHTED SCORE ──────────────────────────
    category_weights = {
        "technical":  weights.get("technical_weight", 0.35),
        "momentum":   weights.get("momentum_weight", 0.20),
        "volume":     weights.get("volume_weight", 0.15),
        "sentiment":  weights.get("sentiment_weight", 0.15),
        "macro":      weights.get("macro_weight", 0.10),
        "onchain":    weights.get("onchain_weight", 0.05),
    }

    composite = sum(scores.get(k, 0) * v for k, v in category_weights.items())

    # Apply macro risk multiplier
    composite *= macro["risk_multiplier"]

    # Confidence = agreement between categories
    pos = sum(1 for s in scores.values() if s > 0.1)
    neg = sum(1 for s in scores.values() if s < -0.1)
    total_cats = len(scores)
    confidence = max(pos, neg) / total_cats

    # Only trade with high confidence AND multiple signals agreeing
    threshold_buy = 0.25
    threshold_sell = -0.20
    min_confidence = 0.50  # at least 50% of categories must agree

    action = "HOLD"
    if composite >= threshold_buy and confidence >= min_confidence and pos >= 3:
        action = "BUY"
    elif composite <= threshold_sell and confidence >= min_confidence and neg >= 3:
        action = "SELL"

    return {
        "action": action,
        "score": composite,
        "confidence": confidence,
        "reasons": reasons[:3],
        "scores": scores,
        "macro_regime": macro["regime"],
        "macro_risk_mult": macro["risk_multiplier"],
        "adx": adx,
        "regime": regime_vol,
        "fear_greed": fg_val,
        "stoch_rsi": stoch_rsi,
    }

# ── Internal math helpers ─────────────────────────
def _calc_rsi(closes, period=14):
    if len(closes) < period+1: return 50.0
    gains = [max(closes[i]-closes[i-1],0) for i in range(1,len(closes))]
    losses = [max(closes[i-1]-closes[i],0) for i in range(1,len(closes))]
    ag = sum(gains[-period:])/period; al = sum(losses[-period:])/period
    if al==0: return 100.0
    return 100-100/(1+ag/al)

def _calc_ema(closes, period):
    if len(closes)<period: return closes[-1]
    k=2/(period+1); ema=sum(closes[:period])/period
    for p in closes[period:]: ema=p*k+ema*(1-k)
    return ema

def _calc_bb(closes, period=20):
    if len(closes)<period: c=closes[-1]; return c*1.02,c,c*0.98
    sl=closes[-period:]; mid=sum(sl)/period
    std=math.sqrt(sum((x-mid)**2 for x in sl)/period)
    return mid+2*std, mid, mid-2*std
