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

P0 修复（2026-09 西瓜话术实测）：
- 单价短语在金额归一化阶段就固化成「N元/单位」canonical 形态。此前
  「5毛一斤」先被改写成「0.5元1斤」，单价标记（一/每）消失，引擎把
  0.5 元当成总额，再反推出 0.5/20=0.03 元/斤 的进货价。
- 进价/卖价角色标签：「进价5毛」落 unit_cost、「卖价1元」落 unit_price，
  不再只按事件类型猜槽位；无动词语境时标签本身决定进/销方向。
- 分句归属改为按语义角色分类：价格补充语（卖价1元一斤）并入前一笔，
  剩余分句（剩余5斤）作为后一笔的数量种子，「一共/总共」汇总语并入前一笔，
  修复「卖了15斤，卖价1元一斤」被拆成两笔、商品与价格互相丢失。
- 同一句话内商品延续：后笔未提商品时继承前笔（「买了西瓜20斤…卖了15斤」
  的 15 斤仍是西瓜），避免逐卡都要手补商品。
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

# 费用（经营支出）名词 → Expense 表 category 归口。顺序即优先级，
# 长词在前（「摊位费」先于「摊位」命中，避免归类歧义）。
_EXPENSE_CATEGORY_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("rent", ("摊位费", "房租", "摊位", "柜台")),
    ("utility", ("水电", "水费", "电费", "煤气", "燃气")),
    (
        "fee",
        (
            "过路费",
            "管理费",
            "卫生费",
            "物业费",
            "手续费",
            "运费",
            "车费",
            "油钱",
            "杂费",
            "罚款",
            "保险",
        ),
    ),
]
_ALL_EXPENSE_NOUNS = sorted(
    (kw for _, kws in _EXPENSE_CATEGORY_KEYWORDS for kw in kws), key=len, reverse=True
)
_EXPENSE_NOUN_RE = re.compile("|".join(_ALL_EXPENSE_NOUNS))
# 无费用名词时的兜底：纯支出动词（「今天花了50」「付了30」）。
# 商品 +「花了」（白菜花了10块）不算——那是进货口径，由调用方结合商品词判断。
_EXPENSE_FALLBACK_RE = re.compile("花了|花掉|付了|支出")


def detect_expense_category(text: str) -> str:
    """费用名词 → Expense.category（rent/utility/fee），无命中归 other。"""
    for category, keywords in _EXPENSE_CATEGORY_KEYWORDS:
        if any(kw in text for kw in keywords):
            return category
    return "other"


# QA2-12（B-205）：报损话术词（「白菜报损6.4斤其他」）。报损动词此前不在任何
# 词表——数量+品词兜底成 purchase（0.85 置信），确认即库存虚增错账。语音域
# 不支持报损入账：命中即不产出 purchase/sale 事件，由 voice.py 给出引导
# warning（转经营管理页手动报损）。
_WASTE_REPORT_KEYWORDS = ("报损",)


def detect_unsupported_waste_report(text: str) -> bool:
    """是否为（语音域暂不支持的）报损话术。"""
    if not text:
        return False
    return any(kw in text for kw in _WASTE_REPORT_KEYWORDS)


# 时间词：既作 time_hint 透传（任务4），也作混合句的分句边界（任务3）。
# 长词在前保证「大前天」不被「前天」截断。
_TIME_HINT_RE = re.compile(
    "大前天|前天|昨天|昨儿|今天|明早|今早|早上|早晨|上午|中午|下午|傍晚|晚上|半夜|凌晨"
)

