"""
多源文章 · A股产业链推荐引擎

Full pipeline:
1. Read articles from MULTIPLE sources (投中网, 财联社, 东方财富行业板块, AASTOCKS)
   → extract industries → match supply chain stocks
2. Analyze X sentiment for matched stocks
3. Evaluate technical indicators (MA/RSI/MACD/volume)
4. Generate buy/sell/stop prices
5. Label as 短线/中线
6. Output formatted report

Usage:
    python src/supply_chain_report.py
    python src/supply_chain_report.py \\
        --articles data/chinaventure_articles.csv \\
        --cls-articles data/cls_articles.csv \\
        --eastmoney-articles data/eastmoney_industry.csv \\
        --aastocks-articles data/aastocks_news.csv \\
        --date 2026-05-24
"""

import argparse
import csv
import os
import sys
from collections import Counter

# Project modules
from entities import find_stocks, load_stock_aliases
from sentiment import score_text
from backtest import load_market, mean, pct_change
from premarket_report import latest_market_rows
from industry_extract import load_supply_chain, extract_industries_from_text, match_supply_chain_stocks

# Industry relatedness matrix
# When articles cover a "hot" top-level industry, related downstream/supply
# industries also get scoring credit. This finds "unnoticed" stocks that
# benefit from hot themes but aren't the direct media focus.
INDUSTRY_RELATED = {
    "AI芯片": ["先进封装", "半导体设备", "半导体材料", "存储芯片", "功率半导体"],
    "人工智能": ["AI芯片", "存储芯片", "机器人", "消费电子"],
    "半导体": ["先进封装", "半导体设备", "半导体材料", "存储芯片", "AI芯片", "功率半导体"],
    "先进封装": ["半导体设备", "半导体材料"],
    "半导体设备": ["半导体材料", "先进封装"],
    "半导体材料": ["半导体设备", "先进封装"],
    "存储芯片": ["先进封装", "半导体设备", "半导体材料"],
    "功率半导体": ["新能源车", "光伏储能"],
    "新能源车": ["锂电池", "功率半导体", "机器人"],
    "新能源": ["新能源车", "光伏储能", "锂电池", "功率半导体"],
    "光伏储能": ["功率半导体", "锂电池"],
    "锂电池": ["新能源车", "功率半导体"],
    "消费电子": ["先进封装", "存储芯片", "半导体设备"],
    "医药创新": [],
    "机器人": ["人工智能", "消费电子", "功率半导体"],
}

# Lagging beneficiary mapping (接力逻辑)
# When Industry A is hot (high article count), stocks in Industry B (lagging beneficiary)
# get bonus points IF they haven't run yet (early price stage, not overheated RSI).
# This captures the "下游接续上涨" logic: A has already surged, B should benefit next.
# Bonus scale: 3pts (early) → 6pts (good room) → 10pts (plenty of room)
LAGGING_BENEFITS = {
    "存储芯片": ["先进封装", "半导体设备", "半导体材料"],
    "AI芯片": ["先进封装", "半导体设备", "半导体材料"],
    "人工智能": ["AI芯片", "存储芯片", "机器人"],
    "半导体": ["先进封装", "半导体设备", "半导体材料"],
    "新能源车": ["锂电池", "功率半导体", "机器人"],
    "新能源": ["光伏储能", "锂电池", "功率半导体"],
    "光伏储能": ["功率半导体"],
    "消费电子": ["先进封装", "存储芯片", "半导体设备"],
}


def build_industry_stock_map(supply_chain_data):
    """
    Build reverse mapping: industry -> [code1, code2, ...]
    Used to look up which stocks belong to a given industry,
    so we can calculate industry-level Twitter buzz for the
    sentiment relay signal (舆论接力信号).
    """
    _, stock_map, _ = supply_chain_data
    ind_map = {}
    for code, info in stock_map.items():
        ind = info.get("industry", "")
        if ind:
            if ind not in ind_map:
                ind_map[ind] = []
            ind_map[ind].append(code)
    return ind_map


def calc_sentiment_relay_score(price_stage_score, rsi_val, stock_inds, industries_found,
                                social, all_social_stats, industry_stock_map):
    """
    Sentiment Relay Signal (0-10): Twitter-verified relay logic (舆论接力信号).

    Core insight:
    - A hot industry (from 投中网) having Twitter buzz = theme is REAL
    - A downstream beneficiary having ZERO Twitter mentions = truly undiscovered
    - Combined: "中际旭创火了 + 东山精密无人问津" = best relay opportunity

    Formula: base(2-4) × confirm(0.8-1.5) × vacuum(0.5-1.5)

    When no Twitter data available, falls back to neutral (confirm=1.0, vacuum=1.0),
    behaving like the old lagging_bonus.
    """
    if not industries_found or price_stage_score <= 0:
        return 0

    rsi = float(rsi_val)
    if rsi >= 60:
        return 0  # Overheated, skip relay

    for hot_ind in industries_found:
        beneficiary_inds = LAGGING_BENEFITS.get(hot_ind, [])
        if not any(si in beneficiary_inds for si in stock_inds):
            continue

        # Step 1: Base score from price stage (how much room)
        if price_stage_score > 15:
            base = 4   # Deep in range bottom, plenty of room
        elif price_stage_score > 10:
            base = 3   # Moderate room
        elif price_stage_score > 0:
            base = 2   # Some room
        else:
            base = 0

        # Step 2: Industry ignition confirmation (0.8x - 1.5x)
        confirm = 1.0  # Neutral fallback

        if all_social_stats is not None and industry_stock_map is not None:
            # Count total Twitter mentions for stocks in this hot industry
            industry_mentions = 0
            hot_stock_codes = industry_stock_map.get(hot_ind, [])
            for code in hot_stock_codes:
                s = all_social_stats.get(code, {})
                industry_mentions += s.get("mentions", 0)

            if industry_mentions >= 5:
                confirm = 1.5  # 🔥 Industry confirmed hot on Twitter
            elif industry_mentions >= 2:
                confirm = 1.2  # Some buzz
            elif industry_mentions == 0:
                # Check if Twitter data exists at all
                total_all = sum(
                    s.get("mentions", 0) for s in all_social_stats.values()
                )
                if total_all > 0:
                    confirm = 0.8  # Twitter exists but industry has no buzz
                # total_all == 0 means no Twitter data, stay at 1.0

        # Step 3: Stock vacuum multiplier (0.5x - 1.5x)
        vacuum = 1.0
        if social:
            mentions = social.get("mentions", 0)
            if mentions == 0:
                vacuum = 1.5  # Truly undiscovered = best entry
            elif mentions <= 2:
                vacuum = 1.2  # Lightly discussed
            elif mentions <= 5:
                vacuum = 0.8  # Somewhat discovered, edge reduced
            else:
                vacuum = 0.5  # Too much attention, skip relay

        score = min(10, base * confirm * vacuum)
        return round(score, 1)

    return 0


