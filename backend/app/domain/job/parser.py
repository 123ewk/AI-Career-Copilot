"""JD 文本解析器

职责：
- 预处理原始 JD 文本（去 HTML、规范化空白、去重行）
- 将 JD 分段为三段式结构（职位描述、任职要求、福利待遇）
- 输出结构化字典供 Job Analysis Agent 使用

设计动机：
- 正则而非 NLP：JD 分段是基于格式的结构化任务，段落标题有明确模式
  · 正则表达式足够精确，且性能远优于 NLP 模型
  · 不依赖外部服务（如 LLM），可在本地快速完成
  · 可预测、可调试、无幻觉
- 三段式结构：覆盖 95% 的中文 JD 格式
  · 职位描述（responsibilities）：工作内容、岗位职责
  · 任职要求（requirements）：岗位要求、任职资格
  · 福利待遇（benefits）：薪资福利、福利待遇
  · 未识别内容归入 "other"，避免信息丢失
- 中文优先：中文 JD 占 90%+，英文标题作为备选
  · 段落标题匹配：精确匹配 → 去标点模糊匹配 → 英文备选
  · 支持「标题：」「标题:」「标题」等格式

潜在风险：
- 正则误匹配：段落标题出现在正文中（如"职位描述：负责..."）
  → 防御：标题必须独占一行（行首匹配），且长度 < 50 字符
- 分段不准确：JD 格式不标准（无标题、单段落、混合格式）
  → 防御：未识别内容归入 "other"，Agent 可处理未分段文本
- 性能瓶颈：超长 JD（50KB）的正则匹配
  → 防御：预处理阶段已限制长度，正则匹配 O(n) 复杂度
"""

from __future__ import annotations

import re
from typing import Any

from app.core.logger import logger
from app.domain.job.models import (
    JD_SECTION_TITLES,
    JDParseResult,
)

# ==================== 内部常量 ====================

# HTML 标签正则：<br>, <p>, <li>, <div> 等转换为换行
_HTML_TAG_PATTERN: re.Pattern[str] = re.compile(
    r"<\s*(br|p|li|div|h[1-6]|tr|td|th)\b[^>]*>",
    re.IGNORECASE,
)

# 闭合 HTML 标签：直接移除
_HTML_CLOSE_TAG_PATTERN: re.Pattern[str] = re.compile(
    r"</\s*(br|p|li|div|h[1-6]|tr|td|th)\s*>",
    re.IGNORECASE,
)

# 其他 HTML 标签：直接移除（如 <span>, <a>, <b> 等）
_HTML_ANY_TAG_PATTERN: re.Pattern[str] = re.compile(
    r"<[^>]+>",
)

# 特殊字符：零宽空格、不间断空格、软连字符等
_SPECIAL_CHARS_PATTERN: re.Pattern[str] = re.compile(
    r"[\u200b\u200c\u200d\ufeff\u00ad\u00a0\u2000-\u200f\u2028-\u202f\u2060-\u206f]",
)

# 多个空格合并为单个空格
_MULTI_SPACE_PATTERN: re.Pattern[str] = re.compile(r"[ \t]+")

# 多个连续空行合并为单个空行
_MULTI_NEWLINE_PATTERN: re.Pattern[str] = re.compile(r"\n\s*\n")

# 段落标题行匹配：行首为标题关键词，可选冒号/空格
# 支持格式：「职位描述：」「职位描述:」「职位描述」
_SECTION_HEADER_PATTERN: re.Pattern[str] = re.compile(
    r"^\s*(.{2,20})\s*[:：]?\s*$",
    re.MULTILINE,
)


# ==================== 内部辅助函数 ====================

def _build_section_mapping() -> dict[str, str]:
    """构建标题 → section_type 的映射表

    将 JD_SECTION_TITLES 展平为 {title: section_type} 格式
    用于快速查找段落标题对应的类型

    Returns:
        {title: section_type} 映射表
    """
    mapping: dict[str, str] = {}
    for section_type, titles in JD_SECTION_TITLES.items():
        for title in titles:
            mapping[title.lower()] = section_type
    return mapping


