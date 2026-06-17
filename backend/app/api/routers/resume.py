"""Resume Router（简历上传 / 查询 / 删除）

职责：
- 暴露 /api/resumes 下的简历相关 HTTP 端点（需鉴权）
- 接收 HTTP 请求，做最薄的协议层适配（multipart 解析、参数校验、状态码选择）
- 调用 ResumeService 处理业务逻辑
- 业务异常（ResourceNotFoundError / ValidationError / ExternalServiceError）
  由全局异常中间件统一翻译为 4xx/5xx JSON 响应

设计动机：
- Router 不做业务逻辑：解析 / 归一化 / 越权校验 / 缓存失效全部在 Service 层
- 鉴权由全局 auth 中间件完成，本 Router 仅从 request.state.user_id 读取（不重复解析 JWT）
- 列表分页参数由 FastAPI Query 校验：limit/offset 越界在协议层早失败
- 上传走 multipart/form-data：FastAPI UploadFile 自动处理流式读取与临时文件清理
- 状态码语义：upload=201 Created（资源创建）、list/get=200 OK、delete=204 No Content

业务流程：
1. POST /upload
   - 接收 multipart 字段 file
   - 提取 filename / content_type / bytes 传给 Service
   - Service 校验 → 解析 → 入库 → 失效缓存 → 返回 ResumeUploadResponse
   - 成功返回 201 + parse_status（PARSED 当前固定）

2. GET /
   - Query 校验 limit (1-100) / offset (>=0)
   - 调 Service.list_resumes → (summaries, total)
   - 返回 200 + ResumeListResponse{items, total, limit, offset}

3. GET /{id}
   - Path 校验 resume_id 必须是合法 UUID（FastAPI 自动）
   - 调 Service.get_resume（带 user_id 防越权）
   - 不存在或越权 → 404（统一为 ResourceNotFoundError，防枚举）
   - 返回 200 + ResumeResponse

4. DELETE /{id}
   - 调 Service.delete_resume（带 user_id 防越权）
   - 成功返回 204（无 body）
   - 不存在或越权 → 404

潜在风险：
- 上传大文件：当前依赖 validator 的 10MB 硬上限
  → ResumeService 内部 validator 拒绝 > 10MB，恶意请求会快速 400
  → 当前不引入流式校验：FastAPI UploadFile 仍会先把文件读入内存（multipart 限制）
  → 强化路径：未来可改用 tus.io / presigned-URL 直传 OSS
- 路径顺序冲突：FastAPI 按声明顺序匹配
  → POST /upload 必须在 GET /{resume_id} 之前定义（虽然 method 不同不会冲突，但保持声明顺序清晰）
  → 实际：本文件顺序为 upload → list → get → delete，与调用频率一致
- user_id 来源：request.state.user_id 由 auth 中间件保证存在（非白名单路径）
  → Router 仍做兜底：缺失时抛 401，防止中间件被错误配置
- 跨用户越权：Service 层 _ensure_resume_owner 二次防御
  → 即使 Router 误传 user_id，Service 仍按 user_id 过滤，抛 404
- Delete 后的孤儿数据：物理删除后无审计轨迹
  → 符合当前 PRD 设计：未来若需软删除，扩展 Resume ORM 加 deleted_at 字段
"""

import uuid

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Path,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import logger
from app.core.settings import get_settings
from app.domain.resume.models import (
    ResumeListResponse,
    ResumeResponse,
    ResumeUploadResponse,
)
from app.domain.resume.service import ResumeService
from app.domain.resume.validator import (
    ALLOWED_EXTENSIONS,
    ALLOWED_MIME_TYPES,
    MAX_FILE_SIZE_BYTES,
)
from app.infra.database.postgres import get_db_session

# ==================== Router 实例 ====================

# prefix 在 main.py 中已设置为 /api/resumes，本 Router 路径均相对该 prefix
# tags=["resume"] 由 main.py 的 include_router 覆盖为「简历」
router = APIRouter()


# ==================== 辅助函数 ====================

def _get_current_user_id(request: Request) -> uuid.UUID:
    """从 request.state.user_id 提取当前用户 UUID

    职责：
    - auth 中间件在白名单外的路径会校验 JWT 并把 user_id 写入 request.state
    - 本函数做兜底：
      · 缺失 user_id → 401（防止中间件被错误配置绕过鉴权）
      · 非法 UUID → 401（防止 token 内部损坏）

    设计动机：
    - 不直接从 Request 依赖注入：项目沿用 auth 中间件 + request.state 模式
    - 字符串 → UUID 转换集中在一处，便于将来切到直接注入 UUID（如改用 Depends）

    Args:
        request: FastAPI 请求对象

    Returns:
        当前用户 UUID

    Raises:
        HTTPException: 401（用户身份不可用）
    """
    raw = getattr(request.state, "user_id", None)
    if not raw:
        # 理论上 auth 中间件会拦截，这里是深度防御
        logger.warning("Router 缺少 user_id | path={}", request.url.path)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少用户身份信息",
        )
    try:
        return uuid.UUID(str(raw))
    except (TypeError, ValueError):
        # token sub 声明损坏（理论上 JWT 解码已校验格式，此处是兜底）
        logger.error("user_id 格式非法 | raw={}", raw)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户身份信息格式错误",
        ) from None