# ── Data Loading ──────────────────────────────────────────────


def load_tweets(path):
    """Load tweets CSV and return list of rows.
    Returns empty list if file does not exist (graceful degradation for CI/cloud).
    """
    if not os.path.exists(path):
        print("WARNING: Tweets file not found: {}".format(path))
        print("         Returning empty dataset. Report will have no social data.")
        return []
    rows = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


def filter_tweets_by_date(tweets, date_str):
    """Filter tweets to a specific date."""
    return [t for t in tweets if t.get("date", "").strip() == date_str]


def filter_tweets_by_authors(tweets, authors_str):
    """Filter tweets to only those from specified author_ids (comma-separated)."""
    if not authors_str:
        return tweets
    whitelist = set(a.strip() for a in authors_str.split(",") if a.strip())
    if not whitelist:
        return tweets
    before = len(tweets)
    result = [t for t in tweets if t.get("author", "") in whitelist]
    print("  Filtered tweets by authors: {} -> {} rows".format(before, len(result)))
    return result


def build_social_stats_for_stocks(tweets, stock_codes, aliases):
    """
    Build social media stats for a given list of stock codes.
    Returns: {code: {mentions, unique_authors, score_sum, keywords, ...}}
    """
    stats = {}
    stock_lookup = {}  # code -> stock info from aliases
    for s in aliases:
        stock_lookup[s["code"]] = s

    from collections import Counter

    for tweet in tweets:
        text = tweet.get("text", "")
        sentiment = score_text(text)
        matches = find_stocks(text, aliases)

        for match in matches:
            code = match["code"]
            if code not in stock_codes:
                continue  # Only track stocks we care about

            if code not in stats:
                stats[code] = {
                    "code": code,
                    "name": match["name"],
                    "industry": match["industry"],
                    "mentions": 0,
                    "score_sum": 0,
                    "authors": set(),
                    "keyword_counter": Counter(),
                }
            stats[code]["mentions"] += 1
            stats[code]["score_sum"] += sentiment["score"]
            author = tweet.get("author", "").strip()
            if author:
                stats[code]["authors"].add(author)

    for item in stats.values():
        item["avg_sentiment"] = item["score_sum"] / float(max(item["mentions"], 1))
        item["unique_authors"] = len(item["authors"])
        del item["authors"]

    return stats


# ── Scoring Engine ────────────────────────────────────────────


def calc_price_stage_score(market_row, max_score):
    """
    Score based on how 'early stage' the stock is in its price cycle.
    '无人问津' = price near bottom of 20-day range, NOT yet broken out,
    still room to run. This finds stocks you can still 'enter early' (早期进场).
    
    High score = price low in range, far from resistance, not yet pumped.
    Low score = price near top of range, close to resistance, already ran.
    """
    close = float(market_row.get("close", 0))
    support = float(market_row.get("support", 0))
    resistance = float(market_row.get("resistance", 0))

    if close == 0 or support == 0 or resistance == 0 or resistance <= support:
        return round(max_score * 0.5, 1)

    # 1. Position in 20-day range: 0.0 (at bottom) → 1.0 (at top)
    range_pos = (close - support) / (resistance - support)
    range_pos = max(0.0, min(1.0, range_pos))

    # 2. Room to run: % from close up to resistance
    room_pct = (resistance - close) / close * 100
    room_pct = max(0.0, room_pct)

    # 3. Already broke out? (close > resistance → 得分归零)
    broke_out = close > resistance

    if broke_out:
        return 0  # Already ran, too late to enter early

    # Score = position_in_range × room_to_run
    # Lower in range = better, more room = better
    position_factor = 1.0 - range_pos           # 0 at top, 1 at bottom
    room_factor = min(1.0, room_pct / 8.0)      # 8%+ room = full factor

    score = max_score * position_factor * room_factor
    return round(max(0, min(max_score, score)), 1)


