import csv


def load_prices(path):
    prices = {}
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = row["code"].strip()
            prices[code] = {
                "code": code,
                "name": row["name"].strip(),
                "yesterday_close": float(row["yesterday_close"]),
                "today_open": float(row["today_open"]),
                "today_volume": float(row.get("today_volume") or 0),
                "avg_5d_volume": float(row.get("avg_5d_volume") or 0),
            }
    return prices


def load_mention_history(path):
    history = {}
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = row["code"].strip()
            history[code] = {
                "code": code,
                "name": row.get("name", "").strip(),
                "avg_7d_mentions": float(row.get("avg_7d_mentions") or 0),
            }
    return history


def load_technical_indicators(path):
    indicators = {}
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = row["code"].strip()
            indicators[code] = {
                "code": code,
                "name": row.get("name", "").strip(),
                "ma5": float(row.get("ma5") or 0),
                "ma20": float(row.get("ma20") or 0),
                "ma60": float(row.get("ma60") or 0),
                "rsi14": float(row.get("rsi14") or 0),
                "macd_hist": float(row.get("macd_hist") or 0),
                "support": float(row.get("support") or 0),
                "resistance": float(row.get("resistance") or 0),
                "atr_pct": float(row.get("atr_pct") or 0),
            }
    return indicators


def pct_change(new_value, old_value):
    if old_value == 0:
        return 0
    return (new_value - old_value) / old_value * 100.0


def round_price(price):
    return round(price + 0.0000001, 2)


def ratio(value, baseline):
    if baseline <= 0:
        return value if value > 0 else 0
    return value / baseline


def capped_score(value, cap, scale):
    if value <= 0:
        return 0
    return min(value, cap) / float(cap) * scale


