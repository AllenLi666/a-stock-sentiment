import argparse
import csv
import os
from collections import Counter, defaultdict

from analyze import extract_keywords
from entities import find_stocks, load_stock_aliases
from sentiment import score_text


def read_csv(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def to_float(row, key):
    return float(row.get(key) or 0)


def mean(values):
    if not values:
        return 0
    return sum(values) / float(len(values))


def pct_change(new_value, old_value):
    if old_value == 0:
        return 0
    return (new_value - old_value) / old_value * 100.0


def load_market(path):
    """Load market data CSV.
    Returns empty defaultdict if file does not exist (graceful degradation for CI/cloud).
    """
    rows_by_code = defaultdict(list)
    if not os.path.exists(path):
        print("WARNING: Market data file not found: {}".format(path))
        print("         Returning empty dataset. Report will have no market data.")
        return rows_by_code
    for row in read_csv(path):
        item = {
            "date": row["date"],
            "code": row["code"].strip(),
            "name": row["name"].strip(),
            "open": to_float(row, "open"),
            "high": to_float(row, "high"),
            "low": to_float(row, "low"),
            "close": to_float(row, "close"),
            "volume": to_float(row, "volume"),
        }
        rows_by_code[item["code"]].append(item)

    for rows in rows_by_code.values():
        rows.sort(key=lambda item: item["date"])
        attach_indicators(rows)
    return rows_by_code


def attach_indicators(rows):
    closes = []
    highs = []
    lows = []
    volumes = []
    ema12 = None
    ema26 = None
    dea = 0

    for idx, row in enumerate(rows):
        closes.append(row["close"])
        highs.append(row["high"])
        lows.append(row["low"])
        volumes.append(row["volume"])

        row["ma5"] = mean(closes[-5:])
        row["ma20"] = mean(closes[-20:])
        row["ma60"] = mean(closes[-60:])
        row["avg_5d_volume"] = mean(volumes[-5:])
        row["support"] = min(lows[-20:])
        row["resistance"] = max(highs[-20:])
        row["rsi14"] = calc_rsi(closes[-15:])

        close = row["close"]
        ema12 = close if ema12 is None else ema12 * (11.0 / 13.0) + close * (2.0 / 13.0)
        ema26 = close if ema26 is None else ema26 * (25.0 / 27.0) + close * (2.0 / 27.0)
        dif = ema12 - ema26
        dea = dea * 0.8 + dif * 0.2
        row["macd_hist"] = dif - dea

        true_ranges = []
        start = max(0, idx - 13)
        for tr_idx in range(start, idx + 1):
            current = rows[tr_idx]
            prev_close = rows[tr_idx - 1]["close"] if tr_idx > 0 else current["close"]
            true_ranges.append(max(
                current["high"] - current["low"],
                abs(current["high"] - prev_close),
                abs(current["low"] - prev_close),
            ))
        row["atr_pct"] = pct_change(mean(true_ranges), close)
        row["history_days"] = idx + 1


def calc_rsi(closes):
    if len(closes) < 2:
        return 50.0
    gains = []
    losses = []
    for idx in range(1, len(closes)):
        change = closes[idx] - closes[idx - 1]
        gains.append(max(change, 0))
        losses.append(abs(min(change, 0)))
    avg_gain = mean(gains)
    avg_loss = mean(losses)
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def build_daily_social_stats(tweets, stocks):
    daily = defaultdict(dict)
    history_counts = defaultdict(list)

    for date in sorted(set(row["date"] for row in tweets)):
        day_rows = [row for row in tweets if row["date"] == date]
        stats = {}
        for tweet in day_rows:
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

        for item in stats.values():
            code = item["code"]
            last_counts = history_counts[code][-7:]
            item["avg_7d_mentions"] = mean(last_counts)
            item["heat_surge"] = item["mentions"] / item["avg_7d_mentions"] if item["avg_7d_mentions"] > 0 else item["mentions"]
            item["avg_sentiment"] = item["score_sum"] / float(item["mentions"])
            item["unique_authors"] = len(item["authors"])
            item["keywords"] = item["keyword_counter"].most_common(5)
            del item["authors"]

        all_codes = set(history_counts.keys()) | set(stats.keys())
        for code in all_codes:
            history_counts[code].append(stats.get(code, {}).get("mentions", 0))
        daily[date] = stats
    return daily


def technical_verdict(row):
    trend = row["open"] > row["ma5"] and row["ma5"] >= row["ma20"] and row["ma20"] >= row["ma60"]
    momentum = 45 <= row["rsi14"] <= 70 and row["macd_hist"] > 0
    upside = pct_change(row["resistance"], row["open"])
    downside = pct_change(row["open"], row["support"])
    risk = upside >= 3 and downside <= 5 and row["atr_pct"] <= 4

    score = 0
    score += 15 if trend else -10
    score += 12 if momentum else -8
    score += 10 if risk else -6
    if trend and momentum and risk:
        label = "技术确认"
    elif score >= 5:
        label = "技术观察"
    else:
        label = "技术不支持"
    return label, score


def signal_score(social, market_row):
    label, tech_score = technical_verdict(market_row)
    volume_ratio = market_row["volume"] / market_row["avg_5d_volume"] if market_row["avg_5d_volume"] > 0 else 0
    gap_pct = pct_change(market_row["open"], market_row["prev_close"])
    score = 0
    score += min(social["heat_surge"], 4.0) / 4.0 * 30.0
    score += min(max(social["avg_sentiment"], -4.0), 4.0) * 5.0
    score += min(volume_ratio, 2.0) / 2.0 * 15.0
    score += min(social["unique_authors"] / float(max(social["mentions"], 1)), 1.0) * 10.0
    score += 15.0 if -1.0 <= gap_pct <= 4.0 else -10.0
    score += tech_score
    if label == "技术不支持":
        score -= 15.0
    return score, label, volume_ratio, gap_pct


def run_backtest(tweets_path, market_path, aliases_path, out_path, hold_days, min_score, min_history):
    stocks = load_stock_aliases(aliases_path)
    tweets = read_csv(tweets_path)
    market = load_market(market_path)
    daily_social = build_daily_social_stats(tweets, stocks)
    trades = []

    for code, rows in market.items():
        for idx in range(1, len(rows) - hold_days):
            row = rows[idx]
            if row.get("history_days", 0) < min_history:
                continue
            prev = rows[idx - 1]
            row["prev_close"] = prev["close"]
            social = daily_social.get(row["date"], {}).get(code)
            if not social:
                continue
            score, tech_label, volume_ratio, gap_pct = signal_score(social, row)
            if score < min_score:
                continue
            buy = row["open"]
            exit_row = rows[idx + hold_days]
            sell = exit_row["close"]
            ret = pct_change(sell, buy)
            trades.append({
                "date": row["date"],
                "exit_date": exit_row["date"],
                "code": code,
                "name": row["name"],
                "score": score,
                "technical": tech_label,
                "heat_surge": social["heat_surge"],
                "mentions": social["mentions"],
                "authors": social["unique_authors"],
                "volume_ratio": volume_ratio,
                "gap_pct": gap_pct,
                "buy": buy,
                "sell": sell,
                "return_pct": ret,
                "keywords": "、".join([word for word, _ in social["keywords"]]) or "-",
            })

    trades.sort(key=lambda item: (item["date"], -item["score"]))
    write_backtest_report(out_path, trades, hold_days, min_score, min_history)
    return trades


def write_backtest_report(path, trades, hold_days, min_score, min_history):
    lines = []
    lines.append("# A股舆情策略回测报告")
    lines.append("")
    lines.append("- 持有期：{} 个交易日".format(hold_days))
    lines.append("- 最低入选分：{:.1f}".format(min_score))
    lines.append("- 最少历史行情：{} 个交易日".format(min_history))
    lines.append("- 交易次数：{}".format(len(trades)))
    if trades:
        returns = [item["return_pct"] for item in trades]
        wins = [item for item in trades if item["return_pct"] > 0]
        lines.append("- 胜率：{:.2f}%".format(len(wins) / float(len(trades)) * 100.0))
        lines.append("- 平均收益：{:.2f}%".format(mean(returns)))
        lines.append("- 最大单笔收益：{:.2f}%".format(max(returns)))
        lines.append("- 最大单笔回撤：{:.2f}%".format(min(returns)))
    lines.append("")
    lines.append("## 交易明细")
    lines.append("")
    lines.append("| 日期 | 卖出日 | 代码 | 名称 | 分数 | 技术 | 热度放大 | 提及 | 账号 | 量能 | 开盘涨跌 | 买入 | 卖出 | 收益 | 关键词 |")
    lines.append("| --- | --- | --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |")
    for item in trades:
        lines.append("| {date} | {exit_date} | {code} | {name} | {score:.1f} | {technical} | {heat_surge:.2f}x | {mentions} | {authors} | {volume_ratio:.2f}x | {gap_pct:+.2f}% | {buy:.2f} | {sell:.2f} | {return_pct:+.2f}% | {keywords} |".format(**item))
    lines.append("")
    lines.append("## 说明")
    lines.append("")
    lines.append("- 这里的样例 CSV 只用于验证流程。接入真实数据时，用同样字段替换 `data/backtest_tweets.csv` 和 `data/backtest_market.csv`。")
    lines.append("- 为避免未来函数，实际生产中应确保 X 文本采集窗口早于买入开盘时间。")
    lines.append("- 回测结果不代表未来收益，策略上线前需要加入手续费、滑点、涨跌停、停牌和容量约束。")

    directory = os.path.dirname(path)
    if directory and not os.path.exists(directory):
        os.makedirs(directory)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parser = argparse.ArgumentParser(description="A股舆情策略历史回测")
    parser.add_argument("--tweets", default=os.path.join(base_dir, "data", "backtest_tweets.csv"))
    parser.add_argument("--market", default=os.path.join(base_dir, "data", "backtest_market.csv"))
    parser.add_argument("--aliases", default=os.path.join(base_dir, "data", "stock_aliases.csv"))
    parser.add_argument("--out", default=os.path.join(base_dir, "reports", "backtest_report.md"))
    parser.add_argument("--hold-days", type=int, default=1)
    parser.add_argument("--min-score", type=float, default=55.0)
    parser.add_argument("--min-history", type=int, default=60)
    args = parser.parse_args()
    trades = run_backtest(
        args.tweets,
        args.market,
        args.aliases,
        args.out,
        args.hold_days,
        args.min_score,
        args.min_history,
    )
    print("Backtest report written to {}".format(args.out))
    print("Trades: {}".format(len(trades)))


if __name__ == "__main__":
    main()