def evaluate_short_term(social, market_row, stock_info, all_social_stats=None, industries_found=None, industry_stock_map=None):
    """
    Short-term trading score (1-5 days).
    Finds downstream/undiscovered stocks (下游无人问津):
    - Supply chain depth 25%: PURE downstream = high, direct hot match = low
    - Price stage 25%: near bottom of 20-day range, room to run
    - Trend quality 15%: MA5>MA20 uptrend, MACD positive
    - Volume awakening 15%: 0.8-1.5x sweet spot
    - RSI sweet spot 10%: 30-60 ideal
    - MA proximity 10%: price near MA20 support

    CRITICAL: Direct match to hot article industries = PENALIZED (产业top已过热)
    Pure downstream beneficiary = REWARDED (无人问津的下游)
    MA5<MA20 downtrend = GLOBAL CAP at 50 (不可逆势)
    """
    score = 0
    details = {}

    # 1. Supply chain depth (0-25): PENALIZE obvious hot stocks
    matched_ind = stock_info.get("matched_industry", "")
    industry_group = stock_info.get("industry_group", "")
    stock_industry = stock_info.get("industry", "")

    stock_inds = set()
    if matched_ind:
        stock_inds.add(matched_ind)
    if industry_group:
        stock_inds.add(industry_group)
    if stock_industry:
        stock_inds.add(stock_industry)

    if industries_found:
        direct_match = any(ind in industries_found for ind in stock_inds)
        related_match = False
        for hot_ind in industries_found:
            related_inds = INDUSTRY_RELATED.get(hot_ind, [])
            if any(si in related_inds for si in stock_inds):
                related_match = True
                break

        # Check if stock is a lagging beneficiary of any hot industry (接力逻辑)
        is_lagging = False
        if industries_found:
            for hot_ind in industries_found:
                beneficiary_inds = LAGGING_BENEFITS.get(hot_ind, [])
                if any(si in beneficiary_inds for si in stock_inds):
                    is_lagging = True
                    break

        if related_match and not direct_match:
            # PURE downstream: NOT in the hot industry = SWEET SPOT
            sc_depth_score = 25
        elif direct_match and not related_match:
            # Directly in hot industry = OBVIOUS TOP, penalized heavily
            sc_depth_score = 5
        elif direct_match and related_match:
            # Both hot and downstream:
            # If stock has relay logic (lagging beneficiary of a hot industry),
            # override to full 25 — 接力逻辑不扣分
            sc_depth_score = 25 if is_lagging else 15
        else:
            sc_depth_score = 0
    else:
        sc_depth_score = 0
    score += sc_depth_score
    details["supply_chain_depth"] = round(sc_depth_score, 1)

    # 2. Price stage / 早期进场 (0-25): price near bottom of range
    price_stage_score = calc_price_stage_score(market_row, 25)
    score += price_stage_score
    details["price_stage"] = round(price_stage_score, 1)

    # 3. Volume awakening (0-15)
    avg_vol = float(market_row.get("avg_5d_volume", 0))
    today_vol = float(market_row.get("volume", 0))
    vol_ratio = today_vol / avg_vol if avg_vol > 0 else 0

    if 0.8 <= vol_ratio <= 1.5:
        vol_score = 15
    elif 0.5 <= vol_ratio < 0.8:
        vol_score = 8
    elif 1.5 < vol_ratio <= 2.0:
        vol_score = 8
    elif vol_ratio > 2.0:
        vol_score = 3
    else:
        vol_score = 0
    score += vol_score
    details["volume"] = round(vol_score, 1)
    details["vol_ratio"] = round(vol_ratio, 2)

    # 4. RSI sweet spot (0-10)
    rsi = float(market_row.get("rsi14", 50))
    if 30 <= rsi <= 60:
        rsi_score = 10
    elif 60 < rsi <= 70 or 25 <= rsi < 30:
        rsi_score = 7
    elif 70 < rsi <= 80 or 20 <= rsi < 25:
        rsi_score = 3
    else:
        rsi_score = 0
    score += rsi_score
    details["rsi"] = round(rsi_score, 1)

    # 5. MA proximity (0-10): price near MA20 = room to run
    ma5 = float(market_row.get("ma5", 0))
    ma20 = float(market_row.get("ma20", 0))
    close = float(market_row.get("close", 0))

    if ma20 > 0 and close > 0:
        pct_from_ma20 = (close - ma20) / ma20 * 100
        if -2 <= pct_from_ma20 <= 3:
            ma_score = 10
        elif -5 <= pct_from_ma20 < -2:
            ma_score = 8
        elif 3 < pct_from_ma20 <= 8:
            ma_score = 5
        elif pct_from_ma20 > 8:
            ma_score = 0
        else:
            ma_score = 3
    else:
        ma_score = 5
    score += ma_score
    details["ma_proximity"] = round(ma_score, 1)

    # 6. Trend quality (0-15): MA5 vs MA20, MACD sign
    macd_hist = float(market_row.get("macd_hist", 0))
    trend_score = 0
    if ma5 > 0 and ma20 > 0 and close > 0:
        if ma5 > ma20:
            # Uptrend — reward
            trend_score += 10
            if macd_hist > 0:
                trend_score += 5  # MACD confirms uptrend
        else:
            # Downtrend — minimal score
            trend_score = 3 if macd_hist > -0.1 else 0
    details["trend"] = round(trend_score, 1)
    score += trend_score

    # 7. Sentiment Relay Signal (0-10): Twitter-verified relay logic (舆论接力信号)
    # Replaces old lagging_bonus. Adds Twitter verification:
    # - Hot industry Twitter buzz = theme confirmation (点火验证)
    # - Stock's own zero mentions = undiscovered gem (舆论真空)
    # Formula: base(2-4) × confirm(0.8-1.5) × vacuum(0.5-1.5)
    sentiment_relay = calc_sentiment_relay_score(
        price_stage_score,
        market_row.get("rsi14", 50),
        stock_inds,
        industries_found,
        social,
        all_social_stats,
        industry_stock_map,
    )
    details["sentiment_relay"] = sentiment_relay
    score += sentiment_relay

    # ── GLOBAL TREND CAP ──
    # If stock is in downtrend, severely limit total score
    if ma5 > 0 and ma20 > 0:
        if close < ma5 and ma5 < ma20:
            # Strong downtrend: close < MA5 < MA20 → cap at 30
            score = min(score, 30)
        elif ma5 < ma20:
            # Mild downtrend: MA5 < MA20 but close >= MA5 → cap at 50
            score = min(score, 50)

    return round(score, 1), details


