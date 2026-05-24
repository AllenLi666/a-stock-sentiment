#!/bin/bash
# ============================================================
# A股产业链每日早盘报告 — 定时生成 & Telegram 推送
# 配 crontab 每天早上 9:00 执行:
#   0 9 * * 1-5 /path/to/scripts/daily_report.sh >> /path/to/logs/daily_report.log 2>&1
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# ── Config ──────────────────────────────────────────────────
REPORT_DIR="$PROJECT_DIR/reports"
LOG_DIR="$PROJECT_DIR/logs"
TODAY=$(date +%Y-%m-%d)
REPORT_FILE="$REPORT_DIR/supply_chain_report_${TODAY}_premarket.md"
# 可选：使用特定推文数据源
TWEETS_FILE="$PROJECT_DIR/data/real_tweets.csv"
# ────────────────────────────────────────────────────────────

mkdir -p "$LOG_DIR" "$REPORT_DIR"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

log "=== A股早盘报告开始 ==="

# Step 1: 抓取投中网最新文章
log "[1] Fetching 投中网 articles..."
cd "$PROJECT_DIR"
python3 src/fetch_chinaventure.py \
    --out "$PROJECT_DIR/data/chinaventure_articles.csv" \
    --max-pages 1 \
    >> "$LOG_DIR/fetch_chinaventure_${TODAY}.log" 2>&1 || true
# fetch 可能因网络失败，用已有数据也能出报告

# Step 2: 生成报告
log "[2] Generating report for $TODAY..."
python3 src/supply_chain_report.py \
    --date "$TODAY" \
    --tweets "$TWEETS_FILE" \
    --exclude-prefixes "688,300" \
    --out "$REPORT_FILE" \
    >> "$LOG_DIR/generate_report_${TODAY}.log" 2>&1

if [ ! -f "$REPORT_FILE" ]; then
    log "ERROR: Report not generated!"
    exit 1
fi

log "Report generated: $REPORT_FILE"

# Step 3: 推送到 Telegram（完整报告文件）
log "[3] Sending to Telegram..."
cd "$PROJECT_DIR"
python3 src/telegram_bot.py "$REPORT_FILE" \
    --title "📊 A股产业链早盘报告 · $TODAY" \
    --full \
    >> "$LOG_DIR/telegram_push_${TODAY}.log" 2>&1

log "=== A股早盘报告完成 ==="