# 混合句二次切分的交易动词（任务2：「西瓜进了20斤卖了15斤赚了50」无标点也要出 2 笔）。
# 「赚了/收入/收成/一共卖/卖了钱」是利润/总结语，不是新交易：切开会把补充说明
# 错拆成新事件（「一共卖了40块」必须整体并入前一笔），故从切分集合剔除。
_SPLIT_EXCLUDED_VERBS = {"赚了", "收入", "收成", "一共卖", "卖了钱"}
_SPLIT_EVENT_RE = re.compile(
    "|".join(
        [*PURCHASE_KEYWORDS, *WASTE_KEYWORDS]
        + [kw for kw in SALE_KEYWORDS if kw not in _SPLIT_EXCLUDED_VERBS]
    )
    + "|卖[了完掉光出]"
)
# 汇总语前缀：动词紧跟其后时不切分（「一共卖了40块」的「卖了」属于汇总语）
_SPLIT_AGGREGATE_PREFIXES = ("一共", "总共", "总计", "合计", "加起来", "共")

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
# 金额动词（后接裸金额，如「花了80」）。卖了/卖出：口语「上午卖了100」的
# 100 就是销售额；「卖了3斤」不受影响（数字后随单位，不满足句读边界前瞻），
# 「赚了」故意不收——那是毛利不是收入，进了金额就成错账。
_MONEY_VERBS = r"一共花了|一共花|总共花了|总共花|花了|花掉|付了|收了|收到|给了|卖了|卖出"
# 单价 canonical 形态：金额归一化把「5毛一斤」统一改写成「0.5元/斤」，
# 单价识别只认这一种形态（汉字数字归一化会把「一斤」变成「1斤」，标记即丢失）
_UNIT_PRICE_RE = re.compile(rf"(\d+(?:\.\d+)?)\s*元\s*/\s*({_QTY_UNITS})")
_CN_DIGIT = r"[一二两三四五六七八九]"