def evaluate_mid_term(social, market_row, stock_info, all_social_stats=None, industries_found=None, industry_stock_map=None):
    """
    Mid-term trading score (1-4 weeks).
    Finds downstream/undiscovered stocks with mid-term room to run:
    - Industry tailwind 25%: PURE downstream = high, direct hot match = low
    - Price stage 25%: price near bottom of range
    - MA support 20%: price at/near MA20/MA60 support
    - MACD turning 15%: histogram turning positive or near zero-cross
    - Volume base building 15%: consistent moderate volume

    CRITICAL: Same as short-term — direct hot match penalized, downtrend capped.
    """
    score = 0
    details = {}

    # 1. Industry tailwind (0-25): PENALIZE obvious hot stocks
    matched_ind = stock_info.get("matched_industry", "")
    industry_group = stock_info.get("industry_group", "")
    stock_industry = stock_info.get("industry", "")

    stock_inds = set()
    if matched_ind:
        stock_inds.add(matched_ind)
    if industry_group:
        stock_inds.add(industry_group)
    if stock_industry:
        stock_inds.add(stock_industry)

    if industries_found:
        direct_match = any(ind in industries_found for ind in stock_inds)
        related_match = False
        for hot_ind in industries_found:
            related_inds = INDUSTRY_RELATED.get(hot_ind, [])
            if any(si in related_inds for si in stock_inds):
                related_match = True
                break

        # Check if stock is a lagging beneficiary of any hot industry (接力逻辑)
        is_lagging = False
        if industries_found:
            for hot_ind in industries_found:
                beneficiary_inds = LAGGING_BENEFITS.get(hot_ind, [])
                if any(si in beneficiary_inds for si in stock_inds):
                    is_lagging = True
                    break

        if related_match and not direct_match:
            ind_score = 25  # PURE downstream = sweet spot
        elif direct_match and not related_match:
            ind_score = 5   # Direct hot = penalized
        elif direct_match and related_match:
            # Both hot and downstream:
            # If stock has relay logic (lagging beneficiary), override to full 25
            ind_score = 25 if is_lagging else 15
        else:
            ind_score = 0
    else:
        ind_score = 0
    score += ind_score
    details["industry_tailwind"] = round(ind_score, 1)

    # 2. Price stage / 早期进场 (0-25): price near bottom of range
    price_stage_score = calc_price_stage_score(market_row, 25)
    score += price_stage_score
    details["price_stage"] = round(price_stage_score, 1)

    # 3. MA support (0-20): price at or near MA20/MA60 support
    ma5 = float(market_row.get("ma5", 0))
    ma20 = float(market_row.get("ma20", 0))
    ma60 = float(market_row.get("ma60", 0))
    close = float(market_row.get("close", 0))

    if ma20 > 0 and ma60 > 0 and close > 0:
        pct_from_ma20 = (close - ma20) / ma20 * 100
        pct_from_ma60 = (close - ma60) / ma60 * 100

        if -2 <= pct_from_ma20 <= 5:
            ma_score = 20
        elif ma60 > 0 and -2 <= pct_from_ma60 <= 5:
            ma_score = 15
        elif 5 < pct_from_ma20 <= 10:
            ma_score = 10
        elif pct_from_ma20 > 10:
            ma_score = 0
        else:
            ma_score = 5
    else:
        ma_score = 10
    score += ma_score
    details["ma_support"] = round(ma_score, 1)

    # 4. MACD turning (0-15): near zero-cross is ideal
    macd_hist = float(market_row.get("macd_hist", 0))
    if 0 < macd_hist < 1:
        macd_score = 15
    elif macd_hist >= 1:
        macd_score = 10
    elif -1 <= macd_hist <= 0:
        macd_score = 8
    elif -2 <= macd_hist < -1:
        macd_score = 3
    else:
        macd_score = 0
    score += macd_score
    details["macd"] = round(macd_score, 1)

    # 5. Volume base building (0-15)
    avg_vol = float(market_row.get("avg_5d_volume", 0))
    today_vol = float(market_row.get("volume", 0))
    vol_ratio = today_vol / avg_vol if avg_vol > 0 else 0

    if 0.8 <= vol_ratio <= 1.8:
        vol_score = 15
    elif 0.5 <= vol_ratio < 0.8:
        vol_score = 8
    elif 1.8 < vol_ratio <= 3.0:
        vol_score = 6
    elif vol_ratio > 3.0:
        vol_score = 3
    else:
        vol_score = 0
    score += vol_score
    details["volume"] = round(vol_score, 1)
    details["vol_ratio"] = round(vol_ratio, 2)

    # 6. Sentiment Relay Signal (0-10): Twitter-verified relay logic (舆论接力信号)
    sentiment_relay = calc_sentiment_relay_score(
        price_stage_score,
        market_row.get("rsi14", 50),
        stock_inds,
        industries_found,
        social,
        all_social_stats,
        industry_stock_map,
    )
    details["sentiment_relay"] = sentiment_relay
    score += sentiment_relay

    # ── GLOBAL TREND CAP ──
    if ma5 > 0 and ma20 > 0:
        if close < ma5 and ma5 < ma20:
            score = min(score, 30)  # Strong downtrend
        elif ma5 < ma20:
            score = min(score, 50)  # Mild downtrend

    return round(score, 1), details


