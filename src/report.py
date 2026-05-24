import os


def sentiment_label(avg_score):
    if avg_score > 0.25:
        return "偏正面"
    if avg_score < -0.25:
        return "偏负面"
    return "中性"


def build_trade_section(trade_candidates):
    lines = []
    lines.append("## 交易候选信号")
    lines.append("")
    lines.append("### 选股框架")
    lines.append("1. 先用股票代码、公司名、简称和别名在 X 文本中命中，统计今日提及频次。")
    lines.append("2. 用今日提及频次除以过去 7 日平均提及频次，得到热度放大倍数，优先找突然变热的股票。")
    lines.append("3. 检查来源分散度：同一股票来自多个账号更可靠，单账号集中刷屏会降低可信度。")
    lines.append("4. 再用关键词情绪给每只股票打分，正面催化词加分，债务、风险、刷屏等负面词降权。")
    lines.append("5. 用今日开盘价相对昨日收盘价过滤价格位置：-1% 到 4% 视为更适合跟踪，过度高开降权，明显低开降权。")
    lines.append("6. 用今日成交量相对 5 日均量验证资金配合，量能放大则加分。")
    lines.append("7. 做技术面双重验证：趋势结构看均线和价格位置，动量/风险看 RSI、MACD、支撑压力和 ATR。")
    lines.append("8. 候选分 = 热度变化 + 绝对热度 + 情绪 + 来源分散度 + 开盘位置 + 成交量确认 + 技术验证。")
    lines.append("")
    if trade_candidates:
        lines.append("### 候选列表")
        lines.append("")
        lines.append("| 排名 | 代码 | 名称 | 行业 | 信号分 | 技术结论 | 热度放大 | 来源账号 | 量能倍数 | 昨收 | 今开 | 开盘涨跌 | 参考买入价 | 预期抛售价 | 风控止损价 |")
        lines.append("| --- | --- | --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
        for index, item in enumerate(trade_candidates, 1):
            lines.append("| {} | {} | {} | {} | {:.1f} | {} | {:.2f}x | {} | {:.2f}x | {:.2f} | {:.2f} | {:+.2f}% | {:.2f} | {:.2f} | {:.2f} |".format(
                index,
                item["code"],
                item["name"],
                item["industry"],
                item["signal_score"],
                item["technical_verdict"],
                item["heat_surge"],
                item["unique_authors"],
                item["volume_ratio"],
                item["yesterday_close"],
                item["today_open"],
                item["gap_pct"],
                item["buy_price"],
                item["target_sell_price"],
                item["stop_loss_price"],
            ))
        lines.append("")
        lines.append("### 逐股逻辑")
        lines.append("")
        for index, item in enumerate(trade_candidates, 1):
            lines.append("{}. {}（{}）".format(index, item["name"], item["code"]))
            lines.append("   - 入选原因：{}".format(item["entry_logic"]))
            lines.append("   - 技术验证：趋势：{}；动量/风险：{}；{}".format(
                item["trend_check"],
                item["momentum_check"],
                item["risk_check"],
            ))
            lines.append("   - 价格计划：{}".format(item["price_plan"]))
            lines.append("   - 失效条件：{}".format(item["invalidation"]))
            lines.append("")
    else:
        lines.append("未生成交易候选。请检查推文样本、股票别名和行情数据是否匹配。")
    lines.append("")
    lines.append("价位规则：参考买入价使用今日开盘价；预期抛售价按热度放大、成交量、情绪强度、技术验证和开盘涨跌幅计算，目标区间约 2.0%-6.5%；止损价按开盘价下方约 2.0%-3.0% 计算。")
    lines.append("")
    return lines


def write_trade_report(path, trade_candidates):
    lines = []
    lines.append("# A股交易候选报告")
    lines.append("")
    lines.extend(build_trade_section(trade_candidates or []))
    lines.append("## 风险提示")
    lines.append("")
    lines.append("- 本报告输出的是量化规则候选信号，不是确定性买卖指令或收益承诺。")
    lines.append("- 社交媒体热度必须结合公告、财报、成交量和实际盘口验证。")
    write_lines(path, lines)


def write_report(path, stats, industry_stats, keyword_counts, tweet_count, trade_candidates=None):
    trade_candidates = trade_candidates or []
    lines = []
    lines.append("# A股舆情雷达日报")
    lines.append("")
    lines.append("## 概览")
    lines.append("")
    lines.append("- 样本推文数：{}".format(tweet_count))
    lines.append("- 覆盖个股数：{}".format(len(stats)))
    lines.append("- 说明：本报告是信息聚合与研究辅助，不构成投资建议。")
    lines.append("")

    lines.extend(build_trade_section(trade_candidates))

    lines.append("## 热门个股")
    lines.append("")
    lines.append("| 排名 | 代码 | 名称 | 行业 | 提及次数 | 平均情绪 | 判断 | 高频词 |")
    lines.append("| --- | --- | --- | --- | ---: | ---: | --- | --- |")
    ranked = sorted(stats.values(), key=lambda item: (-item["mentions"], item["code"]))
    for index, item in enumerate(ranked, 1):
        avg = item["score_sum"] / float(item["mentions"])
        words = "、".join([word for word, _ in item["keywords"][:5]]) or "-"
        lines.append("| {} | {} | {} | {} | {} | {:.2f} | {} | {} |".format(
            index,
            item["code"],
            item["name"],
            item["industry"],
            item["mentions"],
            avg,
            sentiment_label(avg),
            words,
        ))
    lines.append("")

    lines.append("## 热门行业")
    lines.append("")
    lines.append("| 行业 | 提及次数 | 平均情绪 | 判断 |")
    lines.append("| --- | ---: | ---: | --- |")
    for industry, item in sorted(industry_stats.items(), key=lambda kv: (-kv[1]["mentions"], kv[0])):
        avg = item["score_sum"] / float(item["mentions"])
        lines.append("| {} | {} | {:.2f} | {} |".format(
            industry,
            item["mentions"],
            avg,
            sentiment_label(avg),
        ))
    lines.append("")

    lines.append("## 全市场高频关键词")
    lines.append("")
    for word, count in keyword_counts[:20]:
        lines.append("- {}：{}".format(word, count))
    lines.append("")

    lines.append("## 风险提示")
    lines.append("")
    lines.append("- 本报告输出的是量化规则候选信号，不是确定性买卖指令或收益承诺。")
    lines.append("- 社交媒体样本容易被刷屏、转发和单一账号影响，需要结合行情、公告、财务数据和成交量交叉验证。")
    lines.append("- 当前情绪模型是规则版，适合 MVP 验证；接入真实数据后应加入垃圾信息过滤和更严格的来源权重。")

    write_lines(path, lines)


def write_lines(path, lines):
    directory = os.path.dirname(path)
    if directory and not os.path.exists(directory):
        os.makedirs(directory)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