def build_trade_candidates(stats, prices, history=None, technicals=None, max_candidates=5):
    history = history or {}
    technicals = technicals or {}
    if not stats:
        return []

    max_mentions = max(item["mentions"] for item in stats.values()) or 1
    candidates = []

    for code, item in stats.items():
        if code not in prices:
            continue

        price = prices[code]
        mentions = item["mentions"]
        unique_authors = item.get("unique_authors", 0)
        avg_sentiment = item["score_sum"] / float(mentions)
        gap_pct = pct_change(price["today_open"], price["yesterday_close"])
        volume_ratio = ratio(price.get("today_volume", 0), price.get("avg_5d_volume", 0))
        technical = evaluate_technical(price, technicals.get(code))
        mention_baseline = history.get(code, {}).get("avg_7d_mentions", 0)
        heat_surge = ratio(mentions, mention_baseline)
        source_ratio = unique_authors / float(mentions)
        heat_score = mentions / float(max_mentions) * 100.0

        score = 0
        score += capped_score(heat_surge, 4.0, 35.0)
        score += capped_score(heat_score, 100.0, 20.0)
        score += max(min(avg_sentiment * 5.0, 20.0), -20.0)
        score += capped_score(volume_ratio, 2.0, 15.0)
        score += capped_score(source_ratio, 1.0, 10.0)
        score += technical["score"]
        if -1.0 <= gap_pct <= 4.0:
            score += 15.0
        elif gap_pct > 6.0:
            score -= 20.0
        elif gap_pct < -3.0:
            score -= 12.0

        if avg_sentiment <= 0:
            score -= 18.0
        if unique_authors <= 1 and mentions >= 3:
            score -= 12.0
        if technical["verdict"] == "技术不支持":
            score -= 20.0

        target_pct = 3.0 + min(max(avg_sentiment, 0), 4.0) * 0.6
        if heat_surge >= 2.0:
            target_pct += 0.6
        if volume_ratio >= 1.3:
            target_pct += 0.8
        if gap_pct > 4.0:
            target_pct -= 0.8
        if technical["verdict"] == "技术确认":
            target_pct += 0.5
        elif technical["verdict"] == "技术不支持":
            target_pct -= 1.0
        target_pct = max(2.0, min(target_pct, 6.5))

        stop_loss_pct = 2.5
        if technical.get("atr_pct", 0) > 3.0:
            stop_loss_pct = 3.0
        if gap_pct > 4.0:
            stop_loss_pct = 3.0
        elif gap_pct < 0:
            stop_loss_pct = 2.0

        buy_price = price["today_open"]
        target_sell_price = buy_price * (1.0 + target_pct / 100.0)
        stop_loss_price = buy_price * (1.0 - stop_loss_pct / 100.0)

        reasons = []
        reasons.append("X提及{}次，7日热度放大{:.2f}倍".format(mentions, heat_surge))
        reasons.append("平均情绪{:.2f}".format(avg_sentiment))
        reasons.append("今日开盘较昨收{:+.2f}%".format(gap_pct))
        reasons.append("成交量较5日均量{:.2f}倍".format(volume_ratio))
        if -1.0 <= gap_pct <= 4.0:
            reasons.append("开盘溢价处于可跟踪区间")
        if avg_sentiment > 0:
            reasons.append("舆情偏正面")
        elif avg_sentiment < 0:
            reasons.append("舆情偏负面，降低优先级")
        else:
            reasons.append("舆情中性")

        keywords = "、".join([word for word, _ in item["keywords"][:5]]) or "-"
        entry_logic = build_entry_logic(
            mentions, heat_score, heat_surge, unique_authors, avg_sentiment,
            gap_pct, volume_ratio, technical, keywords
        )
        price_plan = build_price_plan(buy_price, target_sell_price, stop_loss_price, target_pct, stop_loss_pct)
        invalidation = build_invalidation(avg_sentiment, gap_pct, stop_loss_price, volume_ratio, technical)

        candidates.append({
            "code": code,
            "name": item["name"],
            "industry": item["industry"],
            "mentions": mentions,
            "avg_sentiment": avg_sentiment,
            "heat_score": heat_score,
            "heat_surge": heat_surge,
            "avg_7d_mentions": mention_baseline,
            "unique_authors": unique_authors,
            "source_ratio": source_ratio,
            "yesterday_close": price["yesterday_close"],
            "today_open": price["today_open"],
            "gap_pct": gap_pct,
            "volume_ratio": volume_ratio,
            "technical_score": technical["score"],
            "technical_verdict": technical["verdict"],
            "technical_summary": technical["summary"],
            "trend_check": technical["trend_check"],
            "momentum_check": technical["momentum_check"],
            "risk_check": technical["risk_check"],
            "signal_score": score,
            "buy_price": round_price(buy_price),
            "target_sell_price": round_price(target_sell_price),
            "target_pct": target_pct,
            "stop_loss_price": round_price(stop_loss_price),
            "stop_loss_pct": stop_loss_pct,
            "keywords": keywords,
            "logic": "；".join(reasons),
            "entry_logic": entry_logic,
            "price_plan": price_plan,
            "invalidation": invalidation,
        })

    ranked = sorted(candidates, key=lambda item: (-item["signal_score"], item["code"]))
    return ranked[:max_candidates]


def evaluate_technical(price, technical):
    if not technical:
        return {
            "score": -8.0,
            "verdict": "缺少技术数据",
            "summary": "缺少均线、RSI、MACD、支撑压力等技术指标，无法做价格结构验证",
            "trend_check": "未验证",
            "momentum_check": "未验证",
            "risk_check": "未验证",
            "atr_pct": 0,
        }

    open_price = price["today_open"]
    ma5 = technical["ma5"]
    ma20 = technical["ma20"]
    ma60 = technical["ma60"]
    rsi = technical["rsi14"]
    macd_hist = technical["macd_hist"]
    support = technical["support"]
    resistance = technical["resistance"]
    atr_pct = technical["atr_pct"]

    trend_pass = open_price > ma5 and ma5 >= ma20 and ma20 >= ma60
    trend_watch = open_price > ma20 and ma5 >= ma20
    momentum_pass = 45 <= rsi <= 70 and macd_hist > 0
    momentum_watch = 40 <= rsi <= 75 and macd_hist >= 0
    upside_pct = pct_change(resistance, open_price) if resistance else 0
    downside_pct = pct_change(open_price, support) if support else 0
    risk_pass = upside_pct >= 3.0 and downside_pct <= 5.0 and atr_pct <= 4.0
    risk_watch = upside_pct >= 1.5 and atr_pct <= 5.0

    score = 0.0
    if trend_pass:
        score += 14.0
        trend_check = "通过：今开站上MA5，且MA5>=MA20>=MA60"
    elif trend_watch:
        score += 6.0
        trend_check = "观察：价格站上MA20，但多头排列不完整"
    else:
        score -= 12.0
        trend_check = "不通过：价格或均线结构偏弱"

    if momentum_pass:
        score += 10.0
        momentum_check = "通过：RSI处于45-70且MACD柱为正"
    elif momentum_watch:
        score += 3.0
        momentum_check = "观察：动量未破坏，但强度一般"
    else:
        score -= 10.0
        momentum_check = "不通过：RSI过弱/过热或MACD转弱"

    if risk_pass:
        score += 8.0
        risk_check = "通过：上方空间{:.2f}%，下方支撑距离{:.2f}%，波动可控".format(upside_pct, downside_pct)
    elif risk_watch:
        score += 2.0
        risk_check = "观察：上方空间{:.2f}%，ATR {:.2f}%，盈亏比一般".format(upside_pct, atr_pct)
    else:
        score -= 8.0
        risk_check = "不通过：上方空间不足或波动风险偏高"

    if trend_pass and momentum_pass and risk_pass:
        verdict = "技术确认"
    elif score >= 5:
        verdict = "技术观察"
    else:
        verdict = "技术不支持"

    summary = "{}；{}；{}".format(trend_check, momentum_check, risk_check)
    return {
        "score": score,
        "verdict": verdict,
        "summary": summary,
        "trend_check": trend_check,
        "momentum_check": momentum_check,
        "risk_check": risk_check,
        "atr_pct": atr_pct,
    }


