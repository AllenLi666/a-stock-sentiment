# 推特舆论融入接力逻辑 — 评分模型改造计划

## 问题分析

### 当前问题
1. **推特数据不参与评分** — 只在目标价计算中微调，不影响选股排名
2. **高市值=高提及** — 宁德时代/贵州茅台/中芯国际占推特讨论多数，但这些大盘股短线没意思
3. **策略断节** — 用户策略是"行业龙头涨→下游接力涨"，但推特数据没有用来验证这个逻辑

### 用户提供的策略模式

**模式A — 投中网产业链接力：**
```
投中网文章(存储芯片热) → 行业龙头启动(兆易创新涨) → 下游接力(长电科技/雅克科技涨)
```
**模式B — AI推荐的供应链接力：**
```
行业龙头启动(中际旭创/新易盛涨) → AI推荐下游供应商(东山精密低位) → 后期爆发
```

### 核心洞察

| 状态 | 含义 | 评分影响 |
|:---|:---|:---:|
| 热门行业**有**推特讨论 + 下游股**无**讨论 | 主题真实 + 标的未被发掘 = **最佳接力机会** | 加分 |
| 热门行业**无**推特讨论 | 可能只是媒体炒作，真实性存疑 | 中性 |
| 下游股**有**推特讨论 | 已被市场发现，接力空间变小 | 减分 |
| 无推特数据 | 回退到现有评分 | 中性 |

---

## 改造方案

### 新增辅助函数: `build_industry_stock_map()`

从 [`supply_chain.csv`](data/supply_chain.csv) 建立 行业→股票列表 的反向映射，用于计算各行业的推特热度。

```python
def build_industry_stock_map(supply_chain_data):
    """Build reverse mapping: industry → [code1, code2, ...]"""
    ind_map = {}
    for row in supply_chain_data:
        ind = row.get("industry", "")
        code = row.get("code", "")
        if ind and code:
            if ind not in ind_map:
                ind_map[ind] = []
            ind_map[ind].append(code)
    return ind_map
```

### 新增评分维度: 舆论接力信号 (Sentiment Relay Signal)

**替换**现有的 `lagging_bonus`（第7维度），将其升级为结合推特验证的版本。

#### 评分逻辑（用于 short-term 和 mid-term 的通用函数）

```python
def calc_sentiment_relay_score(
    price_stage_score,    # 0-25, 价格在区间底部的位置
    rsi_val,              # RSI14 值
    stock_inds,           # set[str]: 该股票所属行业集合
    industries_found,     # dict[str,int]: 投中网识别出的热门行业
    social,               # dict: 该股票的推特统计
    all_social_stats,     # dict[code, dict]: 全部股票的推特统计
    industry_stock_map,   # dict[industry, [codes]]: 行业→股票映射
):
    """
    舆论接力信号 (0-10)
    
    核心逻辑:
    1. 该股票必须是热门行业的下游受益者 (is_lagging == True)
    2. 价格未过热 (price_stage > 0) + RSI 未超买 (RSI < 60)
    3. 验证热门行业在推特上是否有讨论 (主题真实性确认)
    4. 确认该股票在推特上无人问津 (未被发掘)
    
    组合公式:
    score = price_stage_base × buzz_confirm_factor × vacuum_multiplier
    """
    score = 0
    
    if not industries_found or price_stage_score <= 0:
        return 0
    
    rsi = float(rsi_val)
    if rsi >= 60:
        return 0  # 超买了，不接力
    
    # 检查接力逻辑
    for hot_ind in industries_found:
        beneficiary_inds = LAGGING_BENEFITS.get(hot_ind, [])
        if not any(si in beneficiary_inds for si in stock_inds):
            continue
        
        # ── Step 1: 价格阶段基础分 ──
        if price_stage_score > 15:
            base = 4   # 很深的价格低位
        elif price_stage_score > 10:
            base = 3   # 中等的价格低位
        elif price_stage_score > 0:
            base = 2   # 轻度价格低位
        else:
            base = 0
        
        # ── Step 2: 行业点火验证 (0.5x - 1.5x) ──
        confirm = 1.0  # 无推特数据时中性
        
        if all_social_stats is not None and industry_stock_map is not None:
            # 计算该热门行业所有股票的推特提及总数
            industry_mentions = 0
            hot_stock_codes = industry_stock_map.get(hot_ind, [])
            for code in hot_stock_codes:
                s = all_social_stats.get(code, {})
                industry_mentions += s.get("mentions", 0)
            
            if industry_mentions >= 5:
                confirm = 1.5   # 推特上🔥 热门行业真实成立
            elif industry_mentions >= 2:
                confirm = 1.2   # 有轻度讨论
            elif industry_mentions == 0:
                confirm = 0.8   # 无推特验证，强度降低
                # 但推文数据本身为0时（如5.25报告），不降低
                # 因为可能是没抓到推文而非没讨论
                if _total_tweet_count(all_social_stats) == 0:
                    confirm = 1.0
        
        # ── Step 3: 个股舆论真空 (0.5x - 1.5x) ──
        vacuum = 1.0
        if social:
            mentions = social.get("mentions", 0)
            if mentions == 0:
                vacuum = 1.5   # 无人问津 = 完美接力标的
            elif mentions <= 2:
                vacuum = 1.2   # 轻度讨论 = 还行
            elif mentions <= 5:
                vacuum = 0.8   # 有人发现了 = 减分
            else:
                vacuum = 0.5   # 太火了 = 不接力
        
        score = min(10, base * confirm * vacuum)
        break  # 只使用第一个匹配的热门行业
    
    return round(score, 1)
```