# ── Price Calculator ──────────────────────────────────────────


def calc_short_term_prices(market_row, social):
    """
    Calculate short-term buy/sell/stop prices.
    Undiscovered gems have higher potential: 3.0-8.0%
    """
    close = float(market_row.get("close", 0))
    if close == 0:
        return {"buy_price": 0, "target_price": 0, "stop_price": 0}

    # Buy price: slightly below close for dip buying
    buy_price = round(close * 0.99, 2)

    # Target: undiscovered stocks have more room to run
    target_pct = 3.0
    # Sentiment boost (capped)
    if social.get("avg_sentiment", 0) > 0:
        target_pct += min(social.get("avg_sentiment", 0), 4.0) * 0.5
    # Volume awakening boost
    avg_vol = float(market_row.get("avg_5d_volume", 0))
    today_vol = float(market_row.get("volume", 0))
    vol_ratio = today_vol / avg_vol if avg_vol > 0 else 0
    if 0.8 <= vol_ratio <= 1.5:
        target_pct += 1.0  # Healthy awakening volume
    elif vol_ratio > 1.5:
        target_pct += 0.4
    # Low mention bonus (undiscovered = more room to run)
    mentions = social.get("mentions", 0)
    if mentions <= 2:
        target_pct += 1.5  # Undiscovered gem bonus
    elif mentions <= 5:
        target_pct += 0.8

    # Cap for safety: undiscovered gems can go 3-8%
    target_pct = max(3.0, min(target_pct, 8.0))
    target_price = round(buy_price * (1 + target_pct / 100.0), 2)

    # Stop loss: -3%
    stop_price = round(buy_price * 0.97, 2)

    return {
        "buy_price": buy_price,
        "target_price": target_price,
        "stop_price": stop_price,
        "target_pct": round(target_pct, 1),
    }


def calc_mid_term_prices(market_row, social):
    """
    Calculate mid-term buy/sell/stop prices.
    Undiscovered gems have higher potential: 8.0-20.0%
    """
    close = float(market_row.get("close", 0))
    ma20 = float(market_row.get("ma20", 0))
    if close == 0:
        return {"buy_price": 0, "target_price": 0, "stop_price": 0}

    # Buy price: near MA20 for mid-term
    buy_price = round(min(close, ma20) if ma20 > 0 else close * 0.97, 2)

    # Target: undiscovered mid-term has 8-20% potential
    target_pct = 8.0
    # MA structure boost
    ma5 = float(market_row.get("ma5", 0))
    ma60 = float(market_row.get("ma60", 0))
    if ma5 > ma20 > ma60:
        target_pct += 2.0  # Bullish structure
    # Sentiment boost
    if social.get("avg_sentiment", 0) > 0.5:
        target_pct += 2.0
    # Low mention bonus (undiscovered = more room to run)
    mentions = social.get("mentions", 0)
    if mentions <= 2:
        target_pct += 3.0  # Undiscovered gem: big upside
    elif mentions <= 5:
        target_pct += 1.5
    elif mentions <= 10:
        target_pct += 0.5

    target_pct = max(8.0, min(target_pct, 20.0))
    target_price = round(buy_price * (1 + target_pct / 100.0), 2)

    # Stop loss: -7%
    stop_price = round(buy_price * 0.93, 2)

    return {
        "buy_price": buy_price,
        "target_price": target_price,
        "stop_price": stop_price,
        "target_pct": round(target_pct, 1),
    }


# ── Report Generator ──────────────────────────────────────────