def _ensure_upload_file_meta(file: UploadFile) -> tuple[str, str]:
    """校验 UploadFile 元信息并归一化

    FastAPI 的 UploadFile 在 multipart 解析失败时可能 filename/content_type 为 None
    这里显式校验并归一化：
    - filename: 必须非空（具体扩展名校验交给 Service 层 validator）
    - content_type: 必须非空；application/octet-stream 视为合法（浏览器 fallback）

    Args:
        file: FastAPI UploadFile 实例

    Returns:
        (filename, content_type) 元组

    Raises:
        HTTPException: 400（文件元信息缺失）
    """
    filename = file.filename
    if not filename or not filename.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="文件名为空",
        )
    content_type = file.content_type
    if not content_type or not content_type.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MIME 类型缺失",
        )
    return filename.strip(), content_type.strip().lower()


# ==================== 端点：上传简历 ====================

@router.post(
    "/upload",
    response_model=ResumeUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="上传简历（PDF / DOCX / DOC）",
    description=(
        "上传一份简历文件，服务端自动解析并保存。\n\n"
        "- 支持格式：PDF / DOCX（.doc 暂不支持）\n"
        f"- 大小上限：{MAX_FILE_SIZE_BYTES // (1024 * 1024)} MB\n"
        f"- MIME 白名单：{sorted(ALLOWED_MIME_TYPES)}\n"
        f"- 扩展名白名单：{sorted(ALLOWED_EXTENSIONS)}\n"
        "- 上传成功后该简历自动设为当前活跃简历，旧活跃简历自动取消\n"
        "- 解析状态：当前固定为 PARSED（同步解析），未来可扩展 PARSING/FAILED\n"
        "- 失败返回 400（文件校验失败）/ 502（解析失败）"
    ),
)
async def upload_resume(
    request: Request,
    file: UploadFile = File(
        ...,
        description="简历文件（multipart/form-data）",
    ),
    db: AsyncSession = Depends(get_db_session),
) -> ResumeUploadResponse:
    """上传简历端点

    流程：
    1. 提取 user_id（从 auth 中间件注入的 request.state）
    2. 校验 UploadFile 元信息（filename / content_type 非空）
    3. 读取文件字节流（await file.read()）
    4. 调 ResumeService.upload_resume 完成校验 → 解析 → 入库
    5. 返回 201 + ResumeUploadResponse

    Args:
        request: FastAPI 请求对象（用于读取 user_id）
        file: 上传的文件（multipart 字段名固定为 file）
        db: 请求级 AsyncSession

    Returns:
        ResumeUploadResponse: 包含完整 Resume + parse_status

    Raises:
        HTTPException: 400（文件元信息缺失）/ 401（鉴权异常）
        ValidationError: 文件名 / MIME / 大小 / Magic Number 校验失败（由中间件转 400）
        ExternalServiceError: PDF/DOCX 解析失败（由中间件转 502）
    """
    user_id = _get_current_user_id(request)
    filename, content_type = _ensure_upload_file_meta(file)

    # 读取字节流：FastAPI UploadFile 默认使用 SpooledTemporaryFile，
    # 小于阈值在内存，大于阈值落盘；read() 一次读完整个文件
    # 注：当前 Service 层 validator 已限制 10MB，故读入内存可接受
    content = await file.read()
    await file.close()  # 显式关闭：释放 SpooledTemporaryFile 句柄

    settings = get_settings()
    logger.info(
        "简历上传端点 | user_id={} | filename={} | mime={} | size={} | env={}",
        user_id,
        filename,
        content_type,
        len(content),
        settings.app_env,
    )

    service = ResumeService(db)
    return await service.upload_resume(
        user_id=user_id,
        filename=filename,
        mime_type=content_type,
        content=content,
    )


# ==================== 端点：列表 ====================