### 评分维度调整

**短线 (Short-term) — 保持总分上限100 + 10 bonus:**

| 维度 | 原分值 | 新分值 | 变化 |
|:---|:---:|:---:|:---|
| 1. 供应链深度 | 0-25 | 0-25 | 不变 |
| 2. 价格阶段 | 0-25 | 0-25 | 不变 |
| 3. 量能觉醒 | 0-15 | 0-15 | 不变 |
| 4. RSI 舒适区 | 0-10 | 0-10 | 不变 |
| 5. MA 支撑 | 0-10 | 0-10 | 不变 |
| 6. 趋势质量 | 0-15 | 0-15 | 不变 |
| 7. **舆论接力信号** | 0-10 (lagging_bonus) | **0-10** | **替换** |
| **总分** | **100 + 10** | **100 + 10** | 不变 |

**中线 (Mid-term) — 同样调整:**

| 维度 | 原分值 | 新分值 | 变化 |
|:---|:---:|:---:|:---|
| 1. 行业顺风 | 0-25 | 0-25 | 不变 |
| 2. 价格空间 | 0-25 | 0-25 | 不变 |
| 3. MA 支撑 | 0-20 | 0-18 | -2 |
| 4. MACD 转向 | 0-15 | 0-15 | 不变 |
| 5. 量能 | 0-15 | 0-12 | -3 |
| 6. **舆论接力信号** | **0-10** | **新增** |
| **总分** | **100 + 10** | **100 + 10** | 不变 |

### 目标价调整 (保留现有逻辑)

[`calc_short_term_prices()`](src/supply_chain_report.py:528) 和 [`calc_mid_term_prices()`](src/supply_chain_report.py:575) 中现有的社交数据用法 **保持不变**：
- 低提及 → target_pct + 1.5%~3.0%（未被发掘的宝石加成）
- 正向情绪 → target_pct + 少量加成

### 报告展示调整

在 [`generate_report()`](src/supply_chain_report.py:624) 的逐股逻辑部分，增加显示：
- 舆论接力信号分
- 热门行业推特热度（如：存储芯片推特提及 12次）
- 个股舆论状态（无人问津/轻度讨论/已过热）

---

## 数据流变化

```
    投中网文章                   推特数据
        │                          │
        ▼                          ▼
   industries_found          social_stats (per stock)
        │                          │
        ▼                          ▼
   ┌─────────────────────────────────────┐
   │   calc_sentiment_relay_score()       │
   │                                      │
   │   1. is_lagging? ← LAGGING_BENEFITS  │
   │   2. price_stage > 0?               │
   │   3. RSI < 60?                       │
   │   4. hot industry Twitter buzz?      │
   │   5. this stock Twitter vacuum?      │
   │                                      │
   │   公式: base × confirm × vacuum      │
   └─────────────────────────────────────┘
                     │
                     ▼
           evaluate_short_term()
           evaluate_mid_term()
```

---

## 执行计划

### 步骤 1: 新增 `build_industry_stock_map()` 函数
- 从 [`supply_chain.csv`](data/supply_chain.csv) 读取行业→股票映射
- 在 [`generate_report()`](src/supply_chain_report.py:624) 中构建，传递给评分函数

### 步骤 2: 新增 `calc_sentiment_relay_score()` 函数
- 实现上述评分逻辑
- 处理无推特数据的fallback（confirm=1.0, vacuum=1.0）

### 步骤 3: 修改 `evaluate_short_term()`
- 接收 `all_social_stats` 和 `industry_stock_map`
- 将现有的 lagging_bonus（第7步）替换为 sentiment_relay_score 调用

### 步骤 4: 修改 `evaluate_mid_term()`
- 同上，替换 lagging_bonus
- 微调 MA支撑从20→18，量能从15→12，腾出5分给舆论维度

### 步骤 5: 修改 `generate_report()`
- 构建 `industry_stock_map`
- 传递给评分函数
- 在报告中显示新维度数据

### 步骤 6: 验证测试
- 用 5.22 数据生成报告，对比新旧结果
- 检查 长电科技/雅克科技/东山精密 等接力逻辑股票是否受益
- 检查 宁德时代/贵州茅台 等高提及股票是否不受影响
