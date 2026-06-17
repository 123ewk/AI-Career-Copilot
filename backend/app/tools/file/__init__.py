"""File 工具集

负责把不同格式的简历文件统一抽取为纯文本,
供上传链路消费(上传时同步解析入库)。

当前提供的组件:
- PDFReader: PDF 异步文本提取(基于 pypdf)
- DOCXReader: Word(docx)异步文本提取(基于 python-docx)
- make_get_resume_content_tool: 工厂方法,返回 LangChain 工具,
  供 Agent 从 DB 查询已入库的简历(不做文件解析)
"""

from app.tools.file.docx_reader import DOCXReader, DOCXReadResult
from app.tools.file.pdf_reader import PDFReader, PDFReadResult
from app.tools.file.resume_reader import make_get_resume_content_tool

__all__ = [
    # 底层 Reader(上传链路用)
    "DOCXReadResult",
    "DOCXReader",
    "PDFReadResult",
    "PDFReader",
    # LangChain 工具工厂(Agent 用)
    "make_get_resume_content_tool",
]
