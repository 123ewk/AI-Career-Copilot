"""JD Parser 单元测试

职责：
- 测试 JD 文本预处理功能（去 HTML、规范化空白、去重行）
- 测试 JD 分段功能（三段式结构识别）
- 覆盖正常流程、边界条件、异常流程

测试策略：
- 正常流程：标准三段式 JD（中文）
- 边界条件：空文本、无段落标题、单段落、超长文本
- 异常流程：特殊字符、HTML 注入、格式混乱
- 中英文混合：英文标题识别
"""

import pytest
from pydantic import ValidationError

from app.domain.job.parser import JDParser

# ==================== Fixtures ====================

@pytest.fixture
def parser() -> JDParser:
    """JDParser 实例"""
    return JDParser()


# ==================== 正常流程 ====================

class TestJDParserNormalFlow:
    """正常流程测试"""

    async def test_parse_standard_three_section_jd(self, parser: JDParser) -> None:
        """标准三段式 JD：职位描述 + 任职要求 + 福利待遇"""
        jd_text = """
职位描述：
1. 负责公司核心系统的架构设计与开发
2. 参与技术方案评审和代码 Review
3. 指导初级开发人员，提升团队技术水平

任职要求：
1. 本科及以上学历，计算机相关专业
2. 5 年以上 Python 开发经验
3. 熟悉 FastAPI、SQLAlchemy、Redis 等技术栈
4. 有大型项目架构设计经验优先

福利待遇：
1. 五险一金
2. 带薪年假 15 天
3. 年终奖 3-6 个月
4. 弹性工作制
"""
        result = await parser.parse(jd_text)

        # 验证分段结果
        assert "responsibilities" in result.sections
        assert "requirements" in result.sections
        assert "benefits" in result.sections

        # 验证内容
        assert "负责公司核心系统的架构设计与开发" in result.sections["responsibilities"]
        assert "本科及以上学历" in result.sections["requirements"]
        assert "五险一金" in result.sections["benefits"]

        # 验证元数据
        assert result.metadata["section_count"] == 3
        assert "responsibilities" in result.metadata["sections_found"]
        assert "requirements" in result.metadata["sections_found"]
        assert "benefits" in result.metadata["sections_found"]

    async def test_parse_jd_with_html_tags(self, parser: JDParser) -> None:
        """包含 HTML 标签的 JD"""
        jd_text = """
<p><strong>职位描述：</strong></p>
<ul>
<li>负责后端系统开发</li>
<li>参与架构设计</li>
</ul>

<p><strong>任职要求：</strong></p>
<ol>
<li>3 年以上 Python 经验</li>
<li>熟悉 FastAPI</li>
</ol>
"""
        result = await parser.parse(jd_text)

        # 验证 HTML 标签被移除
        assert "<p>" not in result.cleaned_text
        assert "<li>" not in result.cleaned_text
        assert "<strong>" not in result.cleaned_text

        # 验证内容保留
        assert "负责后端系统开发" in result.sections.get("responsibilities", "")
        assert "3 年以上 Python 经验" in result.sections.get("requirements", "")

    async def test_parse_jd_with_colon_variations(self, parser: JDParser) -> None:
        """不同冒号格式的 JD"""
        jd_text = """
职位描述:负责系统开发
任职要求:3年经验
福利待遇:五险一金
"""
        result = await parser.parse(jd_text)

        # 验证分段
        assert "responsibilities" in result.sections
        assert "requirements" in result.sections
        assert "benefits" in result.sections

    async def test_parse_jd_without_colon(self, parser: JDParser) -> None:
        """没有冒号的 JD"""
        jd_text = """
职位描述
负责系统开发
参与架构设计

任职要求
3年经验
熟悉Python

福利待遇
五险一金
带薪年假
"""
        result = await parser.parse(jd_text)

        # 验证分段
        assert "responsibilities" in result.sections
        assert "requirements" in result.sections
        assert "benefits" in result.sections


# ==================== 边界条件 ====================

