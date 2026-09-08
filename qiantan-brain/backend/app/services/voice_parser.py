"""
Voice semantic parsing engine.
Converts ASR text → structured business event via keyword matching + regex extraction.

P0 修复（2026-08 语音链路实测，产品核心链路）：
- 汉字数字全量解析：两=2、三、十五、八十、一百零五、一万、半斤=0.5、两斤半=2.5。
  讯飞 ASR 真实输出就是汉字数字，旧引擎只认「X十Y」一种形态，「两斤/十五块」
  全部丢失 → confirm 入账 0 元。
- 金额抽取：数字或汉字数字 + 块/元/块钱/毛/角/分，含「花了80」「15块」口语
  形态；sale/purchase 缺金额时 missing_fields 含 amount，不再静默 0 元入账。
- 多意图：按 又/然后/再/，/； 切分多段，parse_voice_events 返回全部事件；
  单事件文本（含逗号补充说明）行为与旧版完全一致。
- 数量绑定距离最近的商品词，并排除单价短语（X元一斤）内的数量，
  修复「卖了3斤猪肉又进了2斤白菜」的数量错配。
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
        with open(encoding="utf-8") as f:
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

# 汉字数字串（不含「点」，小数单独处理）
_CN_RUN_RE = r"[零一二两三四五六七八九十百千万]+"
# 数字 token：阿拉伯数字（含小数）或汉字数字串
_NUM_TOKEN = r"(?:\d+(?:\.\d+)?|[零一二两三四五六七八九十百千万]+)"
# 数量单位（筐为摊贩常用包装单位，一并支持）
_QTY_UNITS = r"公斤|千克|斤|个|把|箱|袋|件|筐"
# 金额动词（后接裸金额，如「花了80」）
_MONEY_VERBS = r"一共花了|一共花|总共花了|总共花|花了|花掉|付了|收了|收到|给了"

# Filler words to remove
FILLER_WORDS = ["那个", "嗯", "啊", "哦", "呃", "就是", "然后", "这个"]

# 多意图切分连接词（任务5：又|然后|再|；|，）
_SEGMENT_SPLIT_RE = re.compile(r"然后|又|再|[，,；;]")


# Party (customer/supplier) name extraction patterns
_PARTY_PATTERNS = [
    re.compile(r"([一-龥]{2,6})(?:店|饭店|食堂|公司|单位|家)\s*拿[了了]"),
    re.compile(r"给([一-龥]{1,6})(?:结了|结款|付款|还钱|还款|回款|给了)"),
    re.compile(r"([一-龥]{1,6})(?:欠|赊|记账|挂账)"),
    re.compile(r"(?:从|跟|向)([一-龥]{1,6})(?:进|买|采购|拉|批)"),
]


# ---------------------------------------------------------------------------
# 汉字数字 → 数值
# ---------------------------------------------------------------------------


def _cn_to_int(s: str) -> float:
    """中文整数串 → 数值。支持 十五/八十/一百零五/一万/三千二百 等组合。"""
    total = 0.0
    section = 0.0
    num = 0.0
    for ch in s:
        digit = CN_NUM_MAP.get(ch)
        if digit is not None and ch not in ("十", "百", "千", "万"):
            num = float(digit)
        elif ch == "十":
            section += (num or 1.0) * 10
            num = 0.0
        elif ch == "百":
            section += (num or 1.0) * 100
            num = 0.0
        elif ch == "千":
            section += (num or 1.0) * 1000
            num = 0.0
        elif ch == "万":
            section = (section + num) * 10000
            total += section
            section = 0.0
            num = 0.0
    return total + section + num


def _cn_to_number(s: str) -> float | None:
    """中文数字串 → 数值（支持「三点五」小数形态）。无法解析返回 None。"""
    if not s:
        return None
    if "点" in s:
        int_part, _, frac_part = s.partition("点")
        base = _cn_to_int(int_part) if int_part else 0.0
        frac = 0.0
        for i, ch in enumerate(frac_part):
            digit = CN_NUM_MAP.get(ch)
            if digit is None or ch in ("十", "百", "千", "万"):
                return None
            frac += digit / (10 ** (i + 1))
        return base + frac
    return _cn_to_int(s)


def _fmt_num(v: float) -> str:
    """浮点转字符串，去掉多余小数位（0.30000000000000004 → '0.3'）。"""
    r = round(v, 4)
    if r == int(r):
        return str(int(r))
    return f"{r:.4f}".rstrip("0").rstrip(".")


def _token_value(token: str) -> float | None:
    """数字 token（阿拉伯或汉字）→ 数值。"""
    if token.isdigit() or re.fullmatch(r"\d+\.\d+", token):
        return float(token)
    return _cn_to_number(token)


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


# ---------------------------------------------------------------------------
# 归一化：金额先于数量（「一块二一斤」里的「二」是毛，不能被数量规则吃掉）
# ---------------------------------------------------------------------------


def _normalize_money(text: str) -> str:
    """口语金额 → 「X元」形态。

    覆盖：十五块/八十块/一百零五块/两块钱/3块5毛/一块二/三毛钱/五分/
    花了80（裸金额）。规则按特异性从高到低应用。
    """

    # R1: X块Y毛 / X元Y角 —— 显式毛/角后缀
    def _kuai_mao(m: re.Match) -> str:
        a, b = _token_value(m.group(1)), _token_value(m.group(2))
        if a is None or b is None:
            return m.group(0)
        return f"{_fmt_num(a + b * 0.1)}元"

    text = re.sub(rf"({_NUM_TOKEN})\s*[块元]\s*({_NUM_TOKEN})\s*[毛角]", _kuai_mao, text)

    # R2a: X块[汉字单数字] → X.Y 元（一块二 = 1.2；后随单位则不吞，见 R7）
    def _kuai_cn_single(m: re.Match) -> str:
        a = _token_value(m.group(1))
        b = float(CN_NUM_MAP.get(m.group(2), 0))
        return f"{_fmt_num(a + b * 0.1)}元"

    text = re.sub(
        rf"({_NUM_TOKEN})\s*块\s*([一二两三四五六七八九])(?![毛角分]|{_QTY_UNITS})",
        _kuai_cn_single,
        text,
    )

    # R2b: X块[阿拉伯单数字] → 仅当后面不是 数量单位/其他数字 才视作毛
    def _kuai_digit_single(m: re.Match) -> str:
        a = _token_value(m.group(1))
        return f"{_fmt_num(a + int(m.group(2)) * 0.1)}元"

    text = re.sub(
        rf"({_NUM_TOKEN})\s*块\s*(\d)(?![\d毛角分])(?!\s*[一二两三四五六七八九\d]?\s*(?:{_QTY_UNITS}))",
        _kuai_digit_single,
        text,
    )

    # R7: X块[一每1N]单位 → X元（单价短语「十五块一斤」→「15元一斤」）
    def _kuai_per_unit(m: re.Match) -> str:
        a = _token_value(m.group(1))
        return f"{_fmt_num(a)}元" if a is not None else m.group(0)

    text = re.sub(rf"({_NUM_TOKEN})\s*块(?=\s*[一每1\d]\s*(?:{_QTY_UNITS}))", _kuai_per_unit, text)

    # R3: X毛 / X角 → 0.X 元
    def _mao(m: re.Match) -> str:
        a = _token_value(m.group(1))
        return f"{_fmt_num(a * 0.1)}元" if a is not None else m.group(0)

    text = re.sub(rf"({_NUM_TOKEN})\s*[毛角]钱?", _mao, text)

    # R4: X分(钟除外) → 0.0X 元
    def _fen(m: re.Match) -> str:
        a = _token_value(m.group(1))
        return f"{_fmt_num(a * 0.01)}元" if a is not None else m.group(0)

    text = re.sub(rf"({_NUM_TOKEN})\s*分(?!钟)钱?", _fen, text)

    # R5a: X块钱 → X 元（「块钱」必为金额，先行避免「两块豆腐」误伤）
    def _kuai_qian(m: re.Match) -> str:
        a = _token_value(m.group(1))
        return f"{_fmt_num(a)}元" if a is not None else m.group(0)

    text = re.sub(rf"({_NUM_TOKEN})\s*块钱", _kuai_qian, text)

    # R5b: X块（句读边界或后随买/卖/进等动词时才视作金额）
    text = re.sub(
        rf"({_NUM_TOKEN})\s*块(?=\s*(?:[，,。;；！!？?、]|$|[买卖进了收又再来还给花]))",
        _kuai_qian,
        text,
    )

    # R5c: X元（汉字数字串 + 元 → 数字元）
    text = re.sub(rf"({_NUM_TOKEN})\s*元", _kuai_qian, text)

    # R6: 动词 + 裸金额（「花了80」「收了十五」），金额后须是句读边界
    def _verb_bare(m: re.Match) -> str:
        a = _token_value(m.group(2))
        return f"{m.group(1)}{_fmt_num(a)}元" if a is not None else m.group(0)

    text = re.sub(
        rf"({_MONEY_VERBS})\s*({_NUM_TOKEN})(?=\s*(?:[，,。;；！!？?、]|$|[买卖进了收又再来还给花]))",
        _verb_bare,
        text,
    )
    return text


def _normalize_chinese_numbers(text: str) -> str:
    """数量语境的汉字数字 → 阿拉伯数字：半斤=0.5、两斤半=2.5、三斤=3。"""

    # X斤半 → X.5斤（先于通用规则，否则「两斤半」会先变成「2斤半」）
    def _plus_half(m: re.Match) -> str:
        v = _cn_to_number(m.group(1))
        return f"{_fmt_num(v + 0.5)}{m.group(2)}" if v is not None else m.group(0)

    text = re.sub(rf"({_CN_RUN_RE})\s*({_QTY_UNITS})半", _plus_half, text)

    # 半斤 → 0.5斤
    text = re.sub(rf"半\s*({_QTY_UNITS})", r"0.5\1", text)

    # 三斤 / 一万斤 / 三点五斤 → 数字
    def _num_unit(m: re.Match) -> str:
        v = _cn_to_number(m.group(1))
        return f"{_fmt_num(v)}{m.group(2)}" if v is not None else m.group(0)

    text = re.sub(
        rf"({_CN_RUN_RE}(?:点[零一二两三四五六七八九])?)\s*({_QTY_UNITS})", _num_unit, text
    )
    return text


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


# ---------------------------------------------------------------------------
# 字段抽取
# ---------------------------------------------------------------------------


def _extract_product(text: str, product_names: list[str]) -> tuple[str | None, int]:
    """提取商品词。最长匹配优先；返回 (商品名, 位置) 供数量就近绑定。"""
    matches: list[tuple[str, int]] = []
    for name in product_names:
        pos = text.find(name)
        if pos >= 0:
            matches.append((name, pos))
    if not matches:
        # Fuzzy match: try substring（品类名前两字包含）
        for name in product_names:
            if len(name) >= 2:
                pos = text.find(name[:2])
                if pos >= 0:
                    matches.append((name, pos))
                    break
    if not matches:
        return None, -1
    name, pos = max(matches, key=lambda item: len(item[0]))
    return name, pos


def _overlaps(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    return any(s <= start < e or s < end <= e for s, e in spans)


def _extract_unit_price(text: str) -> tuple[float | None, list[tuple[int, int]]]:
    """提取单价（X元一斤 / 一斤X元）。返回 (单价, 全部单价短语跨度)。

    跨度用于把「X元一斤」里的数量/金额从总量候选中剔除。
    """
    pats = [
        rf"(\d+(?:\.\d+)?)\s*[元块]钱?\s*[一每1]\s*(?:{_QTY_UNITS})",
        rf"[一每1]\s*(?:{_QTY_UNITS})\s*(\d+(?:\.\d+)?)\s*[元块]",
    ]
    value: float | None = None
    spans: list[tuple[int, int]] = []
    for pat in pats:
        for m in re.finditer(pat, text):
            spans.append((m.start(), m.end()))
            if value is None:
                value = float(m.group(1))
    return value, spans


def _extract_quantity(
    text: str,
    product_pos: int = -1,
    exclude_spans: list[tuple[int, int]] | None = None,
) -> tuple[float | None, str]:
    """提取数量与单位，绑定距离商品词最近的一次出现（修复数量错配）。

    exclude_spans 内（单价短语「2元1斤」的 1斤）的数量不计。
    """
    exclude_spans = exclude_spans or []
    candidates: list[tuple[float, str, int]] = []
    for m in re.finditer(rf"(\d+(?:\.\d+)?)\s*({_QTY_UNITS})", text):
        if _overlaps(m.start(), m.end(), exclude_spans):
            continue
        unit = m.group(2)
        if unit == "千克":
            unit = "公斤"
        candidates.append((float(m.group(1)), unit, m.start()))
    if not candidates:
        return None, "斤"
    if product_pos >= 0:
        best = min(candidates, key=lambda c: abs(c[2] - product_pos))
        return best[0], best[1]
    return candidates[0][0], candidates[0][1]


def _extract_total_amount(text: str, exclude_spans: list[tuple[int, int]]) -> float | None:
    """提取总金额：单价短语之外的「X元」，取最后一次出现（总量通常后置）。"""
    last: re.Match | None = None
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*元", text):
        if _overlaps(m.start(), m.end(), exclude_spans):
            continue
        last = m
    return float(last.group(1)) if last else None


_TRAILING_VERB_CHARS = "了的花卖买又还给收再去接着赚"


def _extract_raw_product(text: str) -> str | None:
    """品类/SKU 都未命中时，按「数量+单位」锚点抽取用户原词（如 西红柿）。"""
    m = re.search(rf"\d+(?:\.\d+)?\s*(?:{_QTY_UNITS})\s*([一-龥]{{2,4}})", text)
    if not m:
        m = re.search(rf"([一-龥]{{2,4}})\s*\d+(?:\.\d+)?\s*(?:{_QTY_UNITS})", text)
    if not m:
        return None
    word = m.group(1)
    while word and word[-1] in _TRAILING_VERB_CHARS:
        word = word[:-1]
    return word if len(word) >= 2 else None


# ---------------------------------------------------------------------------
# 单事件解析 + 多意图编排
# ---------------------------------------------------------------------------


def _parse_single(text_in: str, product_names: list[str]) -> dict:
    """把一段已切分的文本解析为单个业务事件。"""
    text = text_in.strip()
    text = _remove_fillers(text)
    # 金额归一化必须先于数量：见 _normalize_money docstring
    text = _normalize_money(text)
    text = _normalize_chinese_numbers(text)

    event_type = _detect_event_type(text)
    product, product_pos = _extract_product(text, product_names)
    unit_price, price_spans = _extract_unit_price(text)
    quantity, unit = _extract_quantity(text, product_pos, price_spans)
    total_amount = _extract_total_amount(text, price_spans)
    party_name = _extract_party_name(text)
    is_credit = _detect_credit(text)
    is_repay = _detect_repay(text) and party_name is not None
    raw_product = product if product else _extract_raw_product(text)

    # Deduce missing values
    missing: list[str] = []
    guessed = 0

    if event_type == "unknown":
        event_type = "purchase"  # Assume purchase by default
        guessed += 1

    # Calculate unit_price from total and quantity
    if unit_price is None and total_amount is not None and quantity is not None and quantity > 0:
        unit_price = round(total_amount / quantity, 2)
        guessed += 1

    # Calculate total from quantity and unit_price
    if total_amount is None and unit_price is not None and quantity is not None:
        total_amount = round(quantity * unit_price, 2)

    if not product:
        missing.append("product")
    if quantity is None:
        missing.append("quantity")
    # sale 与 purchase 都必须有金额，缺金额时显式提示，不再静默 0 元入账
    if event_type in ("sale", "purchase") and total_amount is None:
        missing.append("amount")

    # Confidence score
    confidence = max(0.0, min(1.0, 1.0 - 0.1 * len(missing) - 0.05 * guessed))

    return {
        "event_type": event_type,
        "product": product,
        "product_word": raw_product,
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


def parse_voice_events(
    asr_text: str,
    product_names: list[str] | None = None,
) -> list[dict]:
    """解析 ASR 文本为事件列表（多意图支持）。

    按 又/然后/再/，/； 切分；只有当 ≥2 个分句各自含事件关键词（卖了/进了/
    扔了…）时才按多事件处理，否则整句按单事件解析（保持「今天进了白菜50斤，
    三毛钱一斤」这类逗号补充说明的旧行为）。无关键词分句并入前一分句
    （「卖了苹果3斤，15块」→ 卖了苹果3斤15块）。
    """
    if product_names is None:
        product_names = _load_products()
    text = asr_text.strip()

    groups: list[list[str]] = []
    group_has_keyword: list[bool] = []
    for seg in _SEGMENT_SPLIT_RE.split(text):
        if not seg.strip():
            continue
        has_kw = _detect_event_type(_remove_fillers(seg)) != "unknown"
        if not groups:
            groups.append([seg])
            group_has_keyword.append(has_kw)
        elif has_kw and group_has_keyword[-1]:
            groups.append([seg])
            group_has_keyword.append(True)
        elif has_kw:
            groups[-1].append(seg)
            group_has_keyword[-1] = True
        else:
            groups[-1].append(seg)

    if sum(group_has_keyword) >= 2:
        return [_parse_single("".join(g), product_names) for g in groups]
    return [_parse_single(text, product_names)]


def parse_voice_text(
    asr_text: str,
    product_names: list[str] | None = None,
) -> dict:
    """解析 ASR 文本为单个业务事件（兼容入口，多意图时返回第 1 笔）。"""
    return parse_voice_events(asr_text, product_names)[0]
