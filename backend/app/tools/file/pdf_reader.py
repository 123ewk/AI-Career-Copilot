"""PDF Reader 异步文本提取工具

职责:
- 提供 PDF 文件的异步文本提取能力
- 支持文件路径与字节流两种入参方式
- 输出结构化结果(纯文本、页数、元数据)

设计动机:
- 简历解析(RAG/LLM)的前置基建,需要从 PDF 中提取纯文本
- pypdf 是纯 Python 实现,无原生依赖,跨平台稳定
- 第三方 PDF 库均为同步实现,使用 asyncio.to_thread 包装到线程池,
  避免阻塞 Event Loop,FastAPI 高并发场景下不会因 PDF 解析拖垮整个服务

并发模型:
- asyncio.to_thread 把同步调用丢到默认 ThreadPoolExecutor
- 多个并发请求会被分配到不同线程,IO 密集型场景下吞吐良好
- 单个 PDF 的解析在独立线程内完成,失败不会影响其他请求

异常映射:
- 文件不存在 / 输入为空 / 超过大小限制 -> ValidationError(400)
- PDF 加密 -> ValidationError(400,提示需密码)
- 解析失败(文件损坏/格式错误) -> ExternalServiceError(502)
"""

from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pypdf import PdfReader as _PypdfReader
from pypdf.errors import EmptyFileError, PdfReadError

from app.core.exceptions import (
    ExternalServiceError,
    ValidationError,
)
from app.core.logger import logger
from app.core.settings import get_settings

# ==================== 数据结构 ====================

@dataclass(slots=True)
class PDFReadResult:
    """PDF 读取结果

    Attributes:
        text: 全部页面的纯文本,页与页之间以换行符分隔
        page_count: 页面总数
        metadata: PDF 元数据(title/author/creator 等),无元数据时为空 dict
    """

    text: str
    page_count: int
    metadata: dict[str, str] = field(default_factory=dict)


# ==================== 内部辅助 ====================

def _resolve_size_limit() -> int:
    """从 settings 读取最大允许字节数

    复用全局上传大小限制,避免 Reader 维护独立的配置。
    转换为字节单位便于与 len(bytes) 直接比较。
    """
    return get_settings().max_upload_size_mb * 1024 * 1024


def _ensure_bytes_size(content: bytes) -> None:
    """校验字节流大小

    为什么提前校验:
    - PDF 解析是 CPU + IO 密集型,大文件会长时间占用线程
    - 上传层已有大小限制,这里做二次防御,处理 path 入参的本地文件
    """
    if not content:
        raise ValidationError(
            detail="PDF 内容为空",
            error_code="VAL_002",
            extra={"size": 0},
        )
    size_limit = _resolve_size_limit()
    if len(content) > size_limit:
        raise ValidationError(
            detail=f"PDF 文件超过大小限制({size_limit // (1024 * 1024)}MB)",
            error_code="VAL_003",
            extra={"size": len(content), "limit": size_limit},
        )


def _read_path_sync(path: Path) -> bytes:
    """同步读取本地文件,在线程池内执行

    用 'rb' 模式保留原始字节,后续统一走 bytes 解析路径,减少分支。
    """
    if not path.exists():
        raise ValidationError(
            detail=f"PDF 文件不存在: {path}",
            error_code="VAL_004",
            extra={"path": str(path)},
        )
    if not path.is_file():
        raise ValidationError(
            detail=f"路径不是文件: {path}",
            error_code="VAL_005",
            extra={"path": str(path)},
        )
    return path.read_bytes()