def generate_report(articles_data, social_stats, market_data, supply_chain_data, report_date, exclude_prefixes=None):
    """Generate the full formatted report."""
    industries_db, stock_map, _ = supply_chain_data
    industry_stock_map = build_industry_stock_map(supply_chain_data)
    lines = []

    # Build a union of all stocks to consider: those with market data AND in supply chain
    all_codes = set()
    for code in market_data:
        if code in stock_map:
            # Apply prefix exclusion filter
            if exclude_prefixes:
                skip = False
                for prefix in exclude_prefixes:
                    if code.startswith(prefix):
                        skip = True
                        break
                if skip:
                    continue
            all_codes.add(code)

    # Header
    lines.append("# 多源行业热度 · A股产业链推荐报告")
    lines.append("")
    lines.append("**报告日期**：{}".format(report_date))
    lines.append("")
    lines.append("---")
    lines.append("")

    # Section 1: Industry overview from articles
    lines.append("## 一、今日覆盖行业（投中网 + 财联社 + 东方财富 + AASTOCKS）")
    lines.append("")
    industries_found = articles_data.get("industries_found", {})
    articles_by_ind = articles_data.get("articles_by_industry", {})
    stocks_by_ind = articles_data.get("stocks_by_industry", {})

    if not industries_found:
        lines.append("今日未从投中网文章中识别到行业主题。")
        lines.append("")
    else:
        # Sort by mention count
        sorted_industries = sorted(industries_found.items(), key=lambda x: -x[1])
        for idx, (industry, count) in enumerate(sorted_industries, 1):
            articles_list = articles_by_ind.get(industry, [])
            stocks_list = stocks_by_ind.get(industry, [])

            # Heat indicator
            heat = "🔥" * min(count, 3)
            lines.append("**{}. {}** {} — {}篇文章".format(idx, industry, heat, count))
            if articles_list:
                for a in articles_list[:3]:
                    lines.append("   - {}".format(a[:60]))
            if stocks_list:
                stock_names = "、".join([s["name"] for s in stocks_list[:5]])
                lines.append("   - 产业链映射：{}".format(stock_names))
            lines.append("")

    lines.append("---")
    lines.append("")

    # Section 2: Short-term candidates
    lines.append("## 二、🔥 短线推荐候选（持有1-5天）")
    lines.append("")
    lines.append("| 排名 | 代码 | 名称 | 产业链 | 总分 | 供应链深度 | 价格阶段 | 量能觉醒 | RSI | MA支撑 | 参考买入价 | 预期卖出价 | 止损价 |")
    lines.append("|:---:|:---:|:---:|:------:|:----:|:---------:|:--------:|:--------:|:---:|:------:|:---------:|:---------:|:-----:|")

    short_candidates = []
    for code in all_codes:
        row = market_data[code]
        social = social_stats.get(code, {
            "code": code,
            "name": stock_map.get(code, {}).get("name", ""),
            "avg_sentiment": 0,
            "mentions": 0,
            "unique_authors": 0,
        })
        stock_info = stock_map.get(code, {})
        # Get matched_industry from articles result, fallback to supply chain industry
        matched = articles_data.get("matched_stocks", {}).get(code, {})
        industry = matched.get("matched_industry", stock_info.get("industry_group", ""))
        # Build stock_info dict with both db info and matched industry for scoring
        full_stock_info = {**stock_info, "matched_industry": matched.get("matched_industry", "")}

        score, details = evaluate_short_term(social, row, full_stock_info, social_stats, industries_found, industry_stock_map)
        prices = calc_short_term_prices(row, social)
        vol_ratio = float(row.get("volume", 0)) / float(row.get("avg_5d_volume", 1)) if float(row.get("avg_5d_volume", 0)) > 0 else 0

        short_candidates.append({
            "code": code,
            "name": stock_info.get("name", social.get("name", "")),
            "industry": industry,
            "score": score,
            "sc_depth": details.get("supply_chain_depth", 0),
            "price_stage": details.get("price_stage", 0),
            "vol_score": details.get("volume", 0),
            "rsi_score": details.get("rsi", 0),
            "ma_score": details.get("ma_proximity", 0),
            "sentiment_relay": details.get("sentiment_relay", 0),
            "mentions": social["mentions"],
            "sentiment": round(social["avg_sentiment"], 2),
            "rsi": round(float(row.get("rsi14", 0)), 1),
            **prices,
        })

    short_candidates.sort(key=lambda x: -x["score"])

    for idx, c in enumerate(short_candidates[:5], 1):
        lines.append("| {} | {} | {} | {} | {:.1f} | {:.0f} | {:.0f} | {:.0f} | {:.0f} | {:.0f} | {:.2f} | {:.2f} | {:.2f} |".format(
            idx, c["code"], c["name"], c["industry"],
            c["score"], c["sc_depth"], c["price_stage"],
            c["vol_score"], c["rsi_score"], c["ma_score"],
            c["buy_price"], c["target_price"], c["stop_price"],
        ))

    if not short_candidates:
        lines.append("| 暂无短线候选 |")
    lines.append("")

    # Section 3: Mid-term candidates
    lines.append("## 三、📈 中线推荐候选（持有1-4周）")
    lines.append("")
    lines.append("| 排名 | 代码 | 名称 | 产业链 | 总分 | 行业顺风 | 价格空间 | MA支撑 | MACD | 量能 | 参考买入价 | 预期卖出价 | 止损价 |")
    lines.append("|:---:|:---:|:---:|:------:|:----:|:--------:|:--------:|:------:|:----:|:----:|:---------:|:---------:|:-----:|")

    mid_candidates = []
    for code in all_codes:
        row = market_data[code]
        social = social_stats.get(code, {
            "code": code,
            "name": stock_map.get(code, {}).get("name", ""),
            "avg_sentiment": 0,
            "mentions": 0,
            "unique_authors": 0,
        })
        stock_info = stock_map.get(code, {})
        matched = articles_data.get("matched_stocks", {}).get(code, {})
        industry = matched.get("matched_industry", stock_info.get("industry_group", ""))
        # Build stock_info dict with both db info and matched industry for scoring
        full_stock_info = {**stock_info, "matched_industry": matched.get("matched_industry", "")}

        score, details = evaluate_mid_term(social, row, full_stock_info, social_stats, industries_found, industry_stock_map)
        prices = calc_mid_term_prices(row, social)

        mid_candidates.append({
            "code": code,
            "name": stock_info.get("name", social.get("name", "")),
            "industry": industry,
            "score": score,
            "ind_tailwind": details.get("industry_tailwind", 0),
            "price_stage": details.get("price_stage", 0),
            "ma_support": details.get("ma_support", 0),
            "macd_score": details.get("macd", 0),
            "vol_score": details.get("volume", 0),
            "sentiment_relay": details.get("sentiment_relay", 0),
            "sentiment": round(social["avg_sentiment"], 2),
            **prices,
        })

    mid_candidates.sort(key=lambda x: -x["score"])

    for idx, c in enumerate(mid_candidates[:5], 1):
        lines.append("| {} | {} | {} | {} | {:.1f} | {:.0f} | {:.0f} | {:.0f} | {:.0f} | {:.0f} | {:.2f} | {:.2f} | {:.2f} |".format(
            idx, c["code"], c["name"], c["industry"],
            c["score"], c["ind_tailwind"], c["price_stage"],
            c["ma_support"], c["macd_score"], c["vol_score"],
            c["buy_price"], c["target_price"], c["stop_price"],
        ))

    if not mid_candidates:
        lines.append("| 暂无中线候选 |")
    lines.append("")

    # Section 4: Detailed logic
    lines.append("---")
    lines.append("")
    lines.append("## 四、逐股逻辑")
    lines.append("")

    all_candidates = []
    for c in short_candidates[:5]:
        all_candidates.append(("短线", c))
    for c in mid_candidates[:5]:
        # Deduplicate
        if not any(c2["code"] == c["code"] for _, c2 in all_candidates):
            all_candidates.append(("中线", c))

    for idx, (trade_type, c) in enumerate(all_candidates, 1):
        lines.append("**{}. {}（{}）- {}**".format(idx, c["name"], c["code"], trade_type))
        lines.append("")
        lines.append("| 项目 | 值 |")
        lines.append("|------|-----|")
        s = social_stats.get(c["code"], {})
        lines.append("| 产业链位置 | {} |".format(c.get("industry", "-")))
        lines.append("| X 提及 | {}次（{}个独立账号） |".format(
            s.get("mentions", 0),
            s.get("unique_authors", 0),
        ))
        lines.append("| X 情绪分 | {:.2f} |".format(c["sentiment"]))
        lines.append("| 信号评分 | {:.1f} |".format(c["score"]))
        relay = c.get("sentiment_relay", 0)
        if relay > 0:
            lines.append("| 舆论接力信号 | +{:.1f}（产业链龙头舆论验证 + 股价空间确认） |".format(relay))
        lines.append("| 参考买入价 | {:.2f} |".format(c.get("buy_price", 0)))
        lines.append("| 预期卖出价 | {:.2f}（+{}%） |".format(c.get("target_price", 0), c.get("target_pct", 0)))
        lines.append("| 风控止损价 | {:.2f} |".format(c.get("stop_price", 0)))
        lines.append("")
        lines.append("**买入条件**：次日开盘后观察 15-30 分钟，确认价格在买入价附近且量能配合，方可入场。")
        if trade_type == "短线":
            lines.append("**卖出策略**：达到目标价分批止盈；若跌破止损价果断离场。")
        else:
            lines.append("**卖出策略**：中线持有，周线级别跟踪，趋势走坏或达到目标价止盈。")
        lines.append("**失效条件**：次日开盘涨幅 >4%（不追高）；开盘快速跌破止损价；X 新增讨论转负面。")
        lines.append("")

    # Section 5: Risk disclaimer
    lines.append("---")
    lines.append("")
    lines.append("## ⚠️ 风险提示")
    lines.append("")
    lines.append("- 本报告基于投中网文章主题匹配 + X 社交媒体情绪 + 技术面量化规则生成，**不构成投资建议或收益承诺**。")
    lines.append("- 投中网文章内容反映的是媒体/投资机构关注方向，不代表短期股价必然上涨。")
    lines.append("- X 社交媒体数据容易受刷屏、机器人账号和情绪化内容影响，需要结合成交量、公告等验证。")
    lines.append("- 技术面指标基于历史数据，不能预测未来，需结合实时盘口确认。")
    lines.append("- 入市有风险，投资需谨慎。建议分散持仓，控制仓位。")
    lines.append("")

    return "\n".join(lines)


