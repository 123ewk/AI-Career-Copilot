"""Resume 域校验器

职责：
- 集中管理简历域的可复用校验规则（文件名、MIME、文件大小、文件头、原文长度）
- 与 Pydantic DTO（models.py）解耦：DTO 负责「数据结构」，本模块负责「校验规则」
- Service 层（上传、解析、批量导入）能直接复用，避免重复实现

设计动机：
- 单一职责：models.py 只定义 DTO 字段与文档，校验规则统一在 validator.py
- 可测试性：纯函数式校验器，单测无需构造完整 Pydantic Model
- 可复用性：未来「批量导入」「简历模板导入」等场景都能复用同一套规则
- 集中维护：MIME 列表 / 大小上限 / Magic Number 等策略升级时只需改一个文件

校验维度：
- 文件名：非空、长度上限、无路径分隔符（防路径穿越）
- 文件扩展名：白名单（.pdf / .docx / .doc）
- MIME 类型：白名单 + 与扩展名一致性校验
- 文件大小：上下界限制（防 OOM、防空文件）
- Magic Number：文件二进制头校验（防伪装文件 / 扩展名绕过）
- 解析后原文：与 models.RESUME_RAW_TEXT_MAX_LENGTH 对齐

安全设计：
- Magic Number 强校验：仅靠扩展名/MIME 可被绕过，必须看文件头
- 拒绝路径分隔符：防止用户上传「../../etc/passwd」类文件名注入
- 文件大小硬上限 10MB：PDF 简历一般 1-3MB，10MB 已远超合理上限
- 解析后原文上限 50000 字符：与 DTO 一致，防止恶意 PDF（嵌入 JS / 大文件）触发 OOM
"""

from typing import Final

from app.domain.resume.models import RESUME_RAW_TEXT_MAX_LENGTH

# ==================== 常量 ====================

# 支持的简历文件扩展名（白名单）
# 选择理由：PRD §8.1 F-002 明确要求支持 PDF / DOCX
# 旧版 .doc 仅做兜底（部分用户仍在用 Office 2003）
ALLOWED_EXTENSIONS: Final[frozenset[str]] = frozenset({".pdf", ".docx", ".doc"})

# 支持的 MIME 类型（白名单）
# application/pdf：标准 PDF
# application/vnd.openxmlformats-officedocument.wordprocessingml.document：DOCX（OOXML）
# application/msword：旧版 DOC（二进制 OLE）
ALLOWED_MIME_TYPES: Final[frozenset[str]] = frozenset(
    {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/msword",
    }
)

# MIME 类型 → 合法扩展名映射（一致性校验）
# 防止 MIME 与扩展名不一致的混淆攻击
# 客户端可能发送 application/octet-stream + .pdf，此时按 .pdf 兜底即可
MIME_TO_EXTENSIONS: Final[dict[str, frozenset[str]]] = {
    "application/pdf": frozenset({".pdf"}),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": frozenset(
        {".docx"}
    ),
    "application/msword": frozenset({".doc"}),
}

# Magic Number（文件二进制头）按文件类型映射
# 防御扩展名伪造：仅校验后缀可被绕过，必须看前几个字节
# 选择仅校验「最少必要字节」：避免大文件读取开销
MAGIC_NUMBERS: Final[dict[str, bytes]] = {
    "pdf": b"%PDF",                                  # PDF 文件以 %PDF- 开头
    "docx": b"PK\x03\x04",                           # DOCX 本质是 ZIP 容器
    "doc": b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1",      # OLE Compound File（旧版 DOC）
}

# 识别扩展名 → Magic Number 键名
EXTENSION_TO_MAGIC_KEY: Final[dict[str, str]] = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".doc": "doc",
}

# 文件大小边界
# 上限 10MB：PDF 简历常见 1-3MB，10MB 已远超合理体积，阻止 OOM 攻击
# 下限 1 字节：拒绝空文件（空文件无法解析出任何内容）
MAX_FILE_SIZE_BYTES: Final[int] = 10 * 1024 * 1024   # 10 MB
MIN_FILE_SIZE_BYTES: Final[int] = 1

# 文件名边界
# 255 字符：与主流文件系统（NTFS/ext4）单文件名长度上限对齐
MAX_FILENAME_LENGTH: Final[int] = 255

# Magic Number 读取字节数上界
# 实际按各类型 Magic Number 的真实长度校验（PDF/DOCX=4, DOC=8）
# 设 8 为读取上限，避免对大文件做不必要的 IO
MAGIC_NUMBER_READ_BYTES: Final[int] = 8


# ==================== 校验器 ====================

def _extract_extension(filename: str) -> str:
    """从文件名提取扩展名（统一小写）

    Args:
        filename: 原始文件名

    Returns:
        小写的扩展名（含点），如 ".pdf"；无扩展名返回空串
    """
    if "." not in filename:
        return ""
    return filename.rsplit(".", 1)[-1].lower()


