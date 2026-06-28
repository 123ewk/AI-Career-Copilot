"""Resume 域 Service

职责：
- 编排「上传 → 解析 → 存储 → 查询」简历全生命周期
- 接收 Router 传入的字节流 + 文件元信息,完成业务闭环
- 翻译底层异常(IntegrityError 等)为业务异常,屏蔽 ORM 细节
- 协调 active resume 的 Redis 缓存(读穿透 + 写后失效)

设计动机：
- 业务层(Pydantic DTO)只做字段格式校验,本层做"业务级"校验
  · 文件名 / MIME / 大小 / Magic Number → 复用 validator.py
  · 跨用户隔离(越权防护) → 在 Service 层强制
  · skills 归一化(去重/去空) → 在 Service 层做,DTO 不做
- Repository 仅做数据访问:不负责解析、提交、异常翻译
  · Service 显式 commit,事务边界清晰
  · Service 显式 rollback,业务失败时回滚
- 解析复用 tools/file 下的 PDFReader / DOCXReader
  · 不重复实现 PDF/DOCX 文本提取
  · Agent 视角的"读简历"通过 tools/file/resume_reader.py 查询 DB,
    不再二次解析文件
- 结构化数据(structured_data)由 Agent 后续解析填充
  · 本 Service 只存 raw_text,structured_data 留空 {}
  · Agent 解析完成后通过 fill_structured_data() 回填
- 缓存层独立于 Repository(由 Protocol 抽象):
  · Service 协调"先查 Redis,miss 走 DB,写后失效 Redis"
  · Cache-Aside 模式 + fail-open(Redis 异常不抛,降级到 DB)
  · 当前只缓存 active resume(list / detail 暂不缓存,失效复杂且命中率低)

业务流程:
1. upload_resume():
   校验(filename/mime/size/magic) → 选 Reader 解析 → 归一化 skills →
   Repository.create(is_active=True) → commit → invalidate cache → 返回 ResumeUploadResponse
2. get_resume():  按 ID 查询 + 跨用户隔离(防越权)【不走缓存】
3. get_active_resume(): cache.get → miss 走 DB → cache.set 回填
4. list_resumes(): 分页 + 返回 total【不走缓存】
5. set_active_resume(): 切换活跃(Repository 内部保证同用户最多一条) → invalidate cache
6. delete_resume(): 物理删除 → invalidate cache
7. Agent 域若需回填结构化数据(LLM 抽取后):
   通过 fill_structured_data() 内部方法直接调 Repository.update
   · 不暴露 DTO,避免 API 误用
   · 保持 raw_text / is_active / 所有权校验仍由 Service 掌控
   · 完成后 invalidate cache(若该简历是 active)

潜在风险：
- 解析失败时 raw_text 不可用:本 Service 要求"解析成功才入库"
  · 若未来需要"先存原文、异步解析",应拆出 Background Task
  · 当前实现是同步解析,失败即抛错(用户体验更可控)
- 跨用户越权:REST 层用 user_id 隔离,Service 层用 _ensure_resume_owner 二次防御
  · 深度防御:即使 Router 漏传 user_id,Service 仍按 user_id 过滤
- 上传/删除/切换活跃是三个写入口:
  · 上传 → 创建并自动激活(取消旧活跃)
  · 切换 → set_active
  · 删除 → 物理删除(若删的是活跃简历,该用户将暂时无活跃简历)
- 解析与 LLM 抽取解耦:
  · Service 不调用 LLM,只存 raw_text
  · 抽取由 Agent 域负责,通过 _fill_structured_data() 内部方法回填
  · 内部方法不做 DTO 校验,仅做所有权 + 字段归一化(深度防御)
- 缓存一致性(写后失效):
  · "写后失效"与"读穿透"之间存在微秒级窗口,可能短暂返回陈旧数据
  · 30 分钟 TTL 兜底,业务上简历变更不频繁,可接受
  · 不引入 Pub/Sub 广播失效(复杂度上不值,等真出现一致性问题再加)
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    ExternalServiceError,
    ResourceNotFoundError,
    ValidationError,
)
from app.core.logger import logger
from app.domain.cache.resume import ResumeCacheProtocol
from app.domain.repositories.resume import ResumeRepositoryProtocol
from app.domain.resume.models import (
    PARSE_STATUS_PARSED,
    RESUME_SKILLS_MAX_LENGTH,
    ResumeResponse,
    ResumeStructuredData,
    ResumeSummary,
    ResumeUploadResponse,
)
from app.domain.resume.validator import (
    EXTENSION_TO_MAGIC_KEY,
    validate_parsed_text_length,
    validate_resume_upload,
)
from app.infra.database.models.resume import Resume
from app.tools.file.docx_reader import DOCXReader
from app.tools.file.pdf_reader import PDFReader

# ==================== 常量 ====================

# 旧版 .doc 暂不支持(无成熟 Python 解析器,易触发 OLE 解码错误)
# 提示用户转换为 .docx 是更稳妥的方案
_UNSUPPORTED_DOC_MESSAGE: str = (
    "暂不支持旧版 .doc 格式,请将文件另存为 .docx 后重新上传"
)


# ==================== Service ====================

class ResumeService:
    """简历域 Service

    使用方式:
        session = pg_session_factory.create_session()
        service = ResumeService(session)
        response = await service.upload_resume(
            user_id=...,
            filename=...,
            mime_type=...,
            content=...,
        )
        await session.close()  # 框架保证:get_db_session 的 finally 会关闭

    设计原则：
    - 单实例对应一个请求：构造时注入 session,所有操作共用同一事务
    - Repository / Cache 通过构造函数注入:None 时使用默认 Infra 实现,
      测试可替换为 FakeRepository / FakeResumeCache
    - Reader 无状态,内部实例化即可
    - 显式 commit:业务成功后由 Service 显式 commit,便于业务层回滚控制
    - 异常翻译:DB 异常(IntegrityError)→ 业务异常(ResourceNotFoundError 等)
    - 跨用户隔离:所有读/写接口都接收 user_id,Repository 层做 ownership 过滤
    - 缓存策略:active resume 走 Cache-Aside(读时先查 Redis,写后失效 Redis)
      · 缓存实现依赖 Protocol,测试可替换为 FakeResumeCache
      · Redis 异常一律 fail-open,降级到 DB
    """

    def __init__(
        self,
        session: AsyncSession,
        repo: ResumeRepositoryProtocol | None = None,
        cache: ResumeCacheProtocol | None = None,
    ) -> None:
        """初始化 Service

        Args:
            session: 异步数据库 session（单次请求共用一个事务）
            repo: 简历仓储实现。None 时默认用 ResumeRepository
            cache: 简历缓存实现。None 时默认用 RedisResumeCache
                   · 生产环境:走全局 Redis 单例
                   · 测试环境:可传 FakeResumeCache（内存 dict）实现 Protocol
        """
        self._session = session
        if repo is None:
            # 延迟导入具体实现,避免 Domain 模块顶层依赖 Infra
            from app.infra.repositories.resume_repo import ResumeRepository
            repo = ResumeRepository(session)
        # 类型标注为 Protocol,便于测试时替换为 FakeRepository
        self._repo: ResumeRepositoryProtocol = repo

        if cache is None:
            # 延迟导入具体实现,避免 Domain 模块顶层依赖 Infra
            from app.infra.cache.resume import RedisResumeCache
            cache = RedisResumeCache()
        # 缓存:默认走 Redis,测试时可注入 FakeResumeCache
        self._cache: ResumeCacheProtocol = cache
        # Reader 无状态,模块级单例/实例化皆可,实例化更显式
        self._pdf_reader = PDFReader()
        self._docx_reader = DOCXReader()

    # ==================== 上传 → 解析 → 存储 ====================

    async def upload_resume(
        self,
        *,
        user_id: uuid.UUID,
        filename: str,
        mime_type: str,
        content: bytes,
    ) -> ResumeUploadResponse:
        """上传简历:校验 → 解析 → 存储

        完整流程:
        1. 综合校验(validator.validate_resume_upload)
           · 文件名 / MIME / 大小 / Magic Number 全部通过
           · 返回 file_type 标识("pdf" / "docx" / "doc")
        2. 选择 Reader 解析字节流
           · pdf → PDFReader.read_bytes
           · docx → DOCXReader.read_bytes
           · doc → 拒绝(暂不支持)
        3. 解析后文本长度校验(防止恶意 PDF 撑爆 DB)
        4. Repository.create(is_active=True)
           · 内部批量取消该用户其他活跃简历
           · 数据库部分唯一索引 uq_resumes_user_active 兜底并发安全
        5. commit
        6. 转换为 ResumeUploadResponse(状态固定为 PARSED)

        Args:
            user_id: 所属用户 UUID(Router 层从 JWT 注入)
            filename: 原始文件名(含扩展名,不含路径)
            mime_type: 客户端声明的 MIME 类型
            content: 文件原始字节流(UploadFile.read())

        Returns:
            ResumeUploadResponse,内含 ResumeResponse + parse_status=PARSED

        Raises:
            ValidationError: 文件名校验失败 / MIME 不匹配 / 大小越界 / Magic Number 失败
            ExternalServiceError: 解析失败(PDF 损坏 / DOCX 非 zip)
            ResourceNotFoundError: 解析后文本为空(被 validator 拦截)
            DatabaseError: 极端并发下唯一索引冲突(由中间件处理)
        """
        # ---- 1. 综合校验:文件名 / MIME / 大小 / Magic Number ----
        validated = validate_resume_upload(
            filename=filename,
            mime_type=mime_type,
            size_bytes=len(content),
            content=content,
        )
        # validate_resume_upload 返回 dict[str, str | int],file_type 实际为 str
        # 用 cast 收敛类型,避免后续 str 推断被联合类型污染
        file_type: str = validated["file_type"]  # type: ignore[assignment]
        logger.info(
            "简历上传开始 | user_id={} | filename={} | file_type={} | size={}",
            user_id,
            validated["filename"],
            file_type,
            validated["size_bytes"],
        )

        # ---- 2. 选择 Reader 解析字节流 ----
        try:
            raw_text = await self._parse_resume_file(
                content=content,
                file_type=file_type,
            )
        except (ValidationError, ExternalServiceError) as exc:
            # 解析失败:不写入 DB,直接向上抛
            # · ValidationError:Reader 内部 IO 校验失败(空内容/超限)
            # · ExternalServiceError:文件损坏/格式错误
            logger.info(
                "简历解析失败 | user_id={} | file_type={} | error_code={} | detail={}",
                user_id,
                file_type,
                exc.error_code,
                exc.detail,
            )
            raise

        # ---- 3. 解析后文本长度校验 ----
        # 防御:恶意 PDF 可能内嵌 JS / 大量重复内容,解析后文本膨胀
        # 即使 DTO 层有 max_length,Service 层也做兜底(深度防御)
        try:
            raw_text = validate_parsed_text_length(raw_text)
        except ValueError as exc:
            logger.info(
                "简历解析后文本非法 | user_id={} | detail={}",
                user_id,
                str(exc),
            )
            raise ValidationError(
                detail=str(exc),
                error_code="VAL_020",
            ) from exc

        # ---- 4. 创建简历(自动激活,取消旧活跃) ----
        try:
            resume = await self._repo.create(
                user_id=user_id,
                raw_text=raw_text,
                # 结构化数据由 Agent 后续解析填充,本 Service 只存原文
                structured_data=ResumeStructuredData().model_dump(),
                skills=self._normalize_skills(None),
                experience_years=None,
                is_active=True,
            )
            await self._session.commit()
        except IntegrityError as exc:
            # 极端并发场景:两个上传请求同时尝试激活
            # 走 Repository 内部的 batch update 通常可避免
            # 但数据库 uq_resumes_user_active 部分唯一索引仍是最后兜底
            await self._session.rollback()
            logger.error(
                "简历上传失败:DB 唯一约束冲突 | user_id={} | exc={}",
                user_id,
                type(exc).__name__,
            )
            raise

        logger.info(
            "简历上传成功 | user_id={} | resume_id={} | text_len={}",
            user_id,
            resume.id,
            len(raw_text),
        )

        # ---- 5. 失效 active 缓存 ----
        # 上传成功后该用户的 active 指向新简历,缓存里的旧值已失效
        # DEL 幂等:无缓存也不报错;Redis 异常已由 cache 层 swallow
        await self._cache.invalidate_active(user_id)

        # ---- 6. 构造响应(skills 归一化后再读) ----
        return ResumeUploadResponse(
            resume=ResumeResponse.model_validate(resume),
            parse_status=PARSE_STATUS_PARSED,
            message=None,
        )

    # ==================== 查询(单条) ====================

    async def get_resume(
        self,
        *,
        user_id: uuid.UUID,
        resume_id: uuid.UUID,
    ) -> ResumeResponse:
        """按 ID 查询简历(带跨用户隔离)

        越权场景:
        - 用户 A 传用户 B 的 resume_id → 视为"资源不存在"
        - 原因:不泄露"该 ID 存在但不属于你"的信息(防枚举)
        - 抛 ResourceNotFoundError(Router 转 404)

        Args:
            user_id: 当前用户 UUID(从 JWT 注入,不可信度低于 DB 记录)
            resume_id: 目标简历 UUID

        Returns:
            ResumeResponse(完整字段,含 raw_text)

        Raises:
            ResourceNotFoundError: 简历不存在 / 不属于当前用户
        """
        # 把 _ensure_resume_owner 的返回值赋给 resume,让 mypy 推断出非 None
        resume = self._ensure_resume_owner(
            await self._repo.get_by_id(resume_id),
            user_id=user_id,
            resume_id=resume_id,
        )
        return ResumeResponse.model_validate(resume)

    async def get_active_resume(
        self,
        *,
        user_id: uuid.UUID,
    ) -> ResumeResponse | None:
        """查询指定用户的当前活跃简历(带 Redis 缓存)

        未找到返回 None(不是异常):
        - 用户可能从未上传过简历
        - 用户刚删除了所有简历
        - 区分"无活跃简历"和"系统错误":前者是 200 + null,后者是 500

        缓存策略 (Cache-Aside):
        1. 先查 Redis:`resume:active:{user_id}`
        2. 命中 → 直接返回(不走 DB)
        3. 未命中 → 查 DB,构造 Response 后回填 Redis(setex,30 分钟 TTL)
        4. Redis 异常 → logger.warning + 视为未命中,降级到 DB
           (与 rate_limit 的 fail-open 策略一致)

        一致性:
        - 写操作(upload/set_active/delete/fill_structured_data)成功后
          会 invalidate 该用户的 active 缓存,确保下次读时拿到新数据
        - 写后失效与读穿透之间存在微秒级窗口,30 分钟 TTL 兜底

        Args:
            user_id: 用户 UUID

        Returns:
            ResumeResponse 或 None
        """
        # ---- 1. 先查缓存(快速路径) ----
        cached = await self._cache.get_active(user_id)
        if cached is not None:
            return cached

        # ---- 2. 缓存未命中,走 DB ----
        resume = await self._repo.get_active_by_user(user_id)
        if resume is None:
            return None

        # ---- 3. 构造响应并回填缓存 ----
        # 即使 set_active 失败也不影响本次返回:下次读会再次尝试
        response = ResumeResponse.model_validate(resume)
        await self._cache.set_active(user_id, response)
        return response

    # ==================== 查询(列表) ====================

    async def list_resumes(
        self,
        *,
        user_id: uuid.UUID,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[Sequence[ResumeSummary], int]:
        """分页查询用户的所有简历

        返回结构:
        - summaries: 当前页简历摘要(不含 raw_text / structured_data)
        - total: 用户简历总数(用于前端分页器)

        Args:
            user_id: 用户 UUID
            limit: 每页大小(1-100,默认 20)
            offset: 偏移量(≥0,默认 0)

        Returns:
            (summaries, total) 元组

        Raises:
            ValidationError: limit/offset 越界
        """
        # 边界校验:防止恶意 offset 触发慢查询
        if limit <= 0 or limit > 100:
            raise ValidationError(
                detail="limit 必须在 1-100 之间",
                error_code="VAL_021",
                extra={"limit": limit},
            )
        if offset < 0:
            raise ValidationError(
                detail="offset 不能为负数",
                error_code="VAL_022",
                extra={"offset": offset},
            )

        # 并行查询:列表 + 总数
        # 注:本项目用 async session,简单的两次 await 即可,
        # 无需 asyncio.gather(同 session 内串行更安全)
        resumes = await self._repo.list_by_user(
            user_id, limit=limit, offset=offset,
        )
        total = await self._repo.count_by_user(user_id)

        summaries = [ResumeSummary.model_validate(r) for r in resumes]
        return summaries, total

    # ==================== 切换活跃(更换简历) ====================

    async def set_active_resume(
        self,
        *,
        user_id: uuid.UUID,
        resume_id: uuid.UUID,
    ) -> ResumeResponse:
        """切换用户的活跃简历

        语义:
        - 该用户其他 is_active=True 的简历 → 全部置 False
        - 指定 resume_id → 置 True
        - 整个操作在 Repository 内原子完成
        - 数据库部分唯一索引兜底并发安全

        Args:
            user_id: 用户 UUID
            resume_id: 目标简历 UUID(必须属于该用户)

        Returns:
            切换后的 ResumeResponse(is_active=True)

        Raises:
            ResourceNotFoundError: 简历不存在 / 不属于当前用户
        """
        # 所有权预检:防止"简历存在但不属于你"被 Repository 误抛 ValueError
        # 翻译为统一的 ResourceNotFoundError
        # 注意:_ensure_resume_owner 抛错时不会返回值,所以仅用作断言式校验
        self._ensure_resume_owner(
            await self._repo.get_by_id(resume_id),
            user_id=user_id,
            resume_id=resume_id,
        )

        try:
            resume = await self._repo.set_active(user_id, resume_id)
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            logger.error(
                "切换活跃简历失败:DB 唯一约束冲突 | user_id={} | resume_id={} | exc={}",
                user_id,
                resume_id,
                type(exc).__name__,
            )
            raise

        logger.info(
            "切换活跃简历成功 | user_id={} | resume_id={}",
            user_id,
            resume_id,
        )

        # 失效 active 缓存:活跃简历已变更,旧缓存必须丢弃
        await self._cache.invalidate_active(user_id)

        return ResumeResponse.model_validate(resume)

    # ==================== 内部:Agent 回填结构化数据 ====================

    async def fill_structured_data(
        self,
        *,
        user_id: uuid.UUID,
        resume_id: uuid.UUID,
        structured_data: dict | list,
        skills: list[str] | None = None,
        experience_years: int | None = None,
    ) -> ResumeResponse:
        """Agent 回填结构化数据(内部方法,不暴露 API)

        适用场景:
        - Agent 域调用 LLM 完成结构化抽取后,通过本方法回填到 Resume
        - skills / experience_years 同步更新(LLM 抽取时一并得到)
        - 不接受 raw_text / is_active 修改:
          · raw_text 修改应走"重新上传"流程(走 PDF/DOCX 解析)
          · is_active 走 set_active_resume
          · 内部接口约束更强,避免误用

        字段归一化:
        - skills: 与 upload_resume 相同的归一化逻辑(去空/去重/截断)
        - structured_data: 不做归一化(LLM 输出结构可能变化,容忍度高)
        - experience_years: 不做归一化(LLM 输出 0-50 整数)

        Args:
            user_id: 用户 UUID(所有权校验)
            resume_id: 目标简历 UUID
            structured_data: LLM 抽取的结构化数据(教育/工作/项目)
            skills: 技能列表(可空,None 表示不更新)
            experience_years: 工作年限(可空,None 表示不更新)

        Returns:
            更新后的 ResumeResponse

        Raises:
            ResourceNotFoundError: 简历不存在 / 不属于当前用户
        """
        existing = self._ensure_resume_owner(
            await self._repo.get_by_id(resume_id),
            user_id=user_id,
            resume_id=resume_id,
        )

        normalized_skills = (
            self._normalize_skills(skills) if skills is not None else None
        )

        resume = await self._repo.update(
            existing,
            structured_data=structured_data,
            skills=normalized_skills,
            experience_years=experience_years,
        )
        await self._session.commit()

        logger.info(
            "回填结构化数据成功 | user_id={} | resume_id={} | has_skills={}",
            user_id,
            resume_id,
            normalized_skills is not None,
        )

        # 失效 active 缓存:仅当更新的是 active 简历时才有必要
        # 简单起见:统一失效,反正下次 get_active 会重新加载
        # 这里 has_skills 不影响判断逻辑,直接 invalidate
        await self._cache.invalidate_active(user_id)

        return ResumeResponse.model_validate(resume)

    # ==================== 删除 ====================

    async def delete_resume(
        self,
        *,
        user_id: uuid.UUID,
        resume_id: uuid.UUID,
    ) -> None:
        """物理删除简历

        行为:
        - 物理删除(非软删除)
        - 若删的是当前活跃简历,该用户将暂时无活跃简历
          · 这是预期的"用户主动删除"语义
          · 用户可后续切换其他简历为活跃,或上传新简历
        - Resume 无外键关系:级联删除依赖 ORM relationship 配置
          · 当前 Resume 没有子表引用,删除安全
          · 未来若 Resume 关联 Application/Analysis 等,需先删子表

        Args:
            user_id: 用户 UUID
            resume_id: 目标简历 UUID

        Raises:
            ResourceNotFoundError: 简历不存在 / 不属于当前用户
        """
        existing = self._ensure_resume_owner(
            await self._repo.get_by_id(resume_id),
            user_id=user_id,
            resume_id=resume_id,
        )

        await self._repo.delete(existing)
        await self._session.commit()

        logger.info(
            "删除简历成功 | user_id={} | resume_id={}",
            user_id,
            resume_id,
        )

        # 失效 active 缓存:仅当删除的是 active 时才必要,统一失效更简单
        await self._cache.invalidate_active(user_id)

    # ==================== 私有辅助方法 ====================

    async def _parse_resume_file(
        self,
        *,
        content: bytes,
        file_type: str,
    ) -> str:
        """按文件类型选择 Reader 解析字节流

        设计:
        - 用 file_type 而非扩展名做分支:file_type 来自 Magic Number 校验,
          绝对可信,避免"扩展名是 .pdf 但内容是 .docx"的混淆
        - 旧版 .doc 显式拒绝(无成熟 Python 解析器)
        - 后续若要支持 .doc/.rtf/.md 等,在此处扩展分支即可

        Args:
            content: 文件原始字节流
            file_type: "pdf" / "docx" / "doc"(由 validator 返回)

        Returns:
            解析后的纯文本

        Raises:
            ValidationError: 文件类型不支持
            ExternalServiceError: 解析失败(文件损坏)
        """
        if file_type == "pdf":
            pdf_result = await self._pdf_reader.read_bytes(content)
            return pdf_result.text

        if file_type == "docx":
            docx_result = await self._docx_reader.read_bytes(content)
            return docx_result.text

        if file_type == "doc":
            # 旧版 .doc 是 OLE Compound File,Python 生态无成熟解析器
            # (olefile / msoffcrypto-tool 兼容性差,易触发 OLE 解码错误)
            # 推荐路径:引导用户"另存为 .docx"后重新上传
            raise ValidationError(
                detail=_UNSUPPORTED_DOC_MESSAGE,
                error_code="VAL_023",
            )

        # 防御性兜底:validate_resume_upload 已限制白名单,理论上到不了这里
        # 若到达,说明 EXTENSION_TO_MAGIC_KEY 与此处分支不一致
        raise ValidationError(
            detail=f"不支持的文件类型「{file_type}」",
            error_code="VAL_024",
            extra={"file_type": file_type, "supported": list(EXTENSION_TO_MAGIC_KEY.values())},
        )

    def _ensure_resume_owner(
        self,
        resume: Resume | None,
        *,
        user_id: uuid.UUID,
        resume_id: uuid.UUID,
    ) -> Resume:
        """校验简历所有权:不存在或不属于当前用户 → 统一抛 ResourceNotFoundError

        越权防护:
        - 即使 Router 漏传 user_id 或传错 user_id,本层也强制按 user_id 过滤
        - 不区分"ID 不存在"与"ID 不属于你":防枚举攻击
        - 抛 ResourceNotFoundError(404)而非 AuthorizationError(403)
          · 404 不暴露"该 ID 存在"的信息,更安全

        Args:
            resume: Repository 查询结果(None 表示不存在)
            user_id: 当前用户 UUID
            resume_id: 目标简历 UUID(用于错误日志)

        Returns:
            通过校验的 Resume 实例(便于链式调用)

        Raises:
            ResourceNotFoundError: 简历不存在或无权访问
        """
        if resume is None or resume.user_id != user_id:
            logger.info(
                "简历访问被拒绝 | user_id={} | resume_id={} | reason={}",
                user_id,
                resume_id,
                "not_found" if resume is None else "not_owner",
            )
            raise ResourceNotFoundError(
                detail=f"简历 {resume_id} 不存在或无权访问",
            )
        return resume

    def _normalize_skills(self, skills: list[str] | None) -> list[str]:
        """归一化技能列表:去空 / 去首尾空白 / 去重 / 限制长度

        设计动机:
        - DTO 层只校验长度,不归一化(避免误删 Service 期望的原始输入)
        - Service 层在写入 DB 前统一归一化,保证:
          · DB 中无空白 / 无空字符串 / 无重复
          · Agent 匹配时减少噪声(空格差异、重复)
          · JSONB 数组体积更小

        Args:
            skills: 原始技能列表(可能含空白、重复、None)

        Returns:
            归一化后的技能列表(去空 + 去重 + 截断到 RESUME_SKILLS_MAX_LENGTH)
        """
        if not skills:
            return []

        seen: set[str] = set()
        normalized: list[str] = []
        for raw in skills:
            if not isinstance(raw, str):
                continue
            item = raw.strip()
            if not item or item in seen:
                continue
            seen.add(item)
            normalized.append(item)
            if len(normalized) >= RESUME_SKILLS_MAX_LENGTH:
                break

        return normalized


# ==================== 公开导出 ====================

# 暴露供测试 / 其他模块引用的内部符号
# (按 __all__ 约定显式列出)
__all__ = [
    "PARSE_STATUS_PARSED",  # 重新导出,便于 Router 层统一引用
    "ResumeService",
]