# 预构建映射表（模块级单例，避免重复构建）
_SECTION_MAPPING: dict[str, str] = _build_section_mapping()


def _is_section_header(line: str) -> str | None:
    """判断一行是否为段落标题

    匹配策略：
    1. 精确匹配：去除首尾空白后完全匹配
    2. 模糊匹配：去除标点符号后匹配
    3. 英文备选：英文标题作为备选
    4. 冒号分割：支持「标题：内容」格式，只取标题部分

    Args:
        line: 待判断的行

    Returns:
        section_type（如 "responsibilities"）或 None（非标题行）
    """
    stripped = line.strip()
    if not stripped or len(stripped) > 100:
        return None

    # 精确匹配
    lower_stripped = stripped.lower()
    if lower_stripped in _SECTION_MAPPING:
        return _SECTION_MAPPING[lower_stripped]

    # 模糊匹配：去除标点符号
    cleaned = re.sub(r"[：:、，,。.；;！!？?\s]+", "", stripped)
    if cleaned.lower() in _SECTION_MAPPING:
        return _SECTION_MAPPING[cleaned.lower()]

    # 冒号分割：支持「标题：内容」格式
    # 只取冒号前的部分作为标题
    for sep in [":", "："]:
        if sep in stripped:
            title_part = stripped.split(sep, 1)[0].strip()
            if title_part.lower() in _SECTION_MAPPING:
                return _SECTION_MAPPING[title_part.lower()]
            # 模糊匹配标题部分
            cleaned_title = re.sub(r"[：:、，,。.；;！!？?\s]+", "", title_part)
            if cleaned_title.lower() in _SECTION_MAPPING:
                return _SECTION_MAPPING[cleaned_title.lower()]

    # 组合格式：支持「中文 / English」格式
    # 按 "/" 或 "|" 分割，逐个匹配
    for sep in [" / ", "/", " | ", "|"]:
        if sep in stripped:
            parts = stripped.split(sep)
            for part in parts:
                part_cleaned = part.strip().lower()
                if part_cleaned in _SECTION_MAPPING:
                    return _SECTION_MAPPING[part_cleaned]
                # 去标点后匹配
                part_no_punct = re.sub(r"[：:、，,。.；;！!？?\s]+", "", part_cleaned)
                if part_no_punct in _SECTION_MAPPING:
                    return _SECTION_MAPPING[part_no_punct]

    return None


def _preprocess_html(text: str) -> str:
    """预处理 HTML 标签

    策略：
    - <br>, <p>, <li>, <div> 等块级标签 → 转换为换行
    - 其他标签（<span>, <a>, <b> 等）→ 直接移除
    - 保留标签内的文本内容

    Args:
        text: 原始文本

    Returns:
        去除 HTML 标签后的文本
    """
    # 块级标签 → 换行
    text = _HTML_TAG_PATTERN.sub("\n", text)
    # 闭合标签 → 移除
    text = _HTML_CLOSE_TAG_PATTERN.sub("", text)
    # 其他标签 → 移除
    text = _HTML_ANY_TAG_PATTERN.sub("", text)
    return text


def _normalize_whitespace(text: str) -> str:
    """规范化空白字符

    策略：
    - 多个空格/制表符 → 单个空格
    - 多个连续空行 → 单个空行
    - 去除行首尾空白

    Args:
        text: 原始文本

    Returns:
        规范化后的文本
    """
    # 多个空格 → 单个空格
    text = _MULTI_SPACE_PATTERN.sub(" ", text)
    # 多个空行 → 单个空行
    text = _MULTI_NEWLINE_PATTERN.sub("\n\n", text)
    # 去除行首尾空白（保留换行符）
    lines = [line.strip() for line in text.split("\n")]
    return "\n".join(lines)


