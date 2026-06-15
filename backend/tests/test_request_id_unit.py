"""Test 1: _extract_request_id 单元测试"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import re
from app.api.middleware.request_id import _extract_request_id, _VALID_REQUEST_ID

def make_headers(pairs):
    return [(n.encode(), v.encode()) for n, v in pairs]

print("======== _extract_request_id 单元测试 ========")

# 1. 无任何 header -> UUID
rid = _extract_request_id([])
assert re.match(r"^[0-9a-f]{8}-", rid)
print("TEST 1 (无 header): PASS ->", rid[:8] + "...")

# 2. X-Request-ID 合法
rid = _extract_request_id(make_headers([("X-Request-ID", "abc-123_xyz.v2")]))
assert rid == "abc-123_xyz.v2"
print("TEST 2 (X-Request-ID 合法): PASS ->", rid)

# 3. 小写 header
rid = _extract_request_id(make_headers([("x-request-id", "lowercase-test")]))
assert rid == "lowercase-test"
print("TEST 3 (小写 header): PASS")

# 4. X-Correlation-ID
rid = _extract_request_id(make_headers([("X-Correlation-ID", "corr-001")]))
assert rid == "corr-001"
print("TEST 4 (X-Correlation-ID): PASS")

# 5. X-Trace-ID
rid = _extract_request_id(make_headers([("X-Trace-ID", "trace-001")]))
assert rid == "trace-001"
print("TEST 5 (X-Trace-ID): PASS")

# 6. 列表顺序优先
rid = _extract_request_id(make_headers([
    ("X-Correlation-ID", "first"),
    ("X-Request-ID", "second"),
]))
assert rid == "first", rid
print("TEST 6 (遍历顺序): PASS ->", rid)

# 7. 换行符注入
rid = _extract_request_id(make_headers([("X-Request-ID", "evil\nINJECT")]))
assert "\n" not in rid
assert re.match(r"^[0-9a-f]{8}-", rid)
print("TEST 7 (换行符注入): PASS ->", rid[:8] + "...")

# 8. SQL 注入
rid = _extract_request_id(make_headers([("X-Request-ID", "' OR 1=1 --")]))
assert "OR" not in rid
print("TEST 8 (SQL 注入): PASS")

# 9. 中文
rid = _extract_request_id(make_headers([("X-Request-ID", "你好")]))
assert re.match(r"^[0-9a-f]{8}-", rid)
print("TEST 9 (中文): PASS")

# 10. 超长
rid = _extract_request_id(make_headers([("X-Request-ID", "a" * 200)]))
assert len(rid) <= 36
print("TEST 10 (超长 200 字符): PASS")

# 11. 空字符串
rid = _extract_request_id(make_headers([("X-Request-ID", "")]))
assert re.match(r"^[0-9a-f]{8}-", rid)
print("TEST 11 (空字符串): PASS")

# 12. 仅空白
rid = _extract_request_id(make_headers([("X-Request-ID", "   ")]))
assert re.match(r"^[0-9a-f]{8}-", rid)
print("TEST 12 (仅空白): PASS")

# 13. 非法 header 名
rid = _extract_request_id(make_headers([("X-Other", "abc")]))
assert re.match(r"^[0-9a-f]{8}-", rid)
print("TEST 13 (非法 header 名): PASS")

# 14. trim
rid = _extract_request_id(make_headers([("X-Request-ID", "  trim-me  ")]))
assert rid == "trim-me"
print("TEST 14 (前后空白 trim): PASS")

# 15. 合法符号
rid = _extract_request_id(make_headers([("X-Request-ID", "a_b-c.d")]))
assert rid == "a_b-c.d"
print("TEST 15 (所有合法符号): PASS")

# 16. 边界 128
rid = _extract_request_id(make_headers([("X-Request-ID", "a" * 128)]))
assert rid == "a" * 128
print("TEST 16 (128 字符上限): PASS")

# 17. 边界 129
rid = _extract_request_id(make_headers([("X-Request-ID", "a" * 129)]))
assert re.match(r"^[0-9a-f]{8}-", rid)
print("TEST 17 (129 字符): PASS")

# 18. ANSI 转义
rid = _extract_request_id(make_headers([("X-Request-ID", "\x1b[31mred\x1b[0m")]))
assert "\x1b" not in rid
print("TEST 18 (ANSI 转义): PASS")

# 19. 反斜杠
rid = _extract_request_id(make_headers([("X-Request-ID", "back\\slash")]))
assert "\\" not in rid
print("TEST 19 (反斜杠): PASS")

# 20. 数字开头
rid = _extract_request_id(make_headers([("X-Request-ID", "123-abc")]))
assert rid == "123-abc"
print("TEST 20 (数字开头): PASS")

print()
print("ALL_UNIT_TESTS_OK")
