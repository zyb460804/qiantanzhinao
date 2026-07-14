"""密码策略校验。"""

from __future__ import annotations

import re


def validate_password(password: str) -> tuple[bool, str]:
    """校验密码强度。

    规则：
      - 至少 8 个字符
      - 至少 1 个大写字母
      - 至少 1 个小写字母
      - 至少 1 个数字
      - 至少 1 个特殊字符

    返回 (is_valid, message)
    """
    if len(password) < 8:
        return False, "密码至少 8 个字符"

    if not re.search(r"[A-Z]", password):
        return False, "密码需包含至少 1 个大写字母"

    if not re.search(r"[a-z]", password):
        return False, "密码需包含至少 1 个小写字母"

    if not re.search(r"\d", password):
        return False, "密码需包含至少 1 个数字"

    if not re.search(r"[!@#$%^&*()_+\-=\[\]{}|;':\",./<>?]", password):
        return False, "密码需包含至少 1 个特殊字符"

    return True, "密码强度合格"


def get_password_rules() -> list[str]:
    """获取密码规则说明。"""
    return [
        "至少 8 个字符",
        "至少 1 个大写字母 (A-Z)",
        "至少 1 个小写字母 (a-z)",
        "至少 1 个数字 (0-9)",
        "至少 1 个特殊字符 (!@#$%^&*...)",
    ]
