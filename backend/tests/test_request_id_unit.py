"""_extract_request_id 单元测试（pytest 风格）

测试覆盖：
- 无任何 header → UUID
- X-Request-ID 合法 / 小写 / X-Correlation-ID / X-Trace-ID
- 列表遍历顺序
- 换行符 / SQL 注入 / ANSI 转义 / 反斜杠注入
- 中文 / 超长 200 字符 / 空字符串 / 仅空白
- 非法 header 名
- trim 前后空白
- 合法符号
- 128 / 129 字符边界
- 数字开头
"""

from __future__ import annotations

import re

from app.api.middleware.request_id import _extract_request_id, _VALID_REQUEST_ID


def _make_headers(pairs: list[tuple[str, str]]) -> list[tuple[bytes, bytes]]:
    """构造 ASGI 原始 headers 格式"""
    return [(n.encode(), v.encode()) for n, v in pairs]


# ==================== T1: 无任何 header → UUID ====================

def test_no_header_generates_uuid() -> None:
    """T1: 无任何 header → UUID"""
    rid = _extract_request_id([])
    assert re.match(r"^[0-9a-f]{8}-", rid)


# ==================== T2: X-Request-ID 合法 ====================

def test_x_request_id_valid() -> None:
    """T2: X-Request-ID 合法 → 透传"""
    rid = _extract_request_id(_make_headers([("X-Request-ID", "abc-123_xyz.v2")]))
    assert rid == "abc-123_xyz.v2"


# ==================== T3: 小写 header ====================

def test_lowercase_header() -> None:
    """T3: 小写 header 名 → 透传"""
    rid = _extract_request_id(_make_headers([("x-request-id", "lowercase-test")]))
    assert rid == "lowercase-test"


# ==================== T4: X-Correlation-ID ====================

def test_x_correlation_id() -> None:
    """T4: X-Correlation-ID → 透传"""
    rid = _extract_request_id(_make_headers([("X-Correlation-ID", "corr-001")]))
    assert rid == "corr-001"


# ==================== T5: X-Trace-ID ====================

def test_x_trace_id() -> None:
    """T5: X-Trace-ID → 透传"""
    rid = _extract_request_id(_make_headers([("X-Trace-ID", "trace-001")]))
    assert rid == "trace-001"


# ==================== T6: 列表遍历顺序 ====================

def test_header_traversal_order() -> None:
    """T6: 多个候选 header 存在时按列表顺序取第一个"""
    rid = _extract_request_id(_make_headers([
        ("X-Correlation-ID", "first"),
        ("X-Request-ID", "second"),
    ]))
    assert rid == "first"


# ==================== T7: 换行符注入 ====================

def test_newline_injection_rejected() -> None:
    """T7: 换行符注入 → 回退 UUID"""
    rid = _extract_request_id(_make_headers([("X-Request-ID", "evil\nINJECT")]))
    assert "\n" not in rid
    assert re.match(r"^[0-9a-f]{8}-", rid)


# ==================== T8: SQL 注入 ====================

def test_sql_injection_rejected() -> None:
    """T8: SQL 注入 → 回退 UUID"""
    rid = _extract_request_id(_make_headers([("X-Request-ID", "' OR 1=1 --")]))
    # 空白和 ' 都不在合法字符集，会回退
    assert "OR" not in rid or re.match(r"^[0-9a-f]{8}-", rid)
    # 由于原值含空格和单引号，会被拒绝并回退 UUID
    assert re.match(r"^[0-9a-f]{8}-", rid)


# ==================== T9: 中文 ====================

def test_chinese_rejected() -> None:
    """T9: 中文 → 回退 UUID"""
    rid = _extract_request_id(_make_headers([("X-Request-ID", "你好")]))
    assert re.match(r"^[0-9a-f]{8}-", rid)


# ==================== T10: 超长 200 字符 ====================

