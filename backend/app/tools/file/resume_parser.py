"""Resume Parser Tool

把 PDFReader / DOCXReader 整合并注册为 LangChain 工具,
供 Agent 直接 bind_tools 调用。
"""

from __future__ import annotations

import json
from pathlib import Path

from langchain_core.tools import tool

from app.tools.file.docx_reader import DOCXReader
from app.tools.file.pdf_reader import PDFReader

# 模块级单例:Reader 内部无状态,共享实例避免重复创建
_pdf_reader = PDFReader()
_docx_reader = DOCXReader()


@tool
async def parse_resume(file_path: str) -> str:
    """解析简历文件(PDF / DOCX),返回抽取出的纯文本与基础元信息。

    Args:
        file_path: 简历文件的本地绝对路径,通过扩展名(.pdf / .docx)自动选择 Reader

    Returns:
        JSON 字符串,字段:
        - file_type: "pdf" / "docx"
        - text: 解析后的纯文本
        - page_count: PDF 页数(DOCX 时为 null)
        - paragraph_count: DOCX 段落数(PDF 时为 null)
    """
    path = Path(file_path)
    extension = path.suffix.lower()

    if extension == ".pdf":
        result = await _pdf_reader.read(path)
        payload = {
            "file_type": "pdf",
            "text": result.text,
            "page_count": result.page_count,
            "paragraph_count": None,
        }
    elif extension == ".docx":
        docx_result = await _docx_reader.read(path)
        payload = {
            "file_type": "docx",
            "text": docx_result.text,
            "page_count": None,
            "paragraph_count": docx_result.paragraph_count,
        }
    else:
        payload = {
            "file_type": "unknown",
            "text": "",
            "error": f"不支持的文件类型「{extension}」,仅支持 .pdf / .docx",
        }

    return json.dumps(payload, ensure_ascii=False)


__all__ = ["parse_resume"]
