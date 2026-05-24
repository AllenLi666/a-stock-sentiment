#!/usr/bin/env python3
"""
Feishu (飞书) Webhook Bot — send report to Feishu group chat.

Usage:
    python3 src/feishu_bot.py <markdown_file>

Requires FEISHU_WEBHOOK_URL in .env or environment variable.
"""

import os
import sys
import json
import urllib.request
import urllib.error

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_webhook_url():
    """Load Feishu webhook URL from .env or environment."""
    url = os.environ.get("FEISHU_WEBHOOK_URL")
    if url:
        return url

    env_path = os.path.join(BASE_DIR, ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("FEISHU_WEBHOOK_URL="):
                    url = line.split("=", 1)[1].strip().strip('"').strip("'")
                    return url

    return None


def read_markdown(path):
    """Read markdown report file, return as plain text."""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def build_card_content(md_text, title="📊 A股产业链早盘报告"):
    """Build Feishu interactive card with markdown content."""

    # Feishu card max content length ~ 2000 chars for text field
    # Split into sections if too long
    lines = md_text.split("\n")

    # Extract key sections
    short_candidates = []
    mid_candidates = []
    in_short = False
    in_mid = False
    in_detail = False
    detail_lines = []

    for line in lines:
        if "短线推荐候选" in line:
            in_short = True
            in_mid = False
            in_detail = False
            continue
        elif "中线推荐候选" in line:
            in_short = False
            in_mid = True
            in_detail = False
            continue
        elif "逐股逻辑" in line:
            in_short = False
            in_mid = False
            in_detail = True
            continue
        elif "风险提示" in line:
            in_detail = False
            continue

        if in_short and "|" in line and "605117" in line or \
           in_short and "|" in line and "代码" not in line and line.strip().startswith("|"):
            short_candidates.append(line.strip())
        if in_mid and "|" in line and "代码" not in line and line.strip().startswith("|"):
            mid_candidates.append(line.strip())
        if in_detail and line.strip():
            detail_lines.append(line.strip())

    # Build Feishu card JSON
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": "blue",
        },
        "elements": [],
    }

    # Short-term candidates table
    short_text = "**🔥 短线候选（持有1-5天）**\n"
    for c in short_candidates[:5]:
        # Parse table row: | rank | code | name | industry | score | ...
        parts = [p.strip() for p in c.split("|") if p.strip()]
        if len(parts) >= 5:
            short_text += "• {} {}（{}）: **{}分**\n".format(
                parts[0], parts[2], parts[3], parts[4]
            )

    card["elements"].append({
        "tag": "div",
        "text": {"tag": "lark_md", "content": short_text.strip()},
    })

    # Mid-term candidates table
    mid_text = "**📈 中线候选（持有1-4周）**\n"
    for c in mid_candidates[:5]:
        parts = [p.strip() for p in c.split("|") if p.strip()]
        if len(parts) >= 5:
            mid_text += "• {} {}（{}）: **{}分**\n".format(
                parts[0], parts[2], parts[3], parts[4]
            )

    card["elements"].append({
        "tag": "div",
        "text": {"tag": "lark_md", "content": mid_text.strip()},
    })

    # Relay signal highlights from detail section
    relay_lines = [l for l in detail_lines if "舆论接力信号" in l]
    if relay_lines:
        relay_text = "**📡 舆论接力信号**\n"
        for rl in relay_lines[:3]:
            relay_text += rl + "\n"
        card["elements"].append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": relay_text.strip()},
        })

    # Footer with link to full report
    card["elements"].append({
        "tag": "note",
        "elements": [
            {"tag": "plain_text", "content": "详细报告请查看本地文件 | 入市有风险，投资需谨慎"}
        ],
    })

    return card


def send_feishu_message(webhook_url, card):
    """Send card message to Feishu webhook."""
    payload = {
        "msg_type": "interactive",
        "card": card,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            if result.get("code") == 0:
                return True, "OK"
            else:
                return False, result.get("msg", "Unknown error")
    except urllib.error.HTTPError as e:
        return False, "HTTP {}: {}".format(e.code, e.reason)
    except urllib.error.URLError as e:
        return False, "Network error: {}".format(e.reason)
    except Exception as e:
        return False, str(e)


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 src/feishu_bot.py <report.md>")
        print("   or: python3 src/feishu_bot.py <report.md> [--title 'Custom Title']")
        sys.exit(1)

    report_path = sys.argv[1]
    title = "📊 A股产业链早盘报告"
    if "--title" in sys.argv:
        idx = sys.argv.index("--title")
        if idx + 1 < len(sys.argv):
            title = sys.argv[idx + 1]

    if not os.path.exists(report_path):
        print("Error: report file not found: {}".format(report_path))
        sys.exit(1)

    webhook_url = load_webhook_url()
    if not webhook_url:
        print("Error: FEISHU_WEBHOOK_URL not found.")
        print("Please add to .env: FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/...")
        sys.exit(1)

    md_text = read_markdown(report_path)
    card = build_card_content(md_text, title)

    success, msg = send_feishu_message(webhook_url, card)
    if success:
        print("✅ Report sent to Feishu successfully!")
    else:
        print("❌ Failed to send: {}".format(msg))
        sys.exit(1)


if __name__ == "__main__":
    main()
