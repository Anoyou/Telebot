"""宿主内置的转账通知解析器（B1 下沉前的旧实现，原样保留）。

0.111 起转账通知解析可由 ``payment_sources`` 扩展点的插件提供（官方插件
``payment-source-cn-transfer`` 是这里正则的逐字迁移）。宿主在双跑对照期内仍以本解析器为准，
插件结果只做对照；运行设置打开「以插件结果为准」后才改用插件结果，插件失败 / 超时 / 未安装时
仍回到这里（fail-open）。本模块保持纯函数、无依赖，golden 测试直接对它取样。
"""

from __future__ import annotations

import re
from typing import Any


def parse_transfer_notice(text: str) -> dict[str, Any] | None:
    """解析官方或测试阶段自定义转账通知文案。"""

    if not text.strip():
        return None
    text = re.sub(r"<[^>]+>", "", text)
    labeled = {
        "payer_name": r"付款人\s*[:：]\s*(.+)",
        "receiver_name": r"收款人\s*[:：]\s*(.+)",
        "amount": r"金额\s*[:：]\s*(\d+)",
    }
    out: dict[str, Any] = {}
    for key, pattern in labeled.items():
        match = re.search(pattern, text)
        if not match:
            break
        value = match.group(1).strip()
        out[key] = int(value) if key == "amount" else value
    if set(out) == {"payer_name", "receiver_name", "amount"}:
        payer_id_match = re.search(r"付款人\s*(?:用户)?(?:ID|id)\s*[:：]\s*(\d+)", text)
        if payer_id_match:
            out["payer_user_id"] = int(payer_id_match.group(1))
        receiver_id_match = re.search(r"收款人\s*(?:ID|id)\s*[:：]\s*(\d+)", text)
        if receiver_id_match:
            out["receiver_user_id"] = int(receiver_id_match.group(1))
        return out

    payer_match = re.search(r"^\s*(.+?)\s*(?:转出|射出|转账|扣减)\s*(\d+)\b", text, re.M)
    receiver_match = re.search(r"^\s*(.+?)\s*(?:收到|接收|收款)\s*(\d+)\b", text, re.M)
    if payer_match and receiver_match:
        payer_amount = int(payer_match.group(2))
        receiver_amount = int(receiver_match.group(2))
        if payer_amount != receiver_amount:
            return None
        parsed = {
            "payer_name": payer_match.group(1).strip(),
            "receiver_name": receiver_match.group(1).strip(),
            "amount": payer_amount,
        }
        payer_id_match = re.search(r"付款人\s*(?:用户)?(?:ID|id)\s*[:：]\s*(\d+)", text)
        if payer_id_match:
            parsed["payer_user_id"] = int(payer_id_match.group(1))
        receiver_id_match = re.search(r"收款人\s*(?:ID|id)\s*[:：]\s*(\d+)", text)
        if receiver_id_match:
            parsed["receiver_user_id"] = int(receiver_id_match.group(1))
        return parsed
    return None


__all__ = ["parse_transfer_notice"]