class TestJDParserEdgeCases:
    """边界条件测试"""

    async def test_parse_empty_text(self, parser: JDParser) -> None:
        """空文本"""
        with pytest.raises(ValidationError):
            await parser.parse("")

    async def test_parse_whitespace_only(self, parser: JDParser) -> None:
        """纯空白文本"""
        with pytest.raises(ValidationError):
            await parser.parse("   \n\t\n   ")

    async def test_parse_no_section_headers(self, parser: JDParser) -> None:
        """无段落标题的 JD"""
        jd_text = """
负责公司核心系统的架构设计与开发
参与技术方案评审和代码 Review
本科及以上学历，计算机相关专业
五险一金，带薪年假
"""
        result = await parser.parse(jd_text)

        # 所有内容归入 "other"
        assert "other" in result.sections
        assert len(result.sections) == 1

    async def test_parse_single_section(self, parser: JDParser) -> None:
        """单段落 JD"""
        jd_text = """
职位描述：
1. 负责系统开发
2. 参与架构设计
3. 指导初级开发
"""
        result = await parser.parse(jd_text)

        assert "responsibilities" in result.sections
        assert len(result.sections) == 1

    async def test_parse_duplicate_lines(self, parser: JDParser) -> None:
        """连续重复行"""
        jd_text = """
职位描述：
负责系统开发
负责系统开发
负责系统开发
参与架构设计
"""
        result = await parser.parse(jd_text)

        # 验证去重
        content = result.sections["responsibilities"]
        assert content.count("负责系统开发") == 1

    async def test_parse_multiple_blank_lines(self, parser: JDParser) -> None:
        """多个连续空行"""
        jd_text = """
职位描述：


负责系统开发



参与架构设计


"""
        result = await parser.parse(jd_text)

        # 验证空行规范化
        assert "\n\n\n" not in result.cleaned_text

    async def test_parse_special_characters(self, parser: JDParser) -> None:
        """特殊字符"""
        jd_text = """
职位描述：
\u200b负责系统开发\u200b
\u200c参与架构设计\u200c

任职要求：
\ufeff3年经验\ufeff
"""
        result = await parser.parse(jd_text)

        # 验证特殊字符被移除
        assert "\u200b" not in result.cleaned_text
        assert "\u200c" not in result.cleaned_text
        assert "\ufeff" not in result.cleaned_text


# ==================== 中英文混合 ====================

class TestJDParserBilingual:
    """中英文混合测试"""

    async def test_parse_english_section_headers(self, parser: JDParser) -> None:
        """英文段落标题"""
        jd_text = """
Job Description:
1. Design and develop backend systems
2. Participate in architecture design

Requirements:
1. 3+ years Python experience
2. Familiar with FastAPI

Benefits:
1. Five insurance and one fund
2. 15 days annual leave
"""
        result = await parser.parse(jd_text)

        # 验证英文标题识别
        assert "responsibilities" in result.sections
        assert "requirements" in result.sections
        assert "benefits" in result.sections

    async def test_parse_mixed_headers(self, parser: JDParser) -> None:
        """中英文混合标题"""
        jd_text = """
职位描述 / Job Description:
负责系统开发

Requirements / 任职要求:
3年经验

Benefits / 福利待遇:
五险一金
"""
        result = await parser.parse(jd_text)

        # 验证混合标题识别
        assert "responsibilities" in result.sections
        assert "requirements" in result.sections
        assert "benefits" in result.sections


# ==================== 元数据 ====================

class TestJDParserMetadata:
    """元数据测试"""

    async def test_metadata_fields(self, parser: JDParser) -> None:
        """元数据字段完整性"""
        jd_text = """
职位描述：
负责系统开发

任职要求：
3年经验
"""
        result = await parser.parse(jd_text)

        assert "raw_length" in result.metadata
        assert "cleaned_length" in result.metadata
        assert "line_count" in result.metadata
        assert "section_count" in result.metadata
        assert "sections_found" in result.metadata

    async def test_metadata_values(self, parser: JDParser) -> None:
        """元数据值正确性"""
        jd_text = """
职位描述：
负责系统开发
参与架构设计

任职要求：
3年经验
熟悉Python
"""
        result = await parser.parse(jd_text)

        assert result.metadata["raw_length"] == len(jd_text)
        assert result.metadata["section_count"] == 2
        assert "responsibilities" in result.metadata["sections_found"]
        assert "requirements" in result.metadata["sections_found"]