@router.get(
    "/",
    response_model=ResumeListResponse,
    summary="获取当前用户的简历列表（分页）",
    description=(
        "分页返回当前用户的所有简历摘要。\n\n"
        "- 默认按创建时间倒序（最新上传在前）\n"
        "- 每页 1-100 条，默认 20 条\n"
        "- 响应不含 raw_text / structured_data（详情请调 GET /{id}）"
    ),
)
async def list_resumes(
    request: Request,
    limit: int = Query(
        20,
        ge=1,
        le=100,
        description="每页大小（1-100）",
    ),
    offset: int = Query(
        0,
        ge=0,
        description="偏移量（>=0）",
    ),
    db: AsyncSession = Depends(get_db_session),
) -> ResumeListResponse:
    """简历列表端点

    流程：
    1. 提取 user_id
    2. Query 校验 limit/offset（FastAPI 自动）
    3. 调 ResumeService.list_resumes
    4. 包装为 ResumeListResponse 返回

    Args:
        request: FastAPI 请求对象
        limit: 每页大小（Query 校验 1-100）
        offset: 偏移量（Query 校验 >=0）
        db: 请求级 AsyncSession

    Returns:
        ResumeListResponse: items + total + 回显 limit/offset
    """
    user_id = _get_current_user_id(request)

    service = ResumeService(db)
    summaries, total = await service.list_resumes(
        user_id=user_id,
        limit=limit,
        offset=offset,
    )

    logger.info(
        "简历列表端点 | user_id={} | limit={} | offset={} | returned={} | total={}",
        user_id,
        limit,
        offset,
        len(summaries),
        total,
    )

    return ResumeListResponse(
        items=list(summaries),
        total=total,
        limit=limit,
        offset=offset,
    )


# ==================== 端点：详情 ====================

@router.get(
    "/{resume_id}",
    response_model=ResumeResponse,
    summary="获取简历详情",
    description=(
        "按 ID 查询简历完整内容。\n\n"
        "- 包含 raw_text 与 structured_data\n"
        "- 不存在或无权访问统一返回 404（防枚举）"
    ),
)
async def get_resume(
    request: Request,
    resume_id: uuid.UUID = Path(
        ...,
        description="简历 UUID v4",
    ),
    db: AsyncSession = Depends(get_db_session),
) -> ResumeResponse:
    """简历详情端点

    流程：
    1. 提取 user_id
    2. Path 校验 resume_id 必须是合法 UUID（FastAPI 自动）
    3. 调 ResumeService.get_resume（带 user_id 防越权）
    4. 越权或不存在的处理在 Service 层统一抛 ResourceNotFoundError

    Args:
        request: FastAPI 请求对象
        resume_id: 简历 UUID（Path 校验）
        db: 请求级 AsyncSession

    Returns:
        ResumeResponse: 完整简历信息（含 raw_text + structured_data）

    Raises:
        ResourceNotFoundError: 简历不存在或不属于当前用户（中间件转 404）
    """
    user_id = _get_current_user_id(request)

    service = ResumeService(db)
    return await service.get_resume(
        user_id=user_id,
        resume_id=resume_id,
    )


# ==================== 端点：删除 ====================

@router.delete(
    "/{resume_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除简历",
    description=(
        "物理删除指定简历。\n\n"
        "- 不存在或无权访问返回 404（防枚举）\n"
        "- 若删除的是当前活跃简历，该用户将暂时无活跃简历，"
        "可后续切换其他简历为活跃或上传新简历\n"
        "- 成功返回 204 No Content（无响应体）"
    ),
    # FastAPI 204 必须无 body：response_class 显式标注避免框架误判
    response_class=Response,
    # 显式补齐 OpenAPI 文档：status_code=204 不会自动生成响应 schema
    # 必须通过 responses 显式描述每个可能的状态码,Swagger UI 才能正确展示
    responses={
        204: {
            "description": "简历删除成功,无响应体",
        },
        401: {
            "description": "缺少或无效的认证凭证",
            "content": {
                "application/json": {
                    "example": {
                        "error_code": "AUTH_001",
                        "detail": "认证失败,请重新登录",
                        "request_id": "req-xxxxxxxx",
                    },
                },
            },
        },
        404: {
            "description": "简历不存在或不属于当前用户",
            "content": {
                "application/json": {
                    "example": {
                        "error_code": "RES_001",
                        "detail": "简历 xxx 不存在或无权访问",
                        "request_id": "req-xxxxxxxx",
                    },
                },
            },
        },
    },
)
async def delete_resume(
    request: Request,
    resume_id: uuid.UUID = Path(
        ...,
        description="简历 UUID v4",
    ),
    db: AsyncSession = Depends(get_db_session),
) -> Response:
    """删除简历端点

    流程：
    1. 提取 user_id
    2. Path 校验 resume_id 必须是合法 UUID（FastAPI 自动）
    3. 调 ResumeService.delete_resume（带 user_id 防越权）
    4. 返回 204 No Content

    Args:
        request: FastAPI 请求对象
        resume_id: 简历 UUID（Path 校验）
        db: 请求级 AsyncSession

    Returns:
        Response: 204 No Content（空 body）

    Raises:
        ResourceNotFoundError: 简历不存在或不属于当前用户（中间件转 404）
    """
    user_id = _get_current_user_id(request)

    service = ResumeService(db)
    await service.delete_resume(
        user_id=user_id,
        resume_id=resume_id,
    )

    logger.info(
        "简历删除端点 | user_id={} | resume_id={}",
        user_id,
        resume_id,
    )

    # 显式返回空 Response：确保 FastAPI 不会因为返回 None 而自动加 body
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