def validate_filename(filename: str) -> str:
    """文件名格式校验

    规则：
    1. 非空
    2. 长度 ≤ MAX_FILENAME_LENGTH（255）
    3. 不能包含路径分隔符（/ 或 \\）：防路径穿越攻击
    4. 不能包含 NUL 字符（\\x00）：防 C 风格字符串截断
    5. 扩展名必须在白名单内

    Args:
        filename: 客户端上传的原始文件名（不含路径）

    Returns:
        归一化后的文件名（首尾空白已 strip）

    Raises:
        ValueError: 文件名非法
    """
    if not isinstance(filename, str):
        raise ValueError("文件名必须是字符串")

    normalized = filename.strip()
    if not normalized:
        raise ValueError("文件名不能为空")

    if len(normalized) > MAX_FILENAME_LENGTH:
        raise ValueError(f"文件名长度不能超过 {MAX_FILENAME_LENGTH} 字符")

    # 路径分隔符：拒绝绝对路径 / 相对路径穿越
    if "/" in normalized or "\\" in normalized:
        raise ValueError("文件名不能包含路径分隔符")

    # NUL 字符：部分库（如 C 扩展）会以 NUL 截断字符串
    if "\x00" in normalized:
        raise ValueError("文件名不能包含空字符")

    extension = _extract_extension(normalized)
    if not extension:
        raise ValueError(f"文件名必须包含扩展名，允许的扩展名：{sorted(ALLOWED_EXTENSIONS)}")
    if f".{extension}" not in ALLOWED_EXTENSIONS:
        raise ValueError(
            f"不支持的简历文件类型「.{extension}」，"
            f"仅支持 {sorted(ALLOWED_EXTENSIONS)}"
        )

    return normalized


def validate_mime_type(mime_type: str, *, filename: str) -> str:
    """MIME 类型校验 + 与扩展名一致性校验

    规则：
    1. MIME 在白名单内
    2. MIME 与文件扩展名匹配（防止类型混淆攻击）
    3. 容忍 application/octet-stream（浏览器 fallback）：此时以扩展名为准

    Args:
        mime_type: 客户端声明的 MIME 类型
        filename: 原始文件名（用于读取扩展名）

    Returns:
        归一化后的 MIME 类型（小写）

    Raises:
        ValueError: MIME 不合法或与扩展名冲突
    """
    if not isinstance(mime_type, str) or not mime_type.strip():
        raise ValueError("MIME 类型不能为空")

    normalized_mime = mime_type.strip().lower()

    # 浏览器 fallback：application/octet-stream 视为「未知类型」
    # 此时仅校验扩展名，跳过一致性校验
    if normalized_mime == "application/octet-stream":
        extension = _extract_extension(filename)
        if not extension or f".{extension}" not in ALLOWED_EXTENSIONS:
            raise ValueError("MIME 类型未知且文件扩展名不支持")
        return normalized_mime

    if normalized_mime not in ALLOWED_MIME_TYPES:
        raise ValueError(
            f"不支持的 MIME 类型「{normalized_mime}」，"
            f"仅支持 {sorted(ALLOWED_MIME_TYPES)}"
        )

    # 一致性校验：MIME 与扩展名必须对应
    expected_extensions = MIME_TO_EXTENSIONS.get(normalized_mime)
    if expected_extensions is None:
        # 不应到达此分支（白名单已限制）
        raise ValueError(f"未配置 MIME「{normalized_mime}」对应的扩展名")

    extension = _extract_extension(filename)
    if not extension or f".{extension}" not in expected_extensions:
        raise ValueError(
            f"MIME 类型「{normalized_mime}」与文件扩展名「.{extension or ''}」不匹配"
        )

    return normalized_mime


def validate_file_size(size_bytes: int) -> int:
    """文件大小校验

    规则：
    1. 必须是 int（非 None、非负）
    2. ≥ MIN_FILE_SIZE_BYTES（拒绝空文件）
    3. ≤ MAX_FILE_SIZE_BYTES（10MB，防 OOM）

    设计：
    - 不做单位换算（KB/MB）：调用方传入字节数，避免单位混淆
    - Service 层读取 UploadFile.size 后调用本函数

    Args:
        size_bytes: 文件字节数

    Returns:
        原样返回 size_bytes（便于链式调用）

    Raises:
        ValueError: 大小不合法
    """
    if not isinstance(size_bytes, int) or isinstance(size_bytes, bool):
        raise ValueError("文件大小必须是整数")

    if size_bytes < MIN_FILE_SIZE_BYTES:
        raise ValueError("文件内容为空")

    if size_bytes > MAX_FILE_SIZE_BYTES:
        max_mb = MAX_FILE_SIZE_BYTES / (1024 * 1024)
        raise ValueError(f"文件大小不能超过 {max_mb:.0f}MB")

    return size_bytes


