"""
Voice semantic parsing engine.
Converts ASR text → structured business event via keyword matching + regex extraction.
"""

import json
import re
from pathlib import Path


# Load product list from rules config
_RULES_DIR = Path(__file__).parent.parent / "rules"


def _load_products() -> list[str]:
    """Load product names from categories config."""
    config_path = _RULES_DIR / "product_categories.json"
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("product_names", [])
    # Fallback default products
    return [
        "白菜",
        "菠菜",
        "生菜",
        "青菜",
        "韭菜",
        "土豆",
        "萝卜",
        "胡萝卜",
        "红薯",
        "洋葱",
        "豆腐",
        "豆皮",
        "豆干",
        "黄瓜",
        "番茄",
        "辣椒",
        "西瓜",
        "苹果",
        "香蕉",
        "橙子",
        "葡萄",
        "猪肉",
        "牛肉",
        "鸡肉",
        "鸡蛋",
        "大米",
        "面粉",
        "食用油",
    ]


# Event type trigger keywords
PURCHASE_KEYWORDS = ["进了", "进来", "买的", "买了", "进货", "上了", "拉了", "批了", "采购"]
SALE_KEYWORDS = ["卖了", "卖出", "一共卖", "卖了钱", "收入", "赚了", "收成"]
WASTE_KEYWORDS = ["坏了", "扔了", "烂了", "掉了", "损耗", "报废", "不能卖了"]

# Credit / debt keywords
CREDIT_KEYWORDS = ["记账", "赊账", "欠账", "月结", "先记着", "先记", "挂账", "赊着"]
REPAY_KEYWORDS = ["结了", "结款", "付款", "还钱", "还款", "回款", "付清", "还清", "给了"]

# Number-to-word mapping for spoken Chinese numbers
CN_NUM_MAP = {
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
    "百": 100,
    "千": 1000,
    "万": 10000,
}

# Filler words to remove
FILLER_WORDS = ["那个", "嗯", "啊", "哦", "呃", "就是", "然后", "这个"]


# Party (customer/supplier) name extraction patterns
_PARTY_PATTERNS = [
    re.compile(r"([\u4e00-\u9fa5]{2,6})(?:店|饭店|食堂|公司|单位|家)\s*拿[了了]"),
    re.compile(r"给([\u4e00-\u9fa5]{1,6})(?:结了|结款|付款|还钱|还款|回款|给了)"),
    re.compile(r"([\u4e00-\u9fa5]{1,6})(?:欠|赊|记账|挂账)"),
    re.compile(r"(?:从|跟|向)([\u4e00-\u9fa5]{1,6})(?:进|买|采购|拉|批)"),
]


def _extract_party_name(text: str) -> str | None:
    """Extract counterparty name from voice text (e.g., 张记饭店, 老王)."""
    for pat in _PARTY_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(1)
    return None


def _detect_credit(text: str) -> bool:
    """Detect whether the text implies credit/debt rather than cash settlement."""
    for kw in CREDIT_KEYWORDS:
        if kw in text:
            return True
    return False


def _detect_repay(text: str) -> bool:
    """Detect whether the text implies a repayment/payment to a party."""
    for kw in REPAY_KEYWORDS:
        if kw in text:
            return True
    return False


def _normalize_chinese_numbers(text: str) -> str:
    """Convert spoken Chinese numbers to digits. e.g. '五十斤' → '50斤'."""
    # Pattern: "X十Y斤" → X*10 + Y
    text = re.sub(r"([一二三四五六七八九])十([一二三四五六七八九])", _cn_tens_ones, text)
    # Pattern: "X十斤" → X*10
    text = re.sub(r"([一二三四五六七八九])十", _cn_tens, text)
    # Pattern: "十Y斤" → 10+Y
    text = re.sub(r"十([一二三四五六七八九])", _cn_ten_plus, text)
    # Pattern: standalone "十斤"
    text = re.sub(r"十", "10", text)
    return text