def build_entry_logic(mentions, heat_score, heat_surge, unique_authors, avg_sentiment, gap_pct, volume_ratio, technical, keywords):
    parts = []
    parts.append("热度进入候选池：样本内提及{}次，标准化热度{:.0f}分，较7日均值放大{:.2f}倍".format(
        mentions, heat_score, heat_surge
    ))
    if unique_authors > 1:
        parts.append("来源分散度较好，来自{}个账号".format(unique_authors))
    else:
        parts.append("来源较集中，来自{}个账号，需要警惕单点噪音".format(unique_authors))
    if avg_sentiment > 1.5:
        parts.append("情绪强度较高，说明讨论不是单纯噪音")
    elif avg_sentiment > 0:
        parts.append("情绪偏正面，但强度一般，需要价格确认")
    else:
        parts.append("情绪不占优，只能作为观察标的")

    if -1.0 <= gap_pct <= 4.0:
        parts.append("开盘相对昨收{:+.2f}%，没有明显过度高开".format(gap_pct))
    elif gap_pct > 4.0:
        parts.append("开盘相对昨收{:+.2f}%，存在追高风险".format(gap_pct))
    else:
        parts.append("开盘相对昨收{:+.2f}%，资金承接偏弱".format(gap_pct))

    if volume_ratio >= 1.3:
        parts.append("成交量较5日均量放大{:.2f}倍，价格信号有量能配合".format(volume_ratio))
    elif volume_ratio > 0:
        parts.append("成交量较5日均量{:.2f}倍，量能确认一般".format(volume_ratio))
    else:
        parts.append("缺少成交量数据，价格确认不足")

    parts.append("技术面结论：{}；{}".format(technical["verdict"], technical["summary"]))

    if keywords and keywords != "-":
        parts.append("核心催化词：{}".format(keywords))
    return "；".join(parts)


def build_price_plan(buy_price, target_sell_price, stop_loss_price, target_pct, stop_loss_pct):
    return "参考买入价{:.2f}；若上涨约{:.1f}%至{:.2f}附近分批止盈；若跌破{:.2f}，按约{:.1f}%止损处理".format(
        buy_price,
        target_pct,
        target_sell_price,
        stop_loss_price,
        stop_loss_pct,
    )


def build_invalidation(avg_sentiment, gap_pct, stop_loss_price, volume_ratio, technical):
    rules = []
    rules.append("跌破止损价{:.2f}".format(stop_loss_price))
    if avg_sentiment <= 0:
        rules.append("舆情继续转弱")
    else:
        rules.append("热度回落且新增讨论转为负面")
    if gap_pct > 4.0:
        rules.append("高开后无法维持开盘价")
    else:
        rules.append("开盘后快速跌回昨收下方")
    if volume_ratio < 1.0:
        rules.append("成交量无法放大")
    if technical["verdict"] != "技术确认":
        rules.append("技术面未完成双重确认")
    return "；".join(rules)