def merge_article_sources(results):
    """Merge article processing results from multiple data sources.

    Takes a list of process_articles() result dicts and merges them:
      - industries_found: counts are summed
      - matched_stocks: codes from all sources, later sources override
      - articles_by_industry: article titles are concatenated
      - stocks_by_industry: stock lists are merged (deduplicated by code)

    Returns a single merged result dict.
    """
    merged = {
        "article_count": 0,
        "industries_found": {},
        "matched_stocks": {},
        "articles_by_industry": {},
        "stocks_by_industry": {},
    }

    for result in results:
        merged["article_count"] += result.get("article_count", 0)

        # Merge industries_found (sum counts)
        for ind, count in result.get("industries_found", {}).items():
            merged["industries_found"][ind] = merged["industries_found"].get(ind, 0) + count

        # Merge matched_stocks (deduplicated by code)
        for code, stock_info in result.get("matched_stocks", {}).items():
            if code not in merged["matched_stocks"]:
                merged["matched_stocks"][code] = stock_info

        # Merge articles_by_industry (concatenate titles)
        for ind, titles in result.get("articles_by_industry", {}).items():
            merged["articles_by_industry"].setdefault(ind, [])
            for t in titles:
                if t not in merged["articles_by_industry"][ind]:
                    merged["articles_by_industry"][ind].append(t)

        # Merge stocks_by_industry (deduplicate by code per industry)
        for ind, stocks in result.get("stocks_by_industry", {}).items():
            merged["stocks_by_industry"].setdefault(ind, [])
            existing_codes = {s["code"] for s in merged["stocks_by_industry"][ind]}
            for s in stocks:
                if s["code"] not in existing_codes:
                    existing_codes.add(s["code"])
                    merged["stocks_by_industry"][ind].append(s)

    return merged


def write_lines(path, content):
    directory = os.path.dirname(path)
    if directory and not os.path.exists(directory):
        os.makedirs(directory)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print("Report written to {}".format(path))