def _cn_tens_ones(m: re.Match) -> str:
    tens = CN_NUM_MAP.get(m.group(1), 0)
    ones = CN_NUM_MAP.get(m.group(2), 0)
    return str(tens * 10 + ones)


def _cn_tens(m: re.Match) -> str:
    return str(CN_NUM_MAP.get(m.group(1), 0) * 10)


def _cn_ten_plus(m: re.Match) -> str:
    return str(10 + CN_NUM_MAP.get(m.group(1), 0))


def _normalize_money(text: str) -> str:
    """Convert spoken money to digits. e.g. '三毛钱' → '0.3元'."""
    # "X毛钱" / "X角钱" → 0.X 元
    text = re.sub(r"([一二三四五六七八九])毛钱?", _cn_money_mao, text)
    text = re.sub(r"([一二三四五六七八九])角钱?", _cn_money_mao, text)
    # "X分钱" → 0.0X 元
    text = re.sub(r"([一二三四五六七八九\d]+)分钱?", _cn_money_fen, text)
    # "X块X毛" → X.X 元
    text = re.sub(
        r"([\d一二三四五六七八九两]+)块([\d一二三四五六七八九两]+)毛?",
        _cn_money_kuai_mao,
        text,
    )
    # "X块" → X 元
    text = re.sub(r"([\d一二三四五六七八九两]+)块钱?", _cn_money_kuai, text)
    # "X块钱" → X 元
    text = re.sub(r"([\d]+)块钱", r"\1元", text)
    # "X元X角" → X.X 元
    text = re.sub(r"(\d+)元(\d)角", r"\1.\2元", text)
    return text


def _cn_money_mao(m: re.Match) -> str:
    val = CN_NUM_MAP.get(m.group(1), 0)
    return f"{val * 0.1}元"


def _cn_money_fen(m: re.Match) -> str:
    val = CN_NUM_MAP.get(m.group(1), 0) if m.group(1) in CN_NUM_MAP else int(m.group(1))
    return f"{val * 0.01}元"


def _cn_money_kuai(m: re.Match) -> str:
    raw = m.group(1)
    val = CN_NUM_MAP.get(raw, 0) if raw in CN_NUM_MAP else int(raw) if raw.isdigit() else 0
    return f"{val}元"


def _cn_money_kuai_mao(m: re.Match) -> str:
    kuai_raw, mao_raw = m.group(1), m.group(2)
    kuai = (
        CN_NUM_MAP.get(kuai_raw, 0)
        if kuai_raw in CN_NUM_MAP
        else int(kuai_raw)
        if kuai_raw.isdigit()
        else 0
    )
    mao = (
        CN_NUM_MAP.get(mao_raw, 0)
        if mao_raw in CN_NUM_MAP
        else int(mao_raw)
        if mao_raw.isdigit()
        else 0
    )
    return f"{kuai + mao * 0.1}元"


def _remove_fillers(text: str) -> str:
    """Remove filler words."""
    for word in FILLER_WORDS:
        text = text.replace(word, "")
    return text


def _detect_event_type(text: str) -> str:
    """Detect event type from keywords."""
    for kw in PURCHASE_KEYWORDS:
        if kw in text:
            return "purchase"
    for kw in WASTE_KEYWORDS:
        if kw in text:
            return "waste"
    for kw in SALE_KEYWORDS:
        if kw in text:
            return "sale"
    return "unknown"


def _extract_product(text: str, product_names: list[str]) -> str | None:
    """Extract product name from text. Longest match first to avoid partial matches."""
    matches = []
    for name in product_names:
        if name in text:
            matches.append(name)
    if not matches:
        # Fuzzy match: try substring
        for name in product_names:
            if len(name) >= 2 and name[:2] in text:
                matches.append(name)
                break
    return max(matches, key=len) if matches else None


