"""CSV 导出工具 — 通用数据导出（含公式注入防护）。"""

from __future__ import annotations

import csv
import io
from typing import Any


# 公式注入危险前缀（Excel/WPS 会解释这些开头的单元格为公式）
_DANGEROUS_PREFIXES = ("=", "+", "-", "@")


def _sanitize_cell(value: Any) -> str:
    """对单元格值进行公式注入防护。

    以 =, +, -, @ 开头的单元格在 Excel/WPS 中可能被解释为公式。
    在前导加单引号阻止公式解释，防止 CSV 注入攻击。
    """
    s = str(value) if value is not None else ""
    if s and s[0] in _DANGEROUS_PREFIXES:
        return "'" + s
    return s


def export_csv(rows: list[dict[str, Any]], filename: str = "export") -> str:
    """将字典列表导出为 CSV 字符串。

    Args:
        rows: 数据行列表，每行为 dict
        filename: 文件名（不含扩展名），用于提示

    Returns:
        CSV 格式字符串，UTF-8 BOM 确保 Excel 正确识别中文
    """
    if not rows:
        return ""

    output = io.StringIO()
    # UTF-8 BOM for Excel compatibility
    output.write("\ufeff")

    fieldnames = list(rows[0].keys())
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()

    # 逐行写入并对每个单元格做公式注入防护
    for row in rows:
        sanitized = {k: _sanitize_cell(v) for k, v in row.items()}
        writer.writerow(sanitized)

    return output.getvalue()


def export_csv_response(
    rows: list[dict[str, Any]],
    filename: str = "export",
) -> Any:
    """生成 FastAPI 可返回的 CSV StreamingResponse。

    用法:
        from fastapi.responses import StreamingResponse
        return export_csv_response(rows, "tenants")
    """
    from fastapi.responses import StreamingResponse

    csv_content = export_csv(rows, filename)
    return StreamingResponse(
        iter([csv_content]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename={filename}.csv",
        },
    )