def _parse_pdf_sync(content: bytes) -> PDFReadResult:
    """同步解析 PDF,纯 CPU 工作,放线程池避免阻塞

    失败模式:
    - EmptyFileError -> 业务校验错误(空文件)
    - PdfReadError -> 基础设施错误(文件损坏)
    - is_encrypted=True -> 业务校验错误(加密)
    """
    try:
        reader = _PypdfReader(io.BytesIO(content), strict=False)
    except EmptyFileError as exc:
        raise ValidationError(
            detail="PDF 文件为空",
            error_code="VAL_006",
            extra={"exception": type(exc).__name__},
        ) from exc
    except PdfReadError as exc:
        raise ExternalServiceError(
            detail="PDF 文件无法解析,可能已损坏或格式不标准",
            error_code="EXT_002",
            extra={"exception": type(exc).__name__},
        ) from exc

    # 加密文件 pypdf 会延迟到访问页面时才报错,显式预检以给出友好提示
    if reader.is_encrypted:
        raise ValidationError(
            detail="PDF 已加密,当前 Reader 不支持解密,请先去除密码",
            error_code="VAL_007",
        )

    # ---- 逐页提取文本 ----
    # 某些 PDF 页面 extract_text() 返回 None(纯图片扫描版),做容错
    chunks: list[str] = []
    for page in reader.pages:
        chunks.append(page.extract_text() or "")
    text = "\n".join(chunks)

    # ---- 元数据 ----
    # pypdf 的 metadata 是 DocumentInformation,可能为 None
    raw_meta: Any = reader.metadata or {}
    metadata: dict[str, str] = {
        str(key): str(value)
        for key, value in dict(raw_meta).items()
        if value is not None
    }

    return PDFReadResult(
        text=text,
        page_count=len(reader.pages),
        metadata=metadata,
    )


# ==================== 公共 API ====================

class PDFReader:
    """PDF 异步读取器

    用法:
        reader = PDFReader()
        result = await reader.read("resume.pdf")
        print(result.text, result.page_count)

    设计为可复用实例:Reader 是无状态的,实例方法只是入口,
    实际解析在 _parse_pdf_sync 内完成。重复创建实例没有副作用,
    但建议复用以与项目其他 Tool 类风格保持一致。
    """

    async def read(self, path: str | Path) -> PDFReadResult:
        """从本地路径异步读取 PDF

        Args:
            path: PDF 文件绝对或相对路径

        Returns:
            PDFReadResult: 包含文本、页数、元数据

        Raises:
            ValidationError: 文件不存在/不是文件/超过大小限制/文件加密
            ExternalServiceError: PDF 解析失败(文件损坏)
        """
        path_obj = Path(path)
        logger.info("PDF 读取开始 | path={}", path_obj)

        # IO 与解析都在线程池内完成,完全不阻塞 Event Loop
        content = await asyncio.to_thread(_read_path_sync, path_obj)
        _ensure_bytes_size(content)
        result = await asyncio.to_thread(_parse_pdf_sync, content)

        logger.info(
            "PDF 读取完成 | path={} | pages={} | text_len={}",
            path_obj,
            result.page_count,
            len(result.text),
        )
        return result

    async def read_bytes(self, content: bytes) -> PDFReadResult:
        """从字节流异步读取 PDF

        典型用法:HTTP 上传文件、对象存储下载等已加载到内存的场景。

        Args:
            content: PDF 文件的原始字节流

        Returns:
            PDFReadResult: 包含文本、页数、元数据

        Raises:
            ValidationError: 内容类型错误/为空/超过大小限制/文件加密
            ExternalServiceError: PDF 解析失败
        """
        if not isinstance(content, (bytes, bytearray)):
            raise ValidationError(
                detail="content 必须是 bytes 类型",
                error_code="VAL_008",
                extra={"type": type(content).__name__},
            )
        content_bytes = bytes(content)  # bytearray 转 bytes 避免下游误判
        _ensure_bytes_size(content_bytes)
        logger.info("PDF 读取开始(bytes) | size={}", len(content_bytes))

        result = await asyncio.to_thread(_parse_pdf_sync, content_bytes)

        logger.info(
            "PDF 读取完成(bytes) | pages={} | text_len={}",
            result.page_count,
            len(result.text),
        )
        return result