def _extract_quantity(text: str) -> tuple[float | None, str]:
    """Extract quantity and unit. Returns (number, unit)."""
    patterns = [
        (r"(\d+(?:\.\d+)?)\s*(斤)", "斤"),
        (r"(\d+(?:\.\d+)?)\s*(公斤)", "公斤"),
        (r"(\d+(?:\.\d+)?)\s*(千克)", "公斤"),
        (r"(\d+(?:\.\d+)?)\s*(个)", "个"),
        (r"(\d+(?:\.\d+)?)\s*(把)", "把"),
        (r"(\d+(?:\.\d+)?)\s*(箱)", "箱"),
        (r"(\d+(?:\.\d+)?)\s*(袋)", "袋"),
        (r"(\d+(?:\.\d+)?)\s*(件)", "件"),
    ]
    for pat, unit in patterns:
        m = re.search(pat, text)
        if m:
            return float(m.group(1)), unit
    return None, "斤"


def _extract_unit_price(text: str) -> float | None:
    """Extract unit price from text."""
    # Pattern: "X元一斤" / "X块钱一斤" / "一 斤 X 毛钱"
    pats = [
        r"(\d+(?:\.\d+)?)\s*元[一每]\s*(?:斤|个|把)",
        r"(\d+(?:\.\d+)?)\s*[块元]钱?[一每]\s*(?:斤|个|把)",
        r"[一每]\s*(?:斤|个|把)\s*(\d+(?:\.\d+)?)\s*[元块]",
    ]
    for pat in pats:
        m = re.search(pat, text)
        if m:
            return float(m.group(1))
    return None


def _extract_total_amount(text: str) -> float | None:
    """Extract total amount."""
    pats = [
        r"(?:一共|总计|花了|总价|一共花)\s*(\d+(?:\.\d+)?)\s*[元块]",
        r"(\d+(?:\.\d+)?)\s*[元块]\s*(?:一共|总计|总)",
    ]
    for pat in pats:
        m = re.search(pat, text)
        if m:
            return float(m.group(1))
    return None


def parse_voice_text(
    asr_text: str,
    product_names: list[str] | None = None,
) -> dict:
    """
    Parse ASR text into structured business event.

    Args:
        asr_text: Raw transcribed text from ASR.
        product_names: Optional product name list. If None, loads from config.

    Returns:
        Dict with parsed event fields.
    """
    if product_names is None:
        product_names = _load_products()

    text = asr_text.strip()
    text = _remove_fillers(text)
    text = _normalize_chinese_numbers(text)
    text = _normalize_money(text)

    event_type = _detect_event_type(text)
    product = _extract_product(text, product_names)
    quantity, unit = _extract_quantity(text)
    unit_price = _extract_unit_price(text)
    total_amount = _extract_total_amount(text)
    party_name = _extract_party_name(text)
    is_credit = _detect_credit(text)
    is_repay = _detect_repay(text) and party_name is not None

    # Deduce missing values
    missing = []
    guessed = 0

    if event_type == "unknown":
        event_type = "purchase"  # Assume purchase by default
        guessed += 1

    if not product:
        missing.append("product")
    if quantity is None:
        missing.append("quantity")

    # Calculate unit_price from total and quantity
    if unit_price is None and total_amount is not None and quantity is not None and quantity > 0:
        unit_price = round(total_amount / quantity, 2)
        guessed += 1

    # Calculate total from quantity and unit_price
    if total_amount is None and unit_price is not None and quantity is not None:
        total_amount = round(quantity * unit_price, 2)

    # Confidence score
    confidence = max(0.0, min(1.0, 1.0 - 0.1 * len(missing) - 0.05 * guessed))

    result = {
        "event_type": event_type,
        "product": product,
        "quantity": quantity,
        "unit": unit,
        "unit_cost": unit_price if event_type == "purchase" else None,
        "unit_price": unit_price if event_type == "sale" else None,
        "total_cost": total_amount if event_type == "purchase" else None,
        "total_revenue": total_amount if event_type == "sale" else None,
        "total_amount": total_amount,
        "party_name": party_name,
        "is_credit": is_credit,
        "is_repay": is_repay,
        "confidence": round(confidence, 2),
        "missing_fields": missing,
    }

    return result