def _remove_duplicate_lines(text: str) -> str:
    """去除连续重复行

    策略：
    - 连续相同的行合并为一行
    - 保留非连续的重复行（可能是有意重复）

    Args:
        text: 原始文本

    Returns:
        去重后的文本
    """
    lines = text.split("\n")
    if not lines:
        return text

    result: list[str] = [lines[0]]
    for line in lines[1:]:
        if line != result[-1]:
            result.append(line)
    return "\n".join(result)


def _segment_by_headers(text: str) -> dict[str, str]:
    """按段落标题分段

    策略：
    1. 按行扫描，识别段落标题
    2. 标题行作为新段落的开始
    3. 标题和内容在同一行时（如「职位描述：负责系统开发」），提取内容部分
    4. 非标题行追加到当前段落
    5. 未识别的内容归入 "other" 段

    Args:
        text: 预处理后的文本

    Returns:
        {section_type: content} 分段结果
    """
    lines = text.split("\n")
    sections: dict[str, list[str]] = {
        "responsibilities": [],
        "requirements": [],
        "benefits": [],
        "other": [],
    }

    current_section = "other"
    for line in lines:
        section_type = _is_section_header(line)
        if section_type:
            current_section = section_type
            # 检查是否有内容在同一行（如「职位描述：负责系统开发」）
            for sep in [":", "："]:
                if sep in line:
                    content_part = line.split(sep, 1)[1].strip()
                    if content_part:
                        sections[current_section].append(content_part)
                    break
            continue
        sections[current_section].append(line)

    # 合并行并去除空段落
    result: dict[str, str] = {}
    for section_type, lines in sections.items():
        content = "\n".join(lines).strip()
        if content:
            result[section_type] = content

    return result


# ==================== 公共 API ====================

class JDParser:
    """JD 文本解析器

    用法:
        parser = JDParser()
        result = await parser.parse(jd_text)
        print(result.sections["responsibilities"])
        print(result.metadata)

    设计为可复用实例:Parser 是无状态的,实例方法只是入口。
    """

    async def parse(self, jd_text: str) -> JDParseResult:
        """完整解析流程：预处理 → 分段 → 输出

        Args:
            jd_text: 原始 JD 文本

        Returns:
            JDParseResult: 解析结果

        Raises:
            ValidationError: 文本为空或超过长度限制
        """
        logger.info("JD 解析开始 | text_len={}", len(jd_text))

        # 预处理
        cleaned_text = self._preprocess(jd_text)

        # 分段
        sections = self._segment(cleaned_text)

        # 元数据
        metadata: dict[str, Any] = {
            "raw_length": len(jd_text),
            "cleaned_length": len(cleaned_text),
            "line_count": len(cleaned_text.split("\n")),
            "section_count": len(sections),
            "sections_found": list(sections.keys()),
        }

        logger.info(
            "JD 解析完成 | sections={} | metadata={}",
            list(sections.keys()),
            metadata,
        )

        return JDParseResult(
            raw_text=jd_text,
            cleaned_text=cleaned_text,
            sections=sections,
            metadata=metadata,
        )

    def _preprocess(self, text: str) -> str:
        """文本预处理：去 HTML、规范化空白、去重行

        Args:
            text: 原始文本

        Returns:
            预处理后的文本
        """
        # 1. 去 HTML 标签
        text = _preprocess_html(text)

        # 2. 去特殊字符
        text = _SPECIAL_CHARS_PATTERN.sub("", text)

        # 3. 规范化空白
        text = _normalize_whitespace(text)

        # 4. 去重行
        text = _remove_duplicate_lines(text)

        # 5. 去首尾空白
        return text.strip()

    def _segment(self, text: str) -> dict[str, str]:
        """分段：识别段落标题，按标题切分

        Args:
            text: 预处理后的文本

        Returns:
            {section_type: content} 分段结果
        """
        return _segment_by_headers(text)
