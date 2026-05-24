#!/bin/bash
# ============================================================================
# A股产业链每日早盘报告 — GitHub Actions 云端适配版（多源数据）
#
# Supports multiple data sources: 投中网, 财联社, 东方财富行业板块, AASTOCKS
#
# 用法:
#   export X_BEARER_TOKEN="your_token"
#   export TELEGRAM_BOT_TOKEN="your_token"
#   export TELEGRAM_CHAT_ID="chat_id1,chat_id2"
#   ./scripts/daily_report_cloud.sh
# ============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TODAY=$(date '+%Y-%m-%d')

# ── 路径配置 ────────────────────────────────────────────────
REPORT_DIR="$PROJECT_DIR/reports"
REPORT_FILE="$REPORT_DIR/supply_chain_report_${TODAY}.md"
ARTICLES_FILE="$PROJECT_DIR/data/chinaventure_articles.csv"
CLS_FILE="$PROJECT_DIR/data/cls_articles.csv"
EASTMONEY_FILE="$PROJECT_DIR/data/eastmoney_industry.csv"
AASTOCKS_FILE="$PROJECT_DIR/data/aastocks_news.csv"
TWEETS_FILE="$PROJECT_DIR/data/real_tweets.csv"
MARKET_FILE="$PROJECT_DIR/data/real_market_full.csv"

mkdir -p "$REPORT_DIR"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

log "=== A股早盘报告开始 (多源版) ==="
log "Date: $TODAY"

# Step 1: 抓取投中网文章
log "[1/7] Fetching 投中网 articles..."
python3 "$PROJECT_DIR/src/fetch_chinaventure.py" \
    --date "$TODAY" \
    --out "$ARTICLES_FILE" \
    --max-pages 1 || log "WARNING: chinaventure fetch failed (non-fatal)"

# Step 2: 抓取财联社电报
log "[2/7] Fetching 财联社 telegraph articles..."
python3 "$PROJECT_DIR/src/fetch_cls_articles.py" \
    --date "$TODAY" \
    --out "$CLS_FILE" \
    --max-items 30 || log "WARNING: cls fetch failed (non-fatal)"

# Step 3: 抓取东方财富行业板块
log "[3/7] Fetching East Money industry sectors..."
python3 "$PROJECT_DIR/src/fetch_eastmoney_industry.py" \
    --date "$TODAY" \
    --out "$EASTMONEY_FILE" \
    --insecure || log "WARNING: eastmoney fetch failed (non-fatal)"

# Step 4: 抓取 AASTOCKS 新闻
log "[4/7] Fetching AASTOCKS A-share news..."
python3 "$PROJECT_DIR/src/fetch_aastocks_news.py" \
    --date "$TODAY" \
    --out "$AASTOCKS_FILE" \
    --max-items 30 || log "WARNING: aastocks fetch failed (non-fatal)"

# Step 5: 抓取 X 推文
log "[5/7] Fetching X tweets..."
python3 "$PROJECT_DIR/src/fetch_x_recent.py" \
    --query "(A股 OR 股市) lang:zh -is:retweet" \
    --out "$TWEETS_FILE" \
    --max-results 100 || log "WARNING: X fetch failed (non-fatal)"

# Step 6: 抓取市场行情
log "[6/7] Fetching market data..."
START=$(date -d '-6 months' '+%Y-%m-%d')
python3 "$PROJECT_DIR/src/fetch_market_sina.py" \
    --start "$START" \
    --end "$TODAY" \
    --out "$MARKET_FILE" \
    --datalen 90 \
    --sleep 0.2 || log "WARNING: market data fetch failed (non-fatal)"

# Step 7: 生成报告（多源合并）
log "[7/7] Generating report (multi-source)..."
python3 "$PROJECT_DIR/src/supply_chain_report.py" \
    --date "$TODAY" \
    --articles "$ARTICLES_FILE" \
    --cls-articles "$CLS_FILE" \
    --eastmoney-articles "$EASTMONEY_FILE" \
    --aastocks-articles "$AASTOCKS_FILE" \
    --tweets "$TWEETS_FILE" \
    --exclude-prefixes "688,300" \
    --out "$REPORT_FILE"

if [ ! -f "$REPORT_FILE" ]; then
    log "ERROR: Report not generated!"
    exit 1
fi
log "Report generated: $REPORT_FILE"

# Send to Telegram
log "Sending to Telegram..."
python3 "$PROJECT_DIR/src/telegram_bot.py" "$REPORT_FILE" \
    --title "📊 A股产业链早盘报告 · $TODAY" \
    --full

log "=== A股早盘报告完成 ==="
