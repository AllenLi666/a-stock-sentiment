# A股舆情分析 MVP

这是一个本地原型，用样例社交媒体文本模拟 X/Twitter 数据，生成 A 股舆情雷达日报。

## 功能

- 读取推文 CSV
- 根据股票别名识别 A 股标的
- 统计个股和行业热度
- 计算今日热度相对过去 7 日均值的放大倍数
- 检查来源账号分散度
- 使用规则词典做简单情绪评分
- 提取高频关键词
- 生成 Markdown 日报
- 结合昨收、今开、成交量生成交易候选信号、参考买入价、预期抛售价和风控止损价
- 使用均线、RSI、MACD、支撑压力和 ATR 做技术面双重验证

## 运行

```bash
cd /Users/lichaopeng/a_stock_sentiment
python3 src/analyze.py
```

生成结果：

```text
/Users/lichaopeng/a_stock_sentiment/reports/daily_report.md
```

## 回测

```bash
cd /Users/lichaopeng/a_stock_sentiment
python3 src/backtest.py
```

生成结果：

```text
/Users/lichaopeng/a_stock_sentiment/reports/backtest_report.md
```

回测输入：

```text
data/backtest_tweets.csv
data/backtest_market.csv
```

`backtest_tweets.csv` 字段：

```text
date,tweet_id,author,text
```

`backtest_market.csv` 字段：

```text
date,code,name,open,high,low,close,volume
```

回测器会从日线行情自动计算 MA、RSI、MACD、ATR、支撑压力，并与 X 热度、情绪、来源账号、量能一起评分。

真实数据建议至少包含 60 个交易日。样例数据只有少量日期，只用于检查流程，可用下面命令自检：

```bash
python3 src/backtest.py --min-history 1 --min-score 20
```

## 接入真实数据

拉取 A 股日线行情：

```bash
python3 src/fetch_market_eastmoney.py \
  --start 2025-01-01 \
  --end 2026-05-24 \
  --out data/real_market.csv \
  --insecure
```

`--insecure` 只用于本机 Python 证书链过旧导致 HTTPS 校验失败的情况；如果你的环境证书正常，可以去掉。

把 X/Twitter 导出的 CSV 转成回测格式：

```bash
python3 src/normalize_x_export.py \
  --input /path/to/x_export.csv \
  --out data/real_tweets.csv
```

如果你有 X API Bearer Token，可拉最近 7 天左右的数据：

```bash
X_BEARER_TOKEN=你的token python3 src/fetch_x_recent.py \
  --query '(宁德时代 OR 300750 OR 贵州茅台 OR 600519) lang:zh -is:retweet' \
  --out data/real_tweets.csv
```

用真实数据回测：

```bash
python3 src/backtest.py \
  --tweets data/real_tweets.csv \
  --market data/real_market.csv \
  --min-history 60 \
  --min-score 55 \
  --out reports/backtest_real.md
```

## 数据格式

推文数据位于：

```text
data/sample_tweets.csv
```

股票别名位于：

```text
data/stock_aliases.csv
```

行情数据位于：

```text
data/market_prices.csv
```

字段：

```text
code,name,yesterday_close,today_open,today_volume,avg_5d_volume
```

过去 7 日平均提及量位于：

```text
data/historical_mentions.csv
```

字段：

```text
code,name,avg_7d_mentions
```

技术指标位于：

```text
data/technical_indicators.csv
```

字段：

```text
code,name,ma5,ma20,ma60,rsi14,macd_hist,support,resistance,atr_pct
```

你可以用真实行情源导出的昨收和今开替换这个文件。

## 交易候选逻辑

当前规则：

- X 中股票代码、名称或别名出现次数越多，绝对热度越高
- 今日提及量相对过去 7 日均值放大越明显，热度变化分越高
- 提及来源账号越分散，可信度越高
- 情绪分越正面，候选分越高
- 今日开盘价相对昨日收盘价在 -1% 到 4% 的标的优先
- 开盘涨幅过高会降权，避免追高
- 今日成交量相对 5 日均量放大则加分
- 趋势验证：今开站上 MA5，且 MA5 >= MA20 >= MA60 更优
- 动量验证：RSI 处于 45-70 且 MACD 柱为正更优
- 风险验证：上方压力位空间足够、下方支撑距离可控、ATR 不过高更优
- 参考买入价使用今日开盘价
- 预期抛售价按热度放大、成交量、情绪、技术验证和开盘涨跌幅计算，目标区间约 2.0%-6.5%
- 风控止损价按开盘价下方约 2.0%-3.0% 计算

后续接入真实数据时，建议保持相同字段结构，先替换 CSV，再接 X API 或合规数据供应商。

## 注意

本项目输出只适合作为信息聚合、研究辅助和策略回测起点，不构成投资建议或收益承诺。真实环境需要加入来源权重、垃圾信息过滤、行情数据、公告数据、成交量和回测验证。
