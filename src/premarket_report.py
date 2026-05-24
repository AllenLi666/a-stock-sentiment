import argparse
import csv
import os
from collections import Counter

from analyze import extract_keywords
from backtest import load_market, mean, pct_change, technical_verdict
from entities import find_stocks, load_stock_aliases
from sentiment import score_text


KEYWORD_NOISE = {
    "https", "http", "co", "com", "t", "AI", "ETF", "Air", "Google", "OpenAI",
    "Anthropic", "SpaceX", "NVIDIA", "DeepSeek", "VOAChinese",
}


def read_csv(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def build_social_stats(tweets, stocks):
    stats = {}
    for tweet in tweets:
        text = tweet.get("text", "")
        sentiment = score_text(text)
        keywords = extract_keywords(text)
        for match in find_stocks(text, stocks):
            code = match["code"]
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
            stats[code]["keyword_counter"].update(keywords)
            author = tweet.get("author", "").strip()
            if author:
                stats[code]["authors"].add(author)

    max_mentions = max([item["mentions"] for item in stats.values()] or [1])
    for item in stats.values():
        item["avg_sentiment"] = item["score_sum"] / float(item["mentions"])
        item["unique_authors"] = len(item["authors"])
        item["heat_score"] = item["mentions"] / float(max_mentions) * 100.0
        item["keywords"] = item["keyword_counter"].most_common(6)
        del item["authors"]
    return stats


def latest_market_rows(market, market_date=None):
    result = {}
    for code, rows in market.items():
        if not rows:
            continue
        eligible = [row for row in rows if not market_date or row["date"] <= market_date]
        if eligible:
            result[code] = eligible[-1]
    return result


def clean_keywords(keywords):
    cleaned = []
    for word, count in keywords:
        if word in KEYWORD_NOISE:
            continue
        if word.startswith("@"):
            continue
        if any(ch.isdigit() for ch in word) and not word.isdigit():
            continue
        if len(word) > 18:
            continue
        cleaned.append((word, count))
    return cleaned


def candidate_score(social, market_row):
    tech_label, tech_score = technical_verdict(market_row)
    volume_ratio = market_row["volume"] / market_row["avg_5d_volume"] if market_row["avg_5d_volume"] > 0 else 0
    score = 0
    score += social["heat_score"] * 0.35
    score += min(max(social["avg_sentiment"], -5.0), 5.0) * 6.0
    score += min(social["unique_authors"] / float(max(social["mentions"], 1)), 1.0) * 10.0
    score += min(volume_ratio, 2.0) / 2.0 * 15.0
    score += tech_score
    if tech_label == "技术不支持":
        score -= 15.0
    return score, tech_label, volume_ratio


def build_candidates(social_stats, market_rows, exclude_prefixes=None):
    exclude_prefixes = exclude_prefixes or []
    candidates = []
    for code, social in social_stats.items():
        if any(code.startswith(prefix) for prefix in exclude_prefixes):
            continue
        if code not in market_rows:
            continue
        row = market_rows[code]
        score, tech_label, volume_ratio = candidate_score(social, row)
        close = row["close"]
        buy_low = close * 0.99
        buy_high = close * 1.035
        target_pct = 3.0 + min(max(social["avg_sentiment"], 0), 4.0) * 0.5
        if tech_label == "技术确认":
            target_pct += 0.8
        elif tech_label == "技术不支持":
            target_pct -= 0.8
        if volume_ratio >= 1.2:
            target_pct += 0.4
        target_pct = max(2.0, min(target_pct, 6.2))
        target = close * (1.0 + target_pct / 100.0)
        stop = close * 0.97
        keywords = "、".join([word for word, _ in clean_keywords(social["keywords"])]) or "-"
        candidates.append({
            "code": code,
            "name": social["name"],
            "industry": social["industry"],
            "score": score,
            "technical": tech_label,
            "mentions": social["mentions"],
            "authors": social["unique_authors"],
            "avg_sentiment": social["avg_sentiment"],
            "volume_ratio": volume_ratio,
            "last_date": row["date"],
            "last_close": close,
            "ma5": row["ma5"],
            "ma20": row["ma20"],
            "rsi14": row["rsi14"],
            "macd_hist": row["macd_hist"],
            "support": row["support"],
            "resistance": row["resistance"],
            "buy_low": buy_low,
            "buy_high": buy_high,
            "target": target,
            "stop": stop,
            "keywords": keywords,
        })
    candidates.sort(key=lambda item: (-item["score"], item["code"]))
    return candidates


def write_report(path, candidates, tweets, report_date):
    dates = sorted(set(row.get("date", "") for row in tweets if row.get("date", "")))
    lines = []
    lines.append("# {} A股舆情交易报告".format(report_date))
    lines.append("")
    lines.append("## 数据口径")
    lines.append("")
    lines.append("- X 数据日期：{}".format("、".join(dates) if dates else "-"))
    lines.append("- X 样本数：{}".format(len(tweets)))
    lines.append("- 行情基准：最近可用 A 股日线收盘数据")
    lines.append("- 说明：报告基于指定日期的 X 舆情和最近可用行情生成，给出后续交易条件和参考区间。")
    lines.append("")
    lines.append("## 早盘候选")
    lines.append("")
    lines.append("| 排名 | 代码 | 名称 | 信号分 | 技术 | X提及 | 账号 | 情绪 | 量能 | 上日收盘 | 买入条件区间 | 目标价 | 止损价 | 关键词 |")
    lines.append("| --- | --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | --- |")
    for index, item in enumerate(candidates[:8], 1):
        lines.append("| {} | {} | {} | {:.1f} | {} | {} | {} | {:.2f} | {:.2f}x | {:.2f} | {:.2f}-{:.2f} | {:.2f} | {:.2f} | {} |".format(
            index,
            item["code"],
            item["name"],
            item["score"],
            item["technical"],
            item["mentions"],
            item["authors"],
            item["avg_sentiment"],
            item["volume_ratio"],
            item["last_close"],
            item["buy_low"],
            item["buy_high"],
            item["target"],
            item["stop"],
            item["keywords"],
        ))
    lines.append("")
    lines.append("## 逐股计划")
    lines.append("")
    for index, item in enumerate(candidates[:5], 1):
        lines.append("{}. {}（{}）".format(index, item["name"], item["code"]))
        lines.append("   - 入选逻辑：X 提及 {} 次，来自 {} 个账号，情绪 {:.2f}；关键词：{}。".format(
            item["mentions"], item["authors"], item["avg_sentiment"], item["keywords"]
        ))
        lines.append("   - 技术验证：{}；MA5 {:.2f}，MA20 {:.2f}，RSI14 {:.1f}，MACD柱 {:.2f}；支撑 {:.2f}，压力 {:.2f}。".format(
            item["technical"], item["ma5"], item["ma20"], item["rsi14"], item["macd_hist"], item["support"], item["resistance"]
        ))
        lines.append("   - 开盘条件：若后续开盘价位于 {:.2f}-{:.2f}，且开盘后 15-30 分钟未跌破基准收盘价，可进入观察/小仓试单；若高开超过区间上沿，不追高。".format(
            item["buy_low"], item["buy_high"]
        ))
        lines.append("   - 价格计划：参考目标价 {:.2f}，风控止损价 {:.2f}。".format(item["target"], item["stop"]))
        lines.append("   - 失效条件：开盘快速跌破 {:.2f}，或 X 新增讨论转负面，或成交量无法延续。".format(item["stop"]))
        lines.append("")
    lines.append("## 风险提示")
    lines.append("")
    lines.append("- 本报告是交易研究预案，不构成投资建议或收益承诺。")
    lines.append("- 实际开盘价、成交量和盘口承接出来后，需要二次确认；高开过多不追。")
    lines.append("- X 热度中包含海外政治、供应链和宏观叙事，可能与 A 股短线价格并不完全同步。")

    directory = os.path.dirname(path)
    if directory and not os.path.exists(directory):
        os.makedirs(directory)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parser = argparse.ArgumentParser(description="Generate A-share premarket plan")
    parser.add_argument("--tweets", default=os.path.join(base_dir, "data", "real_tweets.csv"))
    parser.add_argument("--market", default=os.path.join(base_dir, "data", "real_market_test.csv"))
    parser.add_argument("--aliases", default=os.path.join(base_dir, "data", "stock_aliases.csv"))
    parser.add_argument("--date", default="2026-05-25")
    parser.add_argument("--tweet-date", action="append", default=[])
    parser.add_argument("--market-date", default="")
    parser.add_argument("--exclude-prefix", action="append", default=[])
    parser.add_argument("--out", default=os.path.join(base_dir, "reports", "premarket_2026-05-25.md"))
    args = parser.parse_args()

    stocks = load_stock_aliases(args.aliases)
    tweets = read_csv(args.tweets)
    if args.tweet_date:
        allowed_dates = set(args.tweet_date)
        tweets = [row for row in tweets if row.get("date") in allowed_dates]
    social = build_social_stats(tweets, stocks)
    market = latest_market_rows(load_market(args.market), args.market_date or None)
    candidates = build_candidates(social, market, args.exclude_prefix)
    write_report(args.out, candidates, tweets, args.date)
    print("Premarket report written to {}".format(args.out))


if __name__ == "__main__":
    main()
