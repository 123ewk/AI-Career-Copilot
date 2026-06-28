"""Resume 模块单测

测试覆盖：
- 上传：正常 PDF / DOCX / .doc（旧版） + 异常（文件名 / MIME / 大小 / Magic Number / 解析失败 / 文本超限）
- 解析：作为 upload 的一部分被覆盖（Reader 异常、文本为空、文本超长）
- 查询：get_resume / get_active_resume（缓存命中/未命中）/ list_resumes（分页 / limit-offset 越界）
- 删除：物理删除 + 越权防护
- 切换活跃：set_active_resume + 越权防护

设计动机：
- 用内存版 FakeResumeRepository 替代真实 DB 访问，遵循 ResumeRepositoryProtocol
- 复用 test_resume_cache.py 的 FakeResumeCache
- Mock AsyncSession：仅需提供 commit/rollback 桩方法，不必真连 PostgreSQL
- Validator 在 Service.upload_resume 中抛 ValueError,Service 层捕获后未重新包装
  → 本测试按"实际行为"断言:ValueError 直接冒泡到调用方
  → 同时校验通过 validator 间接覆盖 filename/MIME/size/magic 异常路径

复用：
- _build_pdf_bytes / docx_bytes 复用 test_file_reader.py 的构造方式
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from io import BytesIO
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from docx import Document as DocxDocument
from pypdf import PdfWriter
from pypdf.generic import (
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    ExternalServiceError,
    ResourceNotFoundError,
    ValidationError,
)
from app.domain.resume.models import (
    PARSE_STATUS_PARSED,
    RESUME_RAW_TEXT_MAX_LENGTH,
    ResumeResponse,
    ResumeStructuredData,
    ResumeSummary,
    ResumeUploadResponse,
)
from app.domain.resume.service import ResumeService
from app.infra.database.models.resume import Resume
from app.tools.file import DOCXReader, PDFReader

# ==================== Test Helpers ====================


def _build_pdf_bytes(text: str = "John Doe\n5 years Python/FastAPI experience") -> bytes:
    """构造一个含文字的 PDF 字节流（用 pypdf 底层 API）"""
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)

    font = DictionaryObject()
    font[NameObject("/Type")] = NameObject("/Font")
    font[NameObject("/Subtype")] = NameObject("/Type1")
    font[NameObject("/BaseFont")] = NameObject("/Helvetica")
    fonts = DictionaryObject()
    fonts[NameObject("/F1")] = font
    resources = DictionaryObject()
    resources[NameObject("/Font")] = fonts
    page[NameObject("/Resources")] = resources

    line_ops = b"".join(
        f"({line}) Tj 0 -20 Td ".encode("latin-1") for line in text.split("\n")
    )
    content_bytes = b"BT /F1 14 Tf 50 750 Td " + line_ops + b"ET"
    stream = DecodedStreamObject()
    stream.set_data(content_bytes)
    content_ref = writer._add_object(stream)
    page[NameObject("/Contents")] = content_ref

    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _build_docx_bytes(text: str = "Jane Smith\nSenior Backend Engineer") -> bytes:
    """构造一个含段落的 DOCX 字节流"""
    doc = DocxDocument()
    for line in text.split("\n"):
        doc.add_paragraph(line)
    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ==================== Fake Repository ====================


class FakeResumeRepository:
    """内存版 ResumeRepository,实现 ResumeRepositoryProtocol

    特点：
    - 完整模拟 CRUD:create / get_by_id / get_active_by_user / list_by_user /
      count_by_user / update / set_active / delete / delete_by_id
    - 用 dict 存储,支持多用户隔离
    - 记录每次调用,便于断言 Service 行为
    """

    def __init__(self) -> None:
        self.store: dict[uuid.UUID, Resume] = {}
        # set_active 触发的"简历不属于该用户"开关
        self.raise_value_error_on_set_active: bool = False

    def _make_resume(
        self,
        *,
        user_id: uuid.UUID,
        raw_text: str,
        is_active: bool,
    ) -> Resume:
        """构造一个 Resume ORM 实例(无需 session)"""
        resume = Resume(
            id=uuid.uuid4(),
            user_id=user_id,
            raw_text=raw_text,
            structured_data={},
            skills=[],
            experience_years=None,
            is_active=is_active,
        )
        # created_at 在生产环境由 PG now() 填充,测试中显式注入
        resume.created_at = datetime(2026, 6, 17, 10, 0, 0)  # type: ignore[assignment]
        return resume

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        raw_text: str,
        structured_data: dict | list | None = None,
        skills: list[str] | None = None,
        experience_years: int | None = None,
        is_active: bool = True,
    ) -> Resume:
        if is_active:
            # 同事务内批量取消该用户其他活跃简历
            for r in self.store.values():
                if r.user_id == user_id and r.is_active:
                    r.is_active = False
        resume = self._make_resume(
            user_id=user_id,
            raw_text=raw_text,
            is_active=is_active,
        )
        if structured_data is not None:
            resume.structured_data = structured_data
        if skills is not None:
            resume.skills = skills
        if experience_years is not None:
            resume.experience_years = experience_years
        self.store[resume.id] = resume
        return resume

    async def get_by_id(self, resume_id: uuid.UUID) -> Resume | None:
        return self.store.get(resume_id)

    async def get_active_by_user(self, user_id: uuid.UUID) -> Resume | None:
        for r in self.store.values():
            if r.user_id == user_id and r.is_active:
                return r
        return None

    async def list_by_user(
        self,
        user_id: uuid.UUID,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> Sequence[Resume]:
        if limit <= 0:
            return []
        rows = [r for r in self.store.values() if r.user_id == user_id]
        # 按 created_at 倒序
        rows.sort(key=lambda r: r.created_at, reverse=True)
        return rows[offset : offset + limit]

    async def count_by_user(self, user_id: uuid.UUID) -> int:
        return sum(1 for r in self.store.values() if r.user_id == user_id)

    async def update(
        self,
        resume: Resume,
        *,
        structured_data: dict | list | None = None,
        skills: list[str] | None = None,
        experience_years: int | None = None,
    ) -> Resume:
        if structured_data is not None:
            resume.structured_data = structured_data
        if skills is not None:
            resume.skills = skills
        if experience_years is not None:
            resume.experience_years = experience_years
        return resume

    async def set_active(self, user_id: uuid.UUID, resume_id: uuid.UUID) -> Resume:
        if self.raise_value_error_on_set_active:
            raise ValueError(f"简历 {resume_id} 不存在或不属于用户 {user_id}")
        resume = self.store.get(resume_id)
        if resume is None or resume.user_id != user_id:
            raise ValueError(f"简历 {resume_id} 不存在或不属于用户 {user_id}")
        for r in self.store.values():
            if r.user_id == user_id and r.is_active:
                r.is_active = False
        resume.is_active = True
        return resume

    async def delete(self, resume: Resume) -> None:
        self.store.pop(resume.id, None)

    async def delete_by_id(self, resume_id: uuid.UUID) -> bool:
        return self.store.pop(resume_id, None) is not None


# ==================== Fake Cache ====================


class FakeResumeCache:
    """内存版 ResumeCache,实现 ResumeCacheProtocol

    复用 test_resume_cache.py 的实现思路,记录调用次数,便于断言
    """

    def __init__(self) -> None:
        self.store: dict[uuid.UUID, ResumeResponse] = {}
        self.get_count: int = 0
        self.set_count: int = 0
        self.invalidate_count: int = 0
        # 模拟"缓存未命中":下一次 get_active 返回 None
        self.force_miss: bool = False

    async def get_active(self, user_id: uuid.UUID) -> ResumeResponse | None:
        self.get_count += 1
        if self.force_miss:
            self.force_miss = False
            return None
        return self.store.get(user_id)

    async def set_active(
        self,
        user_id: uuid.UUID,
        resume: ResumeResponse,
    ) -> None:
        self.set_count += 1
        self.store[user_id] = resume

    async def invalidate_active(self, user_id: uuid.UUID) -> None:
        self.invalidate_count += 1
        self.store.pop(user_id, None)


# ==================== Fixtures ====================


@pytest.fixture
def fake_repo() -> FakeResumeRepository:
    return FakeResumeRepository()


@pytest.fixture
def fake_cache() -> FakeResumeCache:
    return FakeResumeCache()


@pytest.fixture
def mock_session() -> AsyncSession:
    """Mock 一个 AsyncSession：仅提供 commit/rollback 桩方法"""
    session = MagicMock(spec=AsyncSession)
    session.commit = AsyncMock(return_value=None)
    session.rollback = AsyncMock(return_value=None)
    session.flush = AsyncMock(return_value=None)
    return session


@pytest.fixture
def service(
    fake_repo: FakeResumeRepository,
    fake_cache: FakeResumeCache,
    mock_session: AsyncSession,
) -> ResumeService:
    """构造一个使用 fake repo + fake cache 的 ResumeService"""
    svc = ResumeService(
        session=mock_session,
        repo=fake_repo,
        cache=fake_cache,
    )
    return svc


@pytest.fixture
def user_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def pdf_bytes() -> bytes:
    return _build_pdf_bytes()


@pytest.fixture
def docx_bytes() -> bytes:
    return _build_docx_bytes()


# ==================== Upload: 正常路径 ====================


class TestUploadResumeNormal:
    """upload_resume 正常路径：PDF / DOCX"""

    @pytest.mark.asyncio
    async def test_upload_pdf_success(
        self,
        service: ResumeService,
        fake_repo: FakeResumeRepository,
        fake_cache: FakeResumeCache,
        user_id: uuid.UUID,
        pdf_bytes: bytes,
    ) -> None:
        """正常上传 PDF：解析成功 + 入库 + 缓存失效"""
        result = await service.upload_resume(
            user_id=user_id,
            filename="resume.pdf",
            mime_type="application/pdf",
            content=pdf_bytes,
        )

        assert isinstance(result, ResumeUploadResponse)
        assert result.parse_status == PARSE_STATUS_PARSED
        assert result.message is None
        assert result.resume.raw_text  # 非空
        assert result.resume.is_active is True
        assert result.resume.user_id == user_id

        # 入库断言
        assert len(fake_repo.store) == 1
        # 缓存被失效（DEL 幂等）
        assert fake_cache.invalidate_count == 1

    @pytest.mark.asyncio
    async def test_upload_docx_success(
        self,
        service: ResumeService,
        fake_repo: FakeResumeRepository,
        user_id: uuid.UUID,
        docx_bytes: bytes,
    ) -> None:
        """正常上传 DOCX：解析成功 + 入库"""
        result = await service.upload_resume(
            user_id=user_id,
            filename="resume.docx",
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            content=docx_bytes,
        )

        assert result.parse_status == PARSE_STATUS_PARSED
        assert result.resume.is_active is True
        assert "Jane Smith" in result.resume.raw_text
        assert len(fake_repo.store) == 1

    @pytest.mark.asyncio
    async def test_upload_new_resume_deactivates_old(
        self,
        service: ResumeService,
        fake_repo: FakeResumeRepository,
        user_id: uuid.UUID,
        pdf_bytes: bytes,
    ) -> None:
        """同一用户重复上传：旧活跃简历自动取消,新简历激活"""
        # 第一次上传
        first = await service.upload_resume(
            user_id=user_id,
            filename="r1.pdf",
            mime_type="application/pdf",
            content=pdf_bytes,
        )
        # 第二次上传
        second = await service.upload_resume(
            user_id=user_id,
            filename="r2.pdf",
            mime_type="application/pdf",
            content=pdf_bytes,
        )

        # 旧简历应已取消
        old_resume = await fake_repo.get_by_id(first.resume.id)
        assert old_resume is not None
        assert old_resume.is_active is False
        # 新简历激活
        new_resume = await fake_repo.get_by_id(second.resume.id)
        assert new_resume is not None
        assert new_resume.is_active is True
        # 该用户仅一条活跃
        active_count = sum(
            1 for r in fake_repo.store.values() if r.user_id == user_id and r.is_active
        )
        assert active_count == 1


# ==================== Upload: 异常路径 ====================


class TestUploadResumeException:
    """upload_resume 异常路径：filename / MIME / size / magic / parse"""

    @pytest.mark.asyncio
    async def test_upload_invalid_filename_raises(
        self,
        service: ResumeService,
        user_id: uuid.UUID,
        pdf_bytes: bytes,
    ) -> None:
        """文件名非法（含路径分隔符）→ ValueError"""
        with pytest.raises(ValueError, match="路径分隔符"):
            await service.upload_resume(
                user_id=user_id,
                filename="../../etc/passwd.pdf",
                mime_type="application/pdf",
                content=pdf_bytes,
            )

    @pytest.mark.asyncio
    async def test_upload_unsupported_extension_raises(
        self,
        service: ResumeService,
        user_id: uuid.UUID,
    ) -> None:
        """不支持的扩展名（.txt）→ ValueError"""
        with pytest.raises(ValueError, match="不支持的简历文件类型"):
            await service.upload_resume(
                user_id=user_id,
                filename="resume.txt",
                mime_type="text/plain",
                content=b"some content",
            )

    @pytest.mark.asyncio
    async def test_upload_mime_extension_mismatch_raises(
        self,
        service: ResumeService,
        user_id: uuid.UUID,
        pdf_bytes: bytes,
    ) -> None:
        """MIME 与扩展名不一致（.pdf + application/msword）→ ValueError"""
        with pytest.raises(ValueError, match="不匹配"):
            await service.upload_resume(
                user_id=user_id,
                filename="resume.pdf",
                mime_type="application/msword",
                content=pdf_bytes,
            )

    @pytest.mark.asyncio
    async def test_upload_unsupported_mime_raises(
        self,
        service: ResumeService,
        user_id: uuid.UUID,
    ) -> None:
        """MIME 类型不在白名单 → ValueError"""
        with pytest.raises(ValueError, match="不支持的 MIME"):
            await service.upload_resume(
                user_id=user_id,
                filename="resume.pdf",
                mime_type="application/x-msdownload",
                content=b"%PDF-1.4 fake",
            )

    @pytest.mark.asyncio
    async def test_upload_empty_content_raises(
        self,
        service: ResumeService,
        user_id: uuid.UUID,
    ) -> None:
        """空文件（0 字节）→ ValueError"""
        with pytest.raises(ValueError, match="文件内容为空"):
            await service.upload_resume(
                user_id=user_id,
                filename="empty.pdf",
                mime_type="application/pdf",
                content=b"",
            )

    @pytest.mark.asyncio
    async def test_upload_oversized_raises(
        self,
        service: ResumeService,
        user_id: uuid.UUID,
    ) -> None:
        """文件 > 10MB → ValueError"""
        big = b"%PDF-1.4 " + b"x" * (10 * 1024 * 1024 + 1)
        with pytest.raises(ValueError, match="不能超过"):
            await service.upload_resume(
                user_id=user_id,
                filename="big.pdf",
                mime_type="application/pdf",
                content=big,
            )

    @pytest.mark.asyncio
    async def test_upload_magic_number_mismatch_raises(
        self,
        service: ResumeService,
        user_id: uuid.UUID,
    ) -> None:
        """Magic Number 与扩展名不符（.pdf 但内容是 docx 头）→ ValueError"""
        # 文档头是 PK (docx 的 magic),但扩展名是 .pdf
        fake_pdf_content = b"PK\x03\x04" + b"x" * 100
        with pytest.raises(ValueError, match="不匹配"):
            await service.upload_resume(
                user_id=user_id,
                filename="fake.pdf",
                mime_type="application/pdf",
                content=fake_pdf_content,
            )

    @pytest.mark.asyncio
    async def test_upload_doc_format_rejected(
        self,
        service: ResumeService,
        user_id: uuid.UUID,
    ) -> None:
        """旧版 .doc → ValidationError(VAL_023,旧版格式不支持)"""
        # 合法 OLE 头,通过 Magic Number 校验
        ole_header = b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1" + b"x" * 100
        with pytest.raises(ValidationError) as exc_info:
            await service.upload_resume(
                user_id=user_id,
                filename="legacy.doc",
                mime_type="application/msword",
                content=ole_header,
            )
        assert exc_info.value.error_code == "VAL_023"

    @pytest.mark.asyncio
    async def test_upload_corrupt_pdf_raises(
        self,
        service: ResumeService,
        user_id: uuid.UUID,
    ) -> None:
        """PDF 文件头合法但内容损坏 → ExternalServiceError"""
        # %PDF 头合法,但后续内容不是 PDF,Reader 解析失败
        corrupt = b"%PDF-1.4 " + b"not a real pdf" * 50
        with pytest.raises(ExternalServiceError) as exc_info:
            await service.upload_resume(
                user_id=user_id,
                filename="corrupt.pdf",
                mime_type="application/pdf",
                content=corrupt,
            )
        assert exc_info.value.status_code == 502

    @pytest.mark.asyncio
    async def test_upload_corrupt_docx_raises(
        self,
        service: ResumeService,
        user_id: uuid.UUID,
    ) -> None:
        """DOCX 文件头合法但内容损坏 → ExternalServiceError"""
        # PK 头合法,但内容不是合法 zip
        corrupt = b"PK\x03\x04" + b"not a real docx" * 50
        with pytest.raises(ExternalServiceError) as exc_info:
            await service.upload_resume(
                user_id=user_id,
                filename="corrupt.docx",
                mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                content=corrupt,
            )
        assert exc_info.value.status_code == 502

    @pytest.mark.asyncio
    async def test_upload_oversized_parsed_text_raises(
        self,
        service: ResumeService,
        user_id: uuid.UUID,
    ) -> None:
        """解析后文本超过 RESUME_RAW_TEXT_MAX_LENGTH → ValidationError(VAL_020)"""
        # 构造一个超长文本的真实 PDF
        huge_text = "A" * (RESUME_RAW_TEXT_MAX_LENGTH + 100)
        big_pdf = _build_pdf_bytes(text=huge_text)
        with pytest.raises(ValidationError) as exc_info:
            await service.upload_resume(
                user_id=user_id,
                filename="huge.pdf",
                mime_type="application/pdf",
                content=big_pdf,
            )
        assert exc_info.value.error_code == "VAL_020"

    @pytest.mark.asyncio
    async def test_upload_integrity_error_propagates(
        self,
        service: ResumeService,
        fake_repo: FakeResumeRepository,
        user_id: uuid.UUID,
        pdf_bytes: bytes,
    ) -> None:
        """Repository.create 抛 IntegrityError → 冒泡,Service 触发 rollback"""
        # 让 fake repo 抛 IntegrityError 模拟极端并发场景
        async def _raise(*_args: Any, **_kwargs: Any) -> None:
            raise IntegrityError("INSERT", {}, Exception("uq_resumes_user_active"))

        service._repo.create = _raise  # type: ignore[assignment, method-assign]

        with pytest.raises(IntegrityError):
            await service.upload_resume(
                user_id=user_id,
                filename="r.pdf",
                mime_type="application/pdf",
                content=pdf_bytes,
            )
        # Service 应该在异常时 rollback
        service._session.rollback.assert_called_once()  # type: ignore[attr-defined]


# ==================== Get Resume: 正常 + 异常 ====================


class TestGetResume:
    """get_resume: 按 ID 查询 + 跨用户隔离"""

    @pytest.mark.asyncio
    async def test_get_resume_success(
        self,
        service: ResumeService,
        fake_repo: FakeResumeRepository,
        user_id: uuid.UUID,
    ) -> None:
        """正常路径:本人查询自己的简历"""
        resume = await fake_repo.create(
            user_id=user_id,
            raw_text="Hello World",
        )
        result = await service.get_resume(
            user_id=user_id,
            resume_id=resume.id,
        )
        assert isinstance(result, ResumeResponse)
        assert result.id == resume.id
        assert result.user_id == user_id

    @pytest.mark.asyncio
    async def test_get_resume_not_found_raises(
        self,
        service: ResumeService,
        user_id: uuid.UUID,
    ) -> None:
        """简历 ID 不存在 → ResourceNotFoundError"""
        with pytest.raises(ResourceNotFoundError):
            await service.get_resume(
                user_id=user_id,
                resume_id=uuid.uuid4(),
            )

    @pytest.mark.asyncio
    async def test_get_resume_cross_user_raises(
        self,
        service: ResumeService,
        fake_repo: FakeResumeRepository,
        user_id: uuid.UUID,
    ) -> None:
        """越权访问：用户 A 查询用户 B 的简历 → ResourceNotFoundError(防枚举)"""
        other_user = uuid.uuid4()
        other_resume = await fake_repo.create(
            user_id=other_user,
            raw_text="Other user's resume",
        )
        with pytest.raises(ResourceNotFoundError):
            await service.get_resume(
                user_id=user_id,  # 当前用户
                resume_id=other_resume.id,  # 别人的简历 ID
            )


# ==================== Get Active Resume: 缓存命中 / 未命中 ====================


class TestGetActiveResume:
    """get_active_resume: Cache-Aside 模式"""

    @pytest.mark.asyncio
    async def test_get_active_miss_then_db_then_cache_fill(
        self,
        service: ResumeService,
        fake_repo: FakeResumeRepository,
        fake_cache: FakeResumeCache,
        user_id: uuid.UUID,
    ) -> None:
        """缓存未命中 → 走 DB → 回填缓存"""
        await fake_repo.create(user_id=user_id, raw_text="Active")

        result = await service.get_active_resume(user_id=user_id)

        assert result is not None
        assert result.is_active is True
        # 缓存被回填
        assert fake_cache.get_count == 1
        assert fake_cache.set_count == 1

    @pytest.mark.asyncio
    async def test_get_active_cache_hit_skips_db(
        self,
        service: ResumeService,
        fake_repo: FakeResumeRepository,
        fake_cache: FakeResumeCache,
        user_id: uuid.UUID,
    ) -> None:
        """缓存命中 → 直接返回,不走 DB"""
        # 预填缓存
        cached = ResumeResponse(
            id=uuid.uuid4(),
            user_id=user_id,
            raw_text="from cache",
            structured_data=ResumeStructuredData(),
            skills=[],
            experience_years=None,
            is_active=True,
            created_at=datetime(2026, 6, 17, 10, 0, 0),  # type: ignore[arg-type]
        )
        await fake_cache.set_active(user_id, cached)

        result = await service.get_active_resume(user_id=user_id)

        assert result is not None
        assert result.raw_text == "from cache"
        # 不应 set_active(已命中)
        assert fake_cache.set_count == 1  # 仅预填时的那次

    @pytest.mark.asyncio
    async def test_get_active_returns_none_when_no_resume(
        self,
        service: ResumeService,
        user_id: uuid.UUID,
    ) -> None:
        """无活跃简历 → 返回 None(不抛异常)"""
        result = await service.get_active_resume(user_id=user_id)
        assert result is None

    @pytest.mark.asyncio
    async def test_get_active_miss_with_no_resume_doesnt_set_cache(
        self,
        service: ResumeService,
        fake_cache: FakeResumeCache,
        user_id: uuid.UUID,
    ) -> None:
        """缓存未命中 + DB 无数据 → 不应回填缓存"""
        await service.get_active_resume(user_id=user_id)
        # 未命中 + 无 DB 数据 → 不调用 set
        assert fake_cache.set_count == 0


# ==================== List Resumes: 分页 + 越界 ====================


class TestListResumes:
    """list_resumes: 分页查询 + 边界校验"""

    @pytest.mark.asyncio
    async def test_list_resumes_empty(
        self,
        service: ResumeService,
        user_id: uuid.UUID,
    ) -> None:
        """无简历 → 返回空列表 + total=0"""
        summaries, total = await service.list_resumes(user_id=user_id)
        assert summaries == []
        assert total == 0

    @pytest.mark.asyncio
    async def test_list_resumes_pagination(
        self,
        service: ResumeService,
        fake_repo: FakeResumeRepository,
        user_id: uuid.UUID,
    ) -> None:
        """分页:5 条简历,limit=2 → 返回 2 条,total=5"""
        for i in range(5):
            await fake_repo.create(
                user_id=user_id,
                raw_text=f"Resume {i}",
            )
        summaries, total = await service.list_resumes(
            user_id=user_id, limit=2, offset=0,
        )
        assert total == 5
        assert len(summaries) == 2
        assert all(isinstance(s, ResumeSummary) for s in summaries)
        # 不应返回 raw_text(ResumeSummary 字段裁剪)
        assert not hasattr(summaries[0], "raw_text")

    @pytest.mark.asyncio
    async def test_list_resumes_only_returns_owner(
        self,
        service: ResumeService,
        fake_repo: FakeResumeRepository,
        user_id: uuid.UUID,
    ) -> None:
        """跨用户隔离:仅返回当前用户的简历"""
        other_user = uuid.uuid4()
        await fake_repo.create(user_id=user_id, raw_text="mine")
        await fake_repo.create(user_id=other_user, raw_text="other's")

        summaries, total = await service.list_resumes(user_id=user_id)
        assert total == 1
        # ResumeSummary 不含 user_id 字段（已剥离），仅返回 1 条
        assert len(summaries) == 1

    @pytest.mark.asyncio
    async def test_list_resumes_limit_too_small_raises(
        self,
        service: ResumeService,
        user_id: uuid.UUID,
    ) -> None:
        """limit <= 0 → ValidationError(VAL_021)"""
        with pytest.raises(ValidationError) as exc_info:
            await service.list_resumes(user_id=user_id, limit=0)
        assert exc_info.value.error_code == "VAL_021"

    @pytest.mark.asyncio
    async def test_list_resumes_limit_too_big_raises(
        self,
        service: ResumeService,
        user_id: uuid.UUID,
    ) -> None:
        """limit > 100 → ValidationError(VAL_021)"""
        with pytest.raises(ValidationError) as exc_info:
            await service.list_resumes(user_id=user_id, limit=101)
        assert exc_info.value.error_code == "VAL_021"

    @pytest.mark.asyncio
    async def test_list_resumes_negative_offset_raises(
        self,
        service: ResumeService,
        user_id: uuid.UUID,
    ) -> None:
        """offset < 0 → ValidationError(VAL_022)"""
        with pytest.raises(ValidationError) as exc_info:
            await service.list_resumes(user_id=user_id, offset=-1)
        assert exc_info.value.error_code == "VAL_022"


# ==================== Set Active: 越权防护 + 缓存失效 ====================


class TestSetActiveResume:
    """set_active_resume: 切换活跃 + 越权防护"""

    @pytest.mark.asyncio
    async def test_set_active_success(
        self,
        service: ResumeService,
        fake_repo: FakeResumeRepository,
        fake_cache: FakeResumeCache,
        user_id: uuid.UUID,
    ) -> None:
        """正常切换:另一条非活跃简历被激活,缓存被失效"""
        # 创建两条简历
        first = await fake_repo.create(user_id=user_id, raw_text="first")
        # 创建第二条简历触发「自动取消旧活跃」逻辑
        await fake_repo.create(user_id=user_id, raw_text="second")
        # 此时 second 是 active,first 被自动取消

        # 切换到 first
        result = await service.set_active_resume(
            user_id=user_id, resume_id=first.id,
        )
        assert result.is_active is True
        assert result.id == first.id
        # 缓存被失效
        assert fake_cache.invalidate_count >= 1

    @pytest.mark.asyncio
    async def test_set_active_cross_user_raises(
        self,
        service: ResumeService,
        fake_repo: FakeResumeRepository,
        user_id: uuid.UUID,
    ) -> None:
        """越权切换:用户 A 试图激活用户 B 的简历 → ResourceNotFoundError"""
        other_user = uuid.uuid4()
        other_resume = await fake_repo.create(
            user_id=other_user, raw_text="other",
        )
        with pytest.raises(ResourceNotFoundError):
            await service.set_active_resume(
                user_id=user_id, resume_id=other_resume.id,
            )

    @pytest.mark.asyncio
    async def test_set_active_not_found_raises(
        self,
        service: ResumeService,
        user_id: uuid.UUID,
    ) -> None:
        """简历 ID 不存在 → ResourceNotFoundError"""
        with pytest.raises(ResourceNotFoundError):
            await service.set_active_resume(
                user_id=user_id, resume_id=uuid.uuid4(),
            )


# ==================== Delete: 物理删除 + 越权防护 ====================


class TestDeleteResume:
    """delete_resume: 物理删除 + 越权防护"""

    @pytest.mark.asyncio
    async def test_delete_success(
        self,
        service: ResumeService,
        fake_repo: FakeResumeRepository,
        fake_cache: FakeResumeCache,
        user_id: uuid.UUID,
    ) -> None:
        """正常删除:简历从 store 移除,缓存被失效"""
        resume = await fake_repo.create(user_id=user_id, raw_text="x")
        await fake_cache.set_active(user_id, ResumeResponse.model_validate(resume))

        await service.delete_resume(user_id=user_id, resume_id=resume.id)

        assert resume.id not in fake_repo.store
        # 缓存被失效
        assert fake_cache.invalidate_count == 1

    @pytest.mark.asyncio
    async def test_delete_cross_user_raises(
        self,
        service: ResumeService,
        fake_repo: FakeResumeRepository,
        user_id: uuid.UUID,
    ) -> None:
        """越权删除:用户 A 试图删除用户 B 的简历 → ResourceNotFoundError"""
        other_user = uuid.uuid4()
        other_resume = await fake_repo.create(
            user_id=other_user, raw_text="x",
        )
        with pytest.raises(ResourceNotFoundError):
            await service.delete_resume(
                user_id=user_id, resume_id=other_resume.id,
            )
        # 简历应仍在 store
        assert other_resume.id in fake_repo.store

    @pytest.mark.asyncio
    async def test_delete_not_found_raises(
        self,
        service: ResumeService,
        user_id: uuid.UUID,
    ) -> None:
        """简历 ID 不存在 → ResourceNotFoundError"""
        with pytest.raises(ResourceNotFoundError):
            await service.delete_resume(
                user_id=user_id, resume_id=uuid.uuid4(),
            )


# ==================== Fill Structured Data (Agent 回填) ====================


class TestFillStructuredData:
    """fill_structured_data: Agent 解析完成后回填结构化数据"""

    @pytest.mark.asyncio
    async def test_fill_success(
        self,
        service: ResumeService,
        fake_repo: FakeResumeRepository,
        fake_cache: FakeResumeCache,
        user_id: uuid.UUID,
    ) -> None:
        """正常回填:structured_data / skills / experience_years 全部更新"""
        resume = await fake_repo.create(user_id=user_id, raw_text="x")
        new_data = {
            "education": [{"school": "MIT"}],
            "experience": [],
            "projects": [],
        }

        result = await service.fill_structured_data(
            user_id=user_id,
            resume_id=resume.id,
            structured_data=new_data,
            skills=["Python", "Go"],
            experience_years=5,
        )

        assert result.structured_data.education[0].school == "MIT"
        assert result.skills == ["Python", "Go"]
        assert result.experience_years == 5
        # 缓存被失效
        assert fake_cache.invalidate_count == 1

    @pytest.mark.asyncio
    async def test_fill_skills_normalization(
        self,
        service: ResumeService,
        fake_repo: FakeResumeRepository,
        user_id: uuid.UUID,
    ) -> None:
        """回填时 skills 也走归一化:去空 / 去重 / strip"""
        resume = await fake_repo.create(user_id=user_id, raw_text="x")
        result = await service.fill_structured_data(
            user_id=user_id,
            resume_id=resume.id,
            structured_data={},
            skills=["  Python  ", "", "Python", "Go", "  "],
            experience_years=None,
        )
        # 空串被过滤、重复被去重、空白被 strip
        assert result.skills == ["Python", "Go"]

    @pytest.mark.asyncio
    async def test_fill_cross_user_raises(
        self,
        service: ResumeService,
        fake_repo: FakeResumeRepository,
        user_id: uuid.UUID,
    ) -> None:
        """越权回填:用户 A 试图改用户 B 的简历 → ResourceNotFoundError"""
        other_user = uuid.uuid4()
        other_resume = await fake_repo.create(
            user_id=other_user, raw_text="x",
        )
        with pytest.raises(ResourceNotFoundError):
            await service.fill_structured_data(
                user_id=user_id,
                resume_id=other_resume.id,
                structured_data={},
                skills=["x"],
                experience_years=1,
            )
