"""File 工具集

负责把不同格式的简历文件统一抽取为纯文本,
供 Resume Parser / RAG / LLM 等下游消费。

当前提供的组件:
- PDFReader: PDF 异步文本提取(基于 pypdf)
- DOCXReader: Word(docx)异步文本提取(基于 python-docx)
- parse_resume: 注册为 LangChain 工具的统一简历解析入口
"""

from app.tools.file.docx_reader import DOCXReader, DOCXReadResult
from app.tools.file.pdf_reader import PDFReader, PDFReadResult
from app.tools.file.resume_parser import parse_resume

__all__ = [
    # 底层 Reader
    "DOCXReadResult",
    "DOCXReader",
    "PDFReadResult",
    "PDFReader",
    # LangChain 工具
    "parse_resume",
]