# 价格角色标签：摊主明说「进价/卖价」时必须落到对应字段，不能只按事件类型猜槽位
_COST_PRICE_LABELS = ("进货价", "进价", "买价", "拿货价", "成本价", "本钱")
_SALE_PRICE_LABELS = ("卖出价", "卖价", "售价", "出货价", "零售价")
_NEUTRAL_PRICE_LABELS = ("单价", "价格")
_PRICE_LABEL_ALT = "|".join([*_COST_PRICE_LABELS, *_SALE_PRICE_LABELS, *_NEUTRAL_PRICE_LABELS])
_PRICE_LABEL_RE = re.compile(_PRICE_LABEL_ALT)
# 标签裸金额：「进价5毛」在摊话语里说的是每斤价，不是这一单的总额
_LABELED_AMOUNT_RE = re.compile(rf"({_PRICE_LABEL_ALT})\s*(\d+(?:\.\d+)?)\s*元(?!/)")
# 汇总语（「一共卖了40块」是对前一笔的合计，不另起一笔）。「赚了X」也是对
# 前一笔的补充（毛利说明），并入前一笔而非另立销售事件——否则「卖了15斤，
# 赚了50」会多出一笔空销售。
_AGGREGATE_PREFIX_RE = re.compile(r"^\s*(?:一共|总共|总计|合计|加起来|共|赚了)")
# 剩余语（「剩余5斤」说的是尾货，其数量属于紧随其后的那笔）
_REMAINDER_RE = re.compile(r"剩余|剩下|还剩|余下|剩")
# 完成态交易动词（「卖了/进了/扔了」）。裸「卖」不算：「卖鸡蛋5块钱一斤」是报价，
# 不是独立一笔，得并入后面报数量的那句；「卖完/卖掉/卖光」是完成态，算一笔。
_EXPLICIT_EVENT_RE = re.compile(
    "|".join([*PURCHASE_KEYWORDS, *SALE_KEYWORDS, *WASTE_KEYWORDS]) + "|卖[了完掉光出]"
)
_QTY_RE = re.compile(rf"\d+(?:\.\d+)?\s*(?:{_QTY_UNITS})")

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

    # R2c: X块Y一/每单位 → X.Y元/单位（「3块5一斤」＝3.5 元/斤）。R2b 怕误伤
    # 「1斤2块」不吞阿拉伯数字，单价形态（后面跟着「一斤」）在这里补。
    def _kuai_digit_per_unit(m: re.Match) -> str:
        a = _token_value(m.group(1))
        b = _token_value(m.group(2))
        if a is None or b is None:
            return m.group(0)
        return f"{_fmt_num(a + b * 0.1)}元/{m.group(3)}"

    text = re.sub(
        rf"({_NUM_TOKEN})\s*块\s*(\d|{_CN_DIGIT})\s*[一每]\s*({_QTY_UNITS})",
        _kuai_digit_per_unit,
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

    # R5b: X块（句读边界或后随买/卖/进等动词时才视作金额；「赚」也算——
    # 「卖了100块赚了20」的 100 是销售额，20 是毛利）
    text = re.sub(
        rf"({_NUM_TOKEN})\s*块(?=\s*(?:[，,。;；！!？?、]|$|[买卖进了收又再来还给花赚]))",
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
        rf"({_MONEY_VERBS})\s*({_NUM_TOKEN})(?=\s*(?:[，,。;；！!？?、]|$|[买卖进了收又再来还给花赚]))",
        _verb_bare,
        text,
    )

    # R8: 单价短语 → canonical「N元/单位」。必须在汉字数字归一化之前完成：
    # 「5毛一斤」若先变成「0.5元1斤」，单价标记就没了，会被当成 0.5 元总额。
    # 只认「一/每」引出的短语，「2元3斤」这类「总额+数量」保持原样不误判。
    def _per_unit(m: re.Match) -> str:
        a = _token_value(m.group(1))
        if a is None:
            return m.group(0)
        frac = _token_value(m.group(2)) if m.group(2) else 0.0
        if frac is None:
            return m.group(0)
        return f"{_fmt_num(a + frac * 0.1)}元/{m.group(3)}"

    text = re.sub(
        rf"(\d+(?:\.\d+)?)\s*元\s*(?:({_CN_DIGIT})\s*)?[一每]\s*({_QTY_UNITS})", _per_unit, text
    )
    # 「一斤5毛」「每斤西瓜3块钱」→「5元/斤」「西瓜3元/斤」。中间原词原样保留，
    # 免得「一斤卖了两块钱」被抹掉动词。
    text = re.sub(
        rf"[一每]\s*({_QTY_UNITS})\s*([一-龥]{{0,4}}?)\s*(\d+(?:\.\d+)?)\s*元",
        r"\2\3元/\1",
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
    # 兜底修复：含「卖/出售」但没带「了」（如「卖白菜100斤200块」）此前会落到
    # unknown → 默认进货，把销售额记成进货成本。裸「卖」在摊主话术里是报销售。
    # 注意顺序：WASTE_KEYWORDS（含「不能卖了」）在上面已先命中。
    if "卖" in text or "出售" in text:
        return "sale"
    # 费用名词（摊位费/房租/水电…）：无交易动词时是纯支出，归 expense，
    # 不再落到 unknown → 默认进货把摊位费记成进货成本。
    if _EXPENSE_NOUN_RE.search(text):
        return "expense"
    # 无交易动词时按价格标签定向：「西瓜20斤，进价5毛一斤」是进货。
    # 放在动词判定之后，避免「卖了15斤，进价5毛」被进价标签带偏成进货。
    if any(lb in text for lb in _COST_PRICE_LABELS):
        return "purchase"
    if any(lb in text for lb in _SALE_PRICE_LABELS):
        return "sale"
    return "unknown"


# ---------------------------------------------------------------------------
# 字段抽取
# ---------------------------------------------------------------------------


# QA-19：品名后紧跟这些字时，多为同一词串被延展成另一形态/菜品
# （土豆丝≠土豆、苹果醋≠苹果），视为模糊命中而非精确匹配。
# 只收窄义"形态后缀"，连接词/动词/量词（和/花/了/进/斤…）不算延展，
# 「白菜花了10块」「土豆和白菜」「西瓜进了20斤」仍是精确命中，不产生警告噪声。
_PRODUCT_SUFFIX_CHARS = frozenset(
    "丝片丁条泥汁叶粉末酱醋茶酒饼糕羹脯干核皮籽仁霜丸膏散铺炒烧炖煮蒸拌烩腌卤"
)


def _extract_product(text: str, product_names: list[str]) -> tuple[str | None, int, str | None]:
    """提取商品词。最长匹配优先；返回 (商品名, 位置, 模糊提示) 供数量就近绑定。

    QA-19：旧兜底用「词表名词前两字」在文本任意位置 find 命中即覆写商品
    （confidence 0.95 无警告），「进货F测芦笋」被静默改写成既有 SKU
    「F测-百万菜」。现收紧匹配谓词：
    - 精确命中：文本包含完整候选名，且品名后没有延展成另一形态；
    - 模糊命中（提示非空，调用方对 confidence 打折）：词串延展包含完整名，
      或 2 字前缀兜底时候选名以（剥净动词/时间词后的）完整用户词开头。
      「进货F测芦笋」不再命中「F测-百万菜」。
    """
    exact: tuple[str, int] | None = None
    soft: tuple[str, int] | None = None
    for name in product_names:
        if not name:
            continue
        pos = text.find(name)
        if pos < 0:
            continue
        end = pos + len(name)
        nxt = text[end] if end < len(text) else ""
        if nxt and nxt in _PRODUCT_SUFFIX_CHARS:
            if soft is None or len(name) > len(soft[0]):
                soft = (name, pos)
        else:
            if exact is None or len(name) > len(exact[0]):
                exact = (name, pos)
    if exact is not None:
        return exact[0], exact[1], None
    if soft is not None:
        return soft[0], soft[1], f"自动匹配到相近商品「{soft[0]}」，请核对"
    # 2 字前缀兜底（QA-19）：候选名必须以完整用户词开头才自动匹配
    for name in product_names:
        if len(name) < 2:
            continue
        pos = text.find(name[:2])
        if pos < 0:
            continue
        run = _word_run_at(text, pos)
        user_word = _strip_word_junk(run)[0] if run else ""
        if user_word and name.startswith(user_word):
            return name, pos, f"自动匹配到相近商品「{name}」，请核对"
    return None, -1, None


def _overlaps(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    return any(s <= start < e or s < end <= e for s, e in spans)


def _price_role(text: str, pos: int) -> str | None:
    """价格短语前面的角色标签 → 'cost' / 'sale' / 'neutral' / None。"""
    window = text[max(0, pos - 6) : pos]
    best_label: str | None = None
    best_at = -1
    for label in [*_COST_PRICE_LABELS, *_SALE_PRICE_LABELS, *_NEUTRAL_PRICE_LABELS]:
        at = window.rfind(label)
        if at > best_at:
            best_label, best_at = label, at
    if best_at < 0 or best_label is None:
        return None
    if best_label in _COST_PRICE_LABELS:
        return "cost"
    if best_label in _SALE_PRICE_LABELS:
        return "sale"
    return "neutral"


def _extract_prices(
    text: str,
) -> tuple[float | None, float | None, float | None, list[tuple[int, int]]]:
    """提取进价 / 卖价 / 无标签单价。返回 (unit_cost, unit_price, neutral, spans)。

    spans 为全部价格短语跨度，供数量与总额抽取剔除——「进价0.5元/斤」既不是
    数量也不是总额，此前被当成 0.5 元总额再反推出 0.03 元/斤 的进货价。
    """
    cost: float | None = None
    sale: float | None = None
    neutral: float | None = None
    spans: list[tuple[int, int]] = []

    for m in _UNIT_PRICE_RE.finditer(text):
        spans.append((m.start(), m.end()))
        value = float(m.group(1))
        role = _price_role(text, m.start())
        if role == "cost" and cost is None:
            cost = value
        elif role == "sale" and sale is None:
            sale = value
        elif role in (None, "neutral") and neutral is None:
            neutral = value

    # 标签裸金额（「进价5毛」）在摊话语里同样是每斤价，不是这一单的总额
    for m in _LABELED_AMOUNT_RE.finditer(text):
        if _overlaps(m.start(), m.end(), spans):
            continue
        spans.append((m.start(), m.end()))
        value = float(m.group(2))
        if m.group(1) in _COST_PRICE_LABELS and cost is None:
            cost = value
        elif m.group(1) in _SALE_PRICE_LABELS and sale is None:
            sale = value
        elif neutral is None:
            neutral = value
    return cost, sale, neutral, spans


def _extract_quantity(
    text: str,
    product_pos: int = -1,
    exclude_spans: list[tuple[int, int]] | None = None,
) -> tuple[float | None, str]:
    """提取数量与单位，绑定距离商品词最近的一次出现（修复数量错配）。

    exclude_spans 内（单价短语「0.5元/斤」）的数量不计。
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


# 数量词邻近的修饰词不是商品名（「剩余5斤」曾被抽成商品「剩余」）
_NON_PRODUCT_WORDS = frozenset(
    {
        "剩余",
        "剩下",
        "还剩",
        "余下",
        "一共",
        "总共",
        "总计",
        "合计",
        "按照",
        "根据",
        "全部",
        "大概",
        "差不多",
        "实际",
    }
)


_TRAILING_VERB_CHARS = "了的花卖买又还给收再去接着赚进上拉批"
# QA2-18：动词后的完成态助词（「卖了F测加权菜」剥掉「卖」后残留「了」成
# 「了F测加权菜」）。仅在确有动词被剥离时跟进剥离，避免误伤以这些字
# 开头的原生品名。
_ASPECT_PARTICLES = "了掉完光过"
# 只可能出现在动词侧的首字（不含「花」——「花生」是商品；不含「按/根」，
# 「按照」「根据」已由 _NON_PRODUCT_WORDS 精确拦下）
_LEADING_VERB_CHARS = "卖售扔掉赚赔赊退丢烂"
# 双字动词前缀：「进货葱20斤」曾被整体抽成品名「进货葱」（动词并进品名）。
# 在单字剥离之前先剥双字动词，剥完剩下的单字（葱/姜/蒜）也算干净品名。
# QA-24：补全多字动词整体消费（进了/买进/购进），不残留尾字进入品名。
_LEADING_VERB_PREFIXES = (
    "进货",
    "进来",
    "进了",
    "买进",
    "购进",
    "买了",
    "进的",
    "买的",
    "卖出",
    "卖完",
    "卖掉",
    "上了",
    "拉了",
    "批了",
    "采购",
)

# QA-19/QA-24：词串（中英文与内部连字符，可覆盖「F测-百万菜」这类 SKU 名）。
# 数字不内含——「数量+单位」锚点天然在数字处截断。
_WORD_RUN_PATTERN = r"[一-龥A-Za-z]+(?:-[一-龥A-Za-z]+)*"
_WORD_RUN_RE = re.compile(_WORD_RUN_PATTERN)


def _word_run_at(text: str, pos: int) -> str | None:
    """返回覆盖 pos 的最大连续词串（QA-19 模糊谓词取「用户词」用）。"""
    for m in _WORD_RUN_RE.finditer(text):
        if m.start() <= pos < m.end():
            return m.group(0)
    return None


def _strip_word_junk(word: str) -> tuple[str, bool]:
    """剥掉词串首部时间词/多字动词前缀与首尾单字动词，得到干净品名候选。

    QA-24：「进货赣南橙1斤4块」此前在「数量+单位」锚点抽取时捕获窗只有 4 字，
    只截到「货赣南橙」——动词头「进」在窗外，前缀剥不掉，尾字「货」残留进入
    品名。现在先取完整词串再剥离，多字动词整体消费、不残留尾字。
    返回 (剥净后的词, 是否剥过动词)。
    """
    stripped_verb = False
    changed = True
    while changed and word:
        changed = False
        # 句首时间词不是品名（「今天进了火龙果」→「进了火龙果」→「火龙果」）
        m = _TIME_HINT_RE.match(word)
        if m and m.end() < len(word):
            word = word[m.end() :]
            changed = True
            continue
        for prefix in _LEADING_VERB_PREFIXES:
            if word.startswith(prefix) and len(word) > len(prefix):
                word = word[len(prefix) :]
                stripped_verb = True
                changed = True
                break
    while word and word[0] in _LEADING_VERB_CHARS:
        word = word[1:]
        stripped_verb = True
    # QA2-18：动词剥净后补剥紧随的完成态助词（了/掉/完/光/过）——
    # 「卖了F测加权菜」不再残留「了」打头。
    if stripped_verb:
        while word and word[0] in _ASPECT_PARTICLES:
            word = word[1:]
    while word and word[-1] in _TRAILING_VERB_CHARS:
        word = word[:-1]
    return word, stripped_verb


def _extract_raw_product(text: str) -> str | None:
    """品类/SKU 都未命中时，按「数量+单位」锚点抽取用户原词（如 西红柿）。"""
    m = re.search(rf"\d+(?:\.\d+)?\s*(?:{_QTY_UNITS})\s*({_WORD_RUN_PATTERN})", text)
    if not m:
        m = re.search(rf"({_WORD_RUN_PATTERN})\s*\d+(?:\.\d+)?\s*(?:{_QTY_UNITS})", text)
    if not m:
        return None
    word, stripped_verb = _strip_word_junk(m.group(1))
    # 显式剥掉动词后允许单字品名（葱/姜/蒜），其余仍要求 ≥2 字防噪声
    min_len = 1 if stripped_verb else 2
    if len(word) < min_len or word in _NON_PRODUCT_WORDS:
        return None
    return word


# ---------------------------------------------------------------------------
# 单事件解析 + 多意图编排
# ---------------------------------------------------------------------------


# QA-27：纯「数量+单价」短语（「1斤2块」→ 归一化「1斤2元」）——剥掉数量单位后
# 只剩数字/金额/标点，说明整句既无商品主语也无交易动词（动词字不在集合内，
# 「进了50斤，三毛钱一斤」不会命中），不构成业务事件。
_BARE_PRICE_RESIDUE_RE = re.compile(rf"[{_QTY_UNITS}\s\d.元/，,。、；;！!？?]")


def _is_bare_price_phrase(norm_text: str) -> bool:
    """归一化文本是否为纯「数量+单价」形态（无商品、无动词）。"""
    if _BARE_PRICE_RESIDUE_RE.sub("", norm_text):
        return False
    return bool(_QTY_RE.search(norm_text) and "元" in norm_text)


def _parse_single(text_in: str, product_names: list[str]) -> dict | None:
    """把一段已切分的文本解析为单个业务事件；纯单价短语返回 None（QA-27）。"""
    original = text_in.strip()
    text = _remove_fillers(original)
    # 金额归一化必须先于数量：见 _normalize_money docstring
    text = _normalize_money(text)
    text = _normalize_chinese_numbers(text)

    event_type = _detect_event_type(text)
    # QA-19：第三位为模糊命中提示（非 None 时置信度打折并透传 warning）
    product, product_pos, fuzzy_hint = _extract_product(text, product_names)
    unit_cost, unit_price, neutral_price, price_spans = _extract_prices(text)
    quantity, unit = _extract_quantity(text, product_pos, price_spans)
    # 「赚了50」是毛利不是收入（P0 西瓜话术实测）：利润短语不参与总额抽取，
    # 否则销售笔会被利润金额污染（销售 15 斤记成 50 元）。
    profit_spans = [m.span() for m in re.finditer(r"赚了\s*\d+(?:\.\d+)?\s*元", text)]
    total_amount = _extract_total_amount(text, price_spans + profit_spans)
    party_name = _extract_party_name(text)
    is_credit = _detect_credit(text)
    is_repay = _detect_repay(text) and party_name is not None
    raw_product = product if product else _extract_raw_product(text)
    if fuzzy_hint:
        # QA-19：模糊命中保留用户原词，避免候选名覆写后原词完全丢失
        run = _word_run_at(text, product_pos)
        user_word = _strip_word_junk(run)[0] if run else ""
        if user_word:
            raw_product = user_word

    # 无标签单价按事件类型落槽；带标签（进价/卖价）各归各，互不覆盖
    if unit_cost is None and unit_price is None and neutral_price is not None:
        if event_type == "purchase":
            unit_cost = neutral_price
        else:
            unit_price = neutral_price

    # Deduce missing values
    missing: list[str] = []
    guessed = 0

    if event_type == "unknown":
        # 纯支出兜底：无商品词 + 花了/付了/支出 且非回款语境（「给了老王30」
        # 是往来款）→ expense；有商品词（「白菜花了10块」）仍是进货口径。
        if product is None and not is_repay and _EXPENSE_FALLBACK_RE.search(text):
            event_type = "expense"
        else:
            # QA-27：无商品且纯「数量+单价」形态（独立说「1斤2块」）不产出
            # purchase 事件——confirm 自然拒绝，而非留一张误记进货的卡。
            if product is None and raw_product is None and _is_bare_price_phrase(text):
                return None
            event_type = "purchase"  # Assume purchase by default
            guessed += 1

    # 总额与单价互推：只认本事件类型的那一侧价格（进货单上的卖价不参与成本计算）
    active_price = unit_cost if event_type == "purchase" else unit_price
    if active_price is None and total_amount is not None and quantity is not None and quantity > 0:
        active_price = round(total_amount / quantity, 2)
        guessed += 1
        if event_type == "purchase":
            unit_cost = active_price
        else:
            unit_price = active_price

    if total_amount is None and active_price is not None and quantity is not None:
        total_amount = round(quantity * active_price, 2)

    # expense 事件：只留 total_amount，商品/数量/单价全部置空（费用不碰库存口径）
    expense_category: str | None = None
    if event_type == "expense":
        expense_category = detect_expense_category(original)
        product = None
        raw_product = None
        quantity = None
        unit = None
        unit_cost = None
        unit_price = None
        if total_amount is None:
            missing.append("amount")
    else:
        if not product:
            missing.append("product")
        if quantity is None:
            missing.append("quantity")
        # sale 与 purchase 都必须有金额，缺金额时显式提示，不再静默 0 元入账
        if event_type in ("sale", "purchase") and total_amount is None:
            missing.append("amount")

    # 时间词提示（任务4）：命中什么透传什么（「昨天」），不做日期自动偏移
    hint = _TIME_HINT_RE.search(original)

    # QA-19：模糊（非精确）路径命中的商品，置信度打折并带 warning 提示人工核对；
    # 费用事件与商品无关，不透传商品匹配警告。
    warning = None if event_type == "expense" else fuzzy_hint

    # Confidence score
    confidence = max(0.0, min(1.0, 1.0 - 0.1 * len(missing) - 0.05 * guessed))
    if warning:
        confidence *= 0.6

    return {
        "event_type": event_type,
        "product": product,
        "product_word": raw_product,
        "quantity": quantity,
        "unit": unit,
        "unit_cost": unit_cost if event_type == "purchase" else None,
        "unit_price": unit_price if event_type == "sale" else None,
        "total_cost": total_amount if event_type == "purchase" else None,
        "total_revenue": total_amount if event_type == "sale" else None,
        "total_amount": total_amount,
        "time_hint": hint.group(0) if hint else None,
        "expense_category": expense_category,
        "party_name": party_name,
        "is_credit": is_credit,
        "is_repay": is_repay,
        "confidence": round(confidence, 2),
        "missing_fields": missing,
        "warning": warning,
    }


def _segment_kind(seg: str) -> str:
    """分句语义角色，决定它另起一笔还是并入前一笔。

    event     —— 自带交易动词，另起一笔（「卖了15斤」）
    price     —— 纯价格补充语，并入前一笔（「卖价1元一斤」「进价5毛」）
    aggregate —— 汇总语，并入前一笔（「一共卖了40块」）
    remainder —— 尾货分句，数量属于紧随其后的那笔（「剩余5斤」）
    other     —— 其余（裸金额、补充说明），并入前一笔
    """
    norm = _normalize_chinese_numbers(_normalize_money(_remove_fillers(seg)))
    if _AGGREGATE_PREFIX_RE.match(norm):
        return "aggregate"
    residual = _PRICE_LABEL_RE.sub("", _LABELED_AMOUNT_RE.sub("", _UNIT_PRICE_RE.sub("", norm)))
    if _EXPLICIT_EVENT_RE.search(residual):
        return "event"
    # 剥掉价格短语后既无动词也无数量 → 纯报价（「卖鸡蛋5块钱一斤」），并入相邻那笔
    if residual != norm and not _QTY_RE.search(residual):
        return "price"
    if _detect_event_type(residual) != "unknown":
        return "event"
    if residual != norm:
        return "price"
    if _REMAINDER_RE.search(norm) and _QTY_RE.search(norm):
        return "remainder"
    return "other"


def _split_on_time_words(part: str) -> list[str]:
    """时间词作分句边界（任务3）：「上午卖了100下午卖了200」→ 两段。

    句首的时间词不切（「昨天卖了200块」「今天进了白菜50斤」仍是完整一句）。
    """
    cuts = [m.start() for m in _TIME_HINT_RE.finditer(part) if m.start() > 0]
    if not cuts:
        return [part]
    pieces: list[str] = []
    prev = 0
    for cut in cuts:
        pieces.append(part[prev:cut])
        prev = cut
    pieces.append(part[prev:])
    return pieces


def _split_on_event_verbs(part: str) -> list[str]:
    """段内出现第二个交易动词时在动词前切开（任务2 混合总结句防丢笔）。

    「西瓜进了20斤卖了15斤赚了50」→「西瓜进了20斤」+「卖了15斤赚了50」。
    只在 ≥2 个动词时切，且从第二个动词起切：首个动词（含句首动词）锚定本句，
    它前面的前缀（「今天」「从老王那」）是修饰语，切开只会制造碎片；
    「一共卖了40块」的「卖了」紧跟汇总语，不切。
    """
    starts: list[int] = []
    for m in _SPLIT_EVENT_RE.finditer(part):
        if part[: m.start()].endswith(_SPLIT_AGGREGATE_PREFIXES):
            continue
        starts.append(m.start())
    if len(starts) < 2:
        return [part]
    pieces: list[str] = []
    prev = 0
    for cut in starts[1:]:  # 首个动词是本句锚点，从第二个起切
        pieces.append(part[prev:cut])
        prev = cut
    pieces.append(part[prev:])
    return pieces


def _split_mixed_sentence(part: str) -> list[str]:
    """先按时间词切、再在第二个交易动词前切（顺序保证「今早上了货」不被误切）。"""
    parts: list[str] = []
    for piece in _split_on_time_words(part):
        parts.extend(_split_on_event_verbs(piece))
    return parts


def _group_segments(text: str) -> list[str]:
    """按 又/然后/再/，/；/时间词/第二个交易动词 切分后，依语义角色归并成若干笔原文。"""
    groups: list[list[str]] = []
    seeded: list[bool] = []  # 该组是否已出现交易动词
    for seg in _SEGMENT_SPLIT_RE.split(text):
        for part in _split_mixed_sentence(seg):
            if not part.strip():
                continue
            kind = _segment_kind(part)
            if kind == "event":
                if groups and not seeded[-1]:
                    groups[-1].append(part)  # 并入等待动词的尾货/补充分句
                    seeded[-1] = True
                else:
                    groups.append([part])
                    seeded.append(True)
            elif kind == "remainder":
                if groups and not seeded[-1]:
                    groups[-1].append(part)
                else:
                    groups.append([part])
                    seeded.append(False)
            elif groups:  # price / aggregate / other：并入前一笔
                groups[-1].append(part)
            else:
                groups.append([part])
                seeded.append(False)

    # 收尾：始终没等到交易动词的末组（句尾孤立的「剩余5斤」）并回前一笔
    while len(groups) > 1 and not seeded[-1]:
        groups[-2].extend(groups[-1])
        groups.pop()
        seeded.pop()
    return ["，".join(g) for g in groups]


def _inherit_product(events: list[dict]) -> list[dict]:
    """同一句话内后笔省略商品时继承前笔（「买了西瓜20斤…卖了15斤」仍是西瓜）。"""
    last: tuple[str, str | None] | None = None
    for event in events:
        if event["event_type"] == "expense":
            continue  # 费用与商品无关：不继承前笔商品，也不作为延续锚点
        if event["product"]:
            last = (event["product"], event["product_word"])
        elif last is not None and not event["product_word"]:
            event["product"], event["product_word"] = last
            event["missing_fields"] = [f for f in event["missing_fields"] if f != "product"]
            # 商品由上下文推断：扣掉「缺失」的 0.1，再按一次猜测扣 0.05
            event["confidence"] = round(min(1.0, event["confidence"] + 0.05), 2)
    return events


def parse_voice_events(
    asr_text: str,
    product_names: list[str] | None = None,
) -> list[dict]:
    """解析 ASR 文本为事件列表（多意图支持）。

    按 又/然后/再/，/； 切分后按语义角色归并（见 _segment_kind）：价格补充语、
    汇总语、裸金额并入前一笔，尾货分句为后一笔的数量种子。只有真正的多笔交易
    才会产出多个事件，「今天进了白菜50斤，三毛钱一斤」这类补充说明仍是单事件。
    QA-27：整句是纯「数量+单价」短语时事件列表为空（不成业务事件）。
    """
    if product_names is None:
        product_names = _load_products()
    text = asr_text.strip()
    # QA2-12：报损话术不产出 purchase/sale 事件（此前「白菜报损6.4斤」被
    # 兜底成 0.85 置信的进货）。整句含报损词即整体抑制，防止混合句里夹带
    # 的报损分句被误记；voice.py 会返回引导 warning。
    if detect_unsupported_waste_report(text):
        return []
    groups = _group_segments(text)
    if len(groups) <= 1:
        events = [_parse_single(text, product_names)]
    else:
        events = [_parse_single(g, product_names) for g in groups]
    return _inherit_product([e for e in events if e is not None])


def _empty_event() -> dict:
    """QA-27：无业务事件时的占位结构（字段与正常事件一致，confirm 白名单拒绝 unknown）。"""
    return {
        "event_type": "unknown",
        "product": None,
        "product_word": None,
        "quantity": None,
        "unit": None,
        "unit_cost": None,
        "unit_price": None,
        "total_cost": None,
        "total_revenue": None,
        "total_amount": None,
        "time_hint": None,
        "expense_category": None,
        "party_name": None,
        "is_credit": False,
        "is_repay": False,
        "confidence": 0.0,
        "missing_fields": ["product", "quantity", "amount"],
        "warning": None,
    }


def parse_voice_text(
    asr_text: str,
    product_names: list[str] | None = None,
) -> dict:
    """解析 ASR 文本为单个业务事件（兼容入口，多意图时返回第 1 笔）。

    QA-27：整句为纯「数量+单价」时无业务事件，返回 unknown 占位。
    """
    events = parse_voice_events(asr_text, product_names)
    if not events:
        return _empty_event()
    return events[0]
