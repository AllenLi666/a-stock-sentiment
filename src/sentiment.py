POSITIVE_WORDS = {
    "高", "强", "偏强", "活跃", "发酵", "不错", "回升", "反弹", "积极", "正面",
    "复苏", "增加", "订单", "出海", "分红", "预期",
}

NEGATIVE_WORDS = {
    "负面", "压力", "风险", "债务", "过剩", "刷屏", "缺少", "震荡", "分歧",
    "价格战", "传闻", "降低",
}


def score_text(text):
    positive = sum(1 for word in POSITIVE_WORDS if word in text)
    negative = sum(1 for word in NEGATIVE_WORDS if word in text)
    score = positive - negative
    if score > 0:
        label = "偏正面"
    elif score < 0:
        label = "偏负面"
    else:
        label = "中性"
    return {
        "score": score,
        "positive_hits": positive,
        "negative_hits": negative,
        "label": label,
    }