# ── Main Pipeline ─────────────────────────────────────────────


def main():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    parser = argparse.ArgumentParser(description="多源文章 · A股产业链推荐引擎")
    parser.add_argument("--articles", default=os.path.join(base_dir, "data", "chinaventure_articles.csv"),
                        help="投中网文章 CSV")
    parser.add_argument("--cls-articles", default=os.path.join(base_dir, "data", "cls_articles.csv"),
                        help="财联社电报文章 CSV")
    parser.add_argument("--eastmoney-articles", default=os.path.join(base_dir, "data", "eastmoney_industry.csv"),
                        help="东方财富行业板块 CSV")
    parser.add_argument("--aastocks-articles", default=os.path.join(base_dir, "data", "aastocks_news.csv"),
                        help="AASTOCKS 财经新闻 CSV")
    parser.add_argument("--tweets", default=os.path.join(base_dir, "data", "real_tweets.csv"))
    parser.add_argument("--market", default=os.path.join(base_dir, "data", "real_market_full.csv"))
    parser.add_argument("--aliases", default=os.path.join(base_dir, "data", "stock_aliases.csv"))
    parser.add_argument("--supply-chain", default=os.path.join(base_dir, "data", "supply_chain.csv"))
    parser.add_argument("--date", default="")
    parser.add_argument("--tweet-date", default="")
    parser.add_argument("--market-date", default="")
    parser.add_argument("--authors", default="", help="Comma-separated author_ids to include (e.g. '1518874924718653441,20659155')")
    parser.add_argument("--exclude-prefixes", default="", help="Comma-separated stock code prefixes to exclude (e.g. '300,688' to exclude 创业板 and 科创板)")
    parser.add_argument("--out", default=os.path.join(base_dir, "reports", "supply_chain_report.md"))
    args = parser.parse_args()

    from datetime import datetime
    report_date = args.date or datetime.now().strftime("%Y-%m-%d")
    tweet_date = args.tweet_date or report_date
    market_date = args.market_date or report_date
    exclude_prefixes = [p.strip() for p in args.exclude_prefixes.split(",") if p.strip()] if args.exclude_prefixes else None

    print("=" * 60)
    print("多源文章 · A股产业链推荐引擎")
    print("=" * 60)
    print("Report date: {}".format(report_date))
    print()

    # 1. Load supply chain knowledge base
    print("[1/5] Loading supply chain knowledge base...")
    supply_chain_data = load_supply_chain(args.supply_chain)
    industries_db, stock_map, industry_keywords = supply_chain_data
    print("  {} industries, {} stocks loaded".format(len(industries_db), len(stock_map)))

    # 2. Load and process articles from ALL sources
    print("[2/5] Loading articles from all sources...")
    from industry_extract import process_articles

    article_sources = [
        ("投中网", args.articles),
        ("财联社", args.cls_articles),
        ("东方财富行业", args.eastmoney_articles),
        ("AASTOCKS", args.aastocks_articles),
    ]

    all_results = []
    for source_name, source_path in article_sources:
        if os.path.exists(source_path):
            result = process_articles(source_path, args.supply_chain)
            print("  [{}] {} articles processed, industries: {}".format(
                source_name, result["article_count"],
                list(result["industries_found"].keys()),
            ))
            all_results.append(result)
        else:
            print("  [{}] No file found at {}, skipping".format(source_name, source_path))

    if all_results:
        articles_result = merge_article_sources(all_results)
        print("  MERGED: {} articles total, {} industries found".format(
            articles_result["article_count"],
            len(articles_result["industries_found"]),
        ))
        print("  Industries: {}".format(list(articles_result["industries_found"].keys())))
    else:
        print("  No article sources available, using empty mode")
        articles_result = {
            "article_count": 0,
            "industries_found": {},
            "matched_stocks": {},
            "articles_by_industry": {},
            "stocks_by_industry": {},
        }

    # 3. Load X tweets and build social stats
    print("[3/5] Analyzing X social data...")
    tweets = load_tweets(args.tweets)
    if tweet_date:
        tweets = filter_tweets_by_date(tweets, tweet_date)
    if args.authors:
        tweets = filter_tweets_by_authors(tweets, args.authors)
    print("  {} tweets loaded for date {}".format(len(tweets), tweet_date))

    # Get all stock codes we care about from articles' matched stocks
    all_stock_codes = set()
    if articles_result.get("matched_stocks"):
        all_stock_codes = set(articles_result["matched_stocks"].keys())

    # Load aliases and build stats
    aliases = load_stock_aliases(args.aliases)
    social_stats = build_social_stats_for_stocks(tweets, all_stock_codes, aliases)

    # Also check all supply chain stocks for X mentions
    for code in stock_map:
        if code not in all_stock_codes:
            all_stock_codes.add(code)

    # Rebuild with all supply chain codes if needed
    if not social_stats:
        social_stats = build_social_stats_for_stocks(tweets, all_stock_codes, aliases)

    print("  {} stocks with X mentions tracked".format(len(social_stats)))

    # 4. Load market data
    print("[4/5] Loading market data...")
    market = latest_market_rows(load_market(args.market), market_date or None)
    print("  {} stocks with market data".format(len(market)))

    # 5. Generate report
    print("[5/5] Generating report...")
    report = generate_report(articles_result, social_stats, market, supply_chain_data, report_date, exclude_prefixes)
    write_lines(args.out, report)

    print()
    print("Done! Report: {}".format(args.out))


if __name__ == "__main__":
    main()