def test_length_200_rejected() -> None:
    """T10: 长度 200 超过 128 上限 → 回退 UUID"""
    rid = _extract_request_id(_make_headers([("X-Request-ID", "a" * 200)]))
    assert len(rid) <= 36


# ==================== T11: 空字符串 ====================

def test_empty_string_rejected() -> None:
    """T11: 空字符串 → 回退 UUID"""
    rid = _extract_request_id(_make_headers([("X-Request-ID", "")]))
    assert re.match(r"^[0-9a-f]{8}-", rid)


# ==================== T12: 仅空白 ====================

def test_whitespace_only_rejected() -> None:
    """T12: 仅空白 → 回退 UUID"""
    rid = _extract_request_id(_make_headers([("X-Request-ID", "   ")]))
    assert re.match(r"^[0-9a-f]{8}-", rid)


# ==================== T13: 非法 header 名 ====================

def test_invalid_header_name_rejected() -> None:
    """T13: 非法 header 名 → 回退 UUID"""
    rid = _extract_request_id(_make_headers([("X-Other", "abc")]))
    assert re.match(r"^[0-9a-f]{8}-", rid)


# ==================== T14: trim 前后空白 ====================

def test_trim_whitespace() -> None:
    """T14: 前后空白被 strip → 透传"""
    rid = _extract_request_id(_make_headers([("X-Request-ID", "  trim-me  ")]))
    assert rid == "trim-me"


# ==================== T15: 合法符号 ====================

def test_valid_symbols() -> None:
    """T15: 所有合法符号 (字母/数字/_/-/.) → 透传"""
    rid = _extract_request_id(_make_headers([("X-Request-ID", "a_b-c.d")]))
    assert rid == "a_b-c.d"


# ==================== T16: 边界 128 字符 ====================

def test_boundary_128_accepted() -> None:
    """T16: 长度恰好 128 → 透传"""
    rid = _extract_request_id(_make_headers([("X-Request-ID", "a" * 128)]))
    assert rid == "a" * 128


# ==================== T17: 边界 129 字符 ====================

def test_boundary_129_rejected() -> None:
    """T17: 长度 129 超过上限 → 回退 UUID"""
    rid = _extract_request_id(_make_headers([("X-Request-ID", "a" * 129)]))
    assert re.match(r"^[0-9a-f]{8}-", rid)


# ==================== T18: ANSI 转义 ====================

def test_ansi_escape_rejected() -> None:
    """T18: ANSI 转义符 → 回退 UUID"""
    rid = _extract_request_id(_make_headers([("X-Request-ID", "\x1b[31mred\x1b[0m")]))
    assert "\x1b" not in rid


# ==================== T19: 反斜杠 ====================

def test_backslash_rejected() -> None:
    """T19: 反斜杠 → 回退 UUID（不在合法字符集）"""
    rid = _extract_request_id(_make_headers([("X-Request-ID", "back\\slash")]))
    assert "\\" not in rid


# ==================== T20: 数字开头 ====================

def test_starts_with_digit() -> None:
    """T20: 数字开头的合法 ID → 透传"""
    rid = _extract_request_id(_make_headers([("X-Request-ID", "123-abc")]))
    assert rid == "123-abc"


# ==================== 额外: 正则常量自检 ====================

def test_valid_request_id_regex_pattern() -> None:
    """正则常量自检：与协议 docstring 描述一致"""
    # 字符集：A-Za-z0-9_-.
    assert _VALID_REQUEST_ID.match("abc-123_XYZ.0")
    # 拒绝换行符
    assert not _VALID_REQUEST_ID.match("abc\ndef")
    # 拒绝反斜杠
    assert not _VALID_REQUEST_ID.match("abc\\def")
    # 拒绝空格
    assert not _VALID_REQUEST_ID.match("abc def")
    # 拒绝中文
    assert not _VALID_REQUEST_ID.match("你好")
    # 拒绝超长
    assert not _VALID_REQUEST_ID.match("a" * 129)
    # 接受边界 128
    assert _VALID_REQUEST_ID.match("a" * 128)