def validate_magic_number(content: bytes, *, filename: str) -> str:
    """Magic Number（文件头）校验

    防御场景：
    - 用户将 .exe 重命名为 .pdf：仅看扩展名会被绕过
    - 用户上传 .html 内容但文件名是 .pdf：仅看 MIME 会被绕过
    - Magic Number 是「最后一道防线」：基于文件实际字节内容做判断

    实现：
    - 仅读取前 8 字节（避免大文件 IO 开销）
    - 按扩展名选择对应 Magic Number 比对
    - 大小写敏感：二进制头不存在「忽略大小写」概念

    Args:
        content: 文件原始字节内容
        filename: 原始文件名（用于按扩展名选择 Magic Number）

    Returns:
        识别出的文件类型键名（"pdf" / "docx" / "doc"），便于调用方记录日志

    Raises:
        ValueError: 文件头与扩展名不匹配 / 内容为空
    """
    if not isinstance(content, (bytes, bytearray, memoryview)):
        raise ValueError("文件内容必须是字节类型")

    if len(content) > MAGIC_NUMBER_READ_BYTES:
        # 仅取前 N 字节用于校验，避免不必要的 IO
        content = bytes(content[:MAGIC_NUMBER_READ_BYTES])

    extension = _extract_extension(filename)
    magic_key = EXTENSION_TO_MAGIC_KEY.get(f".{extension}")
    if magic_key is None:
        # 理论上 validate_filename 已校验过扩展名，此处为防御性兜底
        raise ValueError(f"无法识别扩展名「.{extension}」对应的文件类型")

    expected_magic = MAGIC_NUMBERS[magic_key]
    if len(content) < len(expected_magic):
        raise ValueError("文件内容过短，无法识别文件类型")

    actual_magic = bytes(content[: len(expected_magic)])

    if actual_magic != expected_magic:
        raise ValueError(
            f"文件内容与扩展名「.{extension}」不匹配，疑似伪造文件"
        )

    return magic_key


def validate_parsed_text_length(raw_text: str) -> str:
    """解析后原文长度校验

    适用场景：
    - PDF/DOCX 解析完成后，校验 raw_text 是否超过 DTO 上限
    - 防止恶意 PDF（嵌入 JS / 大量重复内容）导致解析后文本膨胀

    与 models.RESUME_RAW_TEXT_MAX_LENGTH 对齐：
    - DTO 是 API 契约层校验（早失败）
    - 本函数是 Service 层校验（解析后兜底）
    - 两层都做，深度防御

    Args:
        raw_text: 解析后的纯文本

    Returns:
        原样返回 raw_text（便于链式调用）

    Raises:
        ValueError: 文本为空或超长
    """
    if not isinstance(raw_text, str):
        raise ValueError("简历原文必须是字符串")

    stripped = raw_text.strip()
    if not stripped:
        raise ValueError("解析后的简历原文为空")

    if len(stripped) > RESUME_RAW_TEXT_MAX_LENGTH:
        raise ValueError(
            f"解析后简历原文长度 {len(stripped)} 超过上限 "
            f"{RESUME_RAW_TEXT_MAX_LENGTH} 字符"
        )

    return stripped


def validate_resume_upload(
    *,
    filename: str,
    mime_type: str,
    size_bytes: int,
    content: bytes,
) -> dict[str, str | int]:
    """简历上传综合校验

    一站式入口：Service 层上传时调用一次，覆盖全部校验维度。
    校验顺序按「成本从低到高」排列：
    1. 文件名（纯字符串）
    2. MIME 类型（纯字符串）
    3. 文件大小（仅 int 比较）
    4. Magic Number（需读取字节，但仅前 8 字节）

    设计：
    - 不在内存中合并/转换数据：调用方各自传原始值
    - 返回 dict 仅用于日志/审计，不做归一化
    - 失败时立即抛 ValueError，前任校验的错误不会被吞掉

    Args:
        filename: 原始文件名
        mime_type: 客户端声明的 MIME 类型
        size_bytes: 文件字节数（UploadFile.size）
        content: 文件原始字节内容（UploadFile.read()）

    Returns:
        校验结果摘要：包含归一化后的 filename/mime_type 与 file_type 键名

    Raises:
        ValueError: 任一维度校验失败
    """
    # 1. 文件名
    normalized_filename = validate_filename(filename)

    # 2. MIME 类型
    normalized_mime = validate_mime_type(mime_type, filename=normalized_filename)

    # 3. 文件大小
    validate_file_size(size_bytes)

    # 4. Magic Number
    file_type = validate_magic_number(content, filename=normalized_filename)

    return {
        "filename": normalized_filename,
        "mime_type": normalized_mime,
        "file_type": file_type,
        "size_bytes": size_bytes,
    }


__all__ = [
    # 常量
    "ALLOWED_EXTENSIONS",
    "ALLOWED_MIME_TYPES",
    "MIME_TO_EXTENSIONS",
    "MAGIC_NUMBERS",
    "EXTENSION_TO_MAGIC_KEY",
    "MAX_FILE_SIZE_BYTES",
    "MIN_FILE_SIZE_BYTES",
    "MAX_FILENAME_LENGTH",
    "MAGIC_NUMBER_READ_BYTES",
    # 校验器
    "validate_filename",
    "validate_mime_type",
    "validate_file_size",
    "validate_magic_number",
    "validate_parsed_text_length",
    "validate_resume_upload",
]
