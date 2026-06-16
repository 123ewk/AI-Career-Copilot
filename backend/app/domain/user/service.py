"""User Domain Service（注册 / 登录 / 刷新 Token）

职责：
- 实现用户域核心业务流程：注册、登录、刷新 Token、获取当前用户
- 编排 Repository + Security + DTO：上层（Router）只调用 Service 接口
- 业务校验（邮箱不重复、密码正确）与异常翻译：DB 异常 → 业务异常

设计动机：
- 业务层不直接 import bcrypt / jwt：调用 app.core.security 统一接口
  · 未来切换算法只需改 security 包，service 零修改
- Repository 与 Service 解耦：Service 通过构造注入 session，调用 repo 方法
  · 便于测试时替换为 mock repository
- 异常使用项目统一异常体系：ConflictError / AuthenticationError / ValidationError
  · 中间件自动映射到 HTTP 状态码与响应体
- 事务边界：注册 / 登录 / 刷新成功后由 Service 调用 commit
  · Router 不再操心 commit，事务逻辑集中
- 密码强度校验下沉到 DTO（models.py）层：Service 收到的是已校验的合法数据
  · 减少「DTO 漏校验 → Service 异常」的中间态

业务流程：
1. register()：邮箱查重 → bcrypt 哈希 → Repository.create → commit → 签发 access/refresh
2. login()：邮箱查用户 → bcrypt 验证 → 签发 access/refresh
3. refresh_token()：解码 refresh → 查用户 → 签发新 access（refresh 旋转可选）
4. get_user_by_id()：按 ID 查用户（用于 /api/users/me 等已鉴权接口）

潜在风险：
- 邮箱查重与 create 之间存在 race condition：两个请求都通过 exists_by_email
  → 防御：Service 层 catch IntegrityError 翻译为 ConflictError
  → 数据库唯一约束是最后防线
- bcrypt 同步阻塞：单次哈希约 250ms，登录/注册接口 QPS 受限
  → 当前规模可接受；高并发场景改用 passlib + thread pool
- refresh token 无主动吊销：泄露后只能用 7d 过期扛
  → 缓解：登录/刷新时旋转 refresh token（每次发新，旧 jti 加黑名单）
  → 简化：本实现不旋转，与 auth 中间件保持简单一致
- 登录失败不区分"用户不存在"vs"密码错误"：防枚举攻击，但牺牲了 UX
  → 防御：均返回 "邮箱或密码错误" 模糊提示
"""

import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    AuthenticationError,
    ConflictError,
    ResourceNotFoundError,
)
from app.core.logger import logger
from app.core.security import (
    TOKEN_TYPE_REFRESH,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.domain.user.models import (
    TokenResponse,
    UserLoginRequest,
    UserRegisterRequest,
    UserResponse,
)
from app.infra.database.models.user import User
from app.infra.repositories.user_repo import UserRepository


# 登录失败统一提示：避免泄露「用户不存在」还是「密码错误」
# 防枚举攻击：攻击者通过错误信息差异确认哪些邮箱已注册
_INVALID_CREDENTIALS: str = "邮箱或密码错误"


class UserService:
    """用户域 Service

    使用方式：
        session = pg_session_factory.create_session()
        service = UserService(session)
        token = await service.register(register_dto)
        await session.close()  # 框架保证：get_db_session 的 finally 会关闭

    设计原则：
    - 单实例对应一个请求：构造时注入 session，所有操作共用同一事务
    - 内部创建 UserRepository：避免外部重复实例化
    - 显式 commit：业务成功后由 Service 显式 commit，便于业务层回滚控制
    - 异常翻译：DB 异常（IntegrityError）→ 业务异常（ConflictError）
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = UserRepository(session)

    # ==================== 公共业务方法 ====================

    async def register(
        self,
        request: UserRegisterRequest,
    ) -> tuple[UserResponse, str, str]:
        """用户注册

        流程：
        1. 邮箱查重（exists_by_email）：快速失败，避免无谓的 bcrypt 开销
        2. bcrypt 哈希密码
        3. Repository.create：插入 DB
        4. commit：持久化
        5. 签发 access + refresh token
        6. 转换为 UserResponse（剥离 password_hash）

        Args:
            request: 已通过 Pydantic 校验的注册请求 DTO
                · email 已归一化为小写（validator.py 保证）
                · password 已通过强度校验（DTO 层）
                · password_confirm 一致（DTO 层）
                · 密码不含邮箱 local part（DTO 层）

        Returns:
            (user_response, access_token, refresh_token) 三元组
            · user_response：剥离敏感字段的公开用户信息
            · access_token：JWT，15min 过期
            · refresh_token：JWT，7d 过期，调用方应通过 Set-Cookie 写入

        Raises:
            ConflictError: 邮箱已被注册（409）
            DatabaseError: DB 异常由 IntegrityError 翻译

        安全设计：
        - exists_by_email 提前检查：避免重复 bcrypt
        - email 已归一化（DTO 层）：无需 Service 再 lowercase
        - password 明文仅在 Service 内出现一次：bcrypt 后立即丢弃
        - 异常不泄露 user_id：仅返回 ConflictError + 邮箱冲突
        """
        # ---- 1. 邮箱查重 ----
        if await self._repo.exists_by_email(request.email):
            logger.info("注册失败：邮箱已被注册 | email={}", request.email)
            raise ConflictError(
                detail="该邮箱已被注册",
                extra={"email": request.email},
            )

        # ---- 2. bcrypt 哈希密码 ----
        password_hash = hash_password(request.password)

        # ---- 3. 创建用户 ----
        try:
            user = await self._repo.create(
                email=request.email,
                password_hash=password_hash,
                name=request.name,
                target_position=request.target_position,
                target_industry=request.target_industry,
            )
            # ---- 4. 提交事务 ----
            await self._session.commit()
        except IntegrityError as exc:
            # race condition 兜底：exists_by_email 通过但 INSERT 仍可能冲突
            # （两个请求同时通过 exists 检查）
            await self._session.rollback()
            logger.info(
                "注册失败：DB 唯一约束冲突 | email={} | exc={}",
                request.email,
                type(exc).__name__,
            )
            raise ConflictError(
                detail="该邮箱已被注册",
                extra={"email": request.email},
            ) from exc

        logger.info(
            "用户注册成功 | user_id={} | email={}",
            user.id,
            user.email,
        )

        # ---- 5-6. 签发 token + 转换 DTO ----
        access_token = create_access_token(user.id)
        refresh_token = create_refresh_token(user.id)
        user_response = UserResponse.model_validate(user)

        return user_response, access_token, refresh_token

    async def login(
        self,
        request: UserLoginRequest,
    ) -> tuple[UserResponse, str, str]:
        """用户登录

        流程：
        1. 按邮箱查用户（get_by_email）
        2. 校验密码（bcrypt.verify）
        3. 签发 access + refresh token
        4. 转换为 UserResponse

        Args:
            request: 登录请求 DTO
                · email 已归一化（DTO 层）
                · password 长度合法（DTO 层）
                · 注意：登录 DTO 不做密码强度校验（老用户兼容）

        Returns:
            (user_response, access_token, refresh_token) 三元组

        Raises:
            AuthenticationError: 用户不存在或密码错误（统一为 "邮箱或密码错误"）

        安全设计：
        - 用户不存在与密码错误返回相同 detail：防枚举攻击
        - 不记录「密码错误次数」：避免成为攻击线索
        - bcrypt 验证是恒定时间：内部使用 hmac.compare_digest 防时序
        - 登录失败不更新 updated_at：不暴露「最近登录」信息
        """
        # ---- 1. 查用户 ----
        user = await self._repo.get_by_email(request.email)
        if user is None:
            logger.info("登录失败：邮箱不存在 | email={}", request.email)
            raise AuthenticationError(detail=_INVALID_CREDENTIALS)

        # ---- 2. 验证密码 ----
        if not verify_password(request.password, user.password_hash):
            logger.info(
                "登录失败：密码错误 | user_id={} | email={}",
                user.id,
                user.email,
            )
            raise AuthenticationError(detail=_INVALID_CREDENTIALS)

        logger.info("用户登录成功 | user_id={} | email={}", user.id, user.email)

        # ---- 3-4. 签发 token + 转换 DTO ----
        access_token = create_access_token(user.id)
        refresh_token = create_refresh_token(user.id)
        user_response = UserResponse.model_validate(user)

        return user_response, access_token, refresh_token

    async def refresh_token(
        self,
        refresh_token_str: str,
    ) -> tuple[UserResponse, str, str]:
        """刷新 access token

        流程：
        1. 解码并校验 refresh token（type 必须为 refresh）
        2. 解析 user_id
        3. 查询用户（确认未被删除）
        4. 签发新 access + 新 refresh（旋转策略）
        5. 转换为 UserResponse

        Args:
            refresh_token_str: refresh token JWT 字符串

        Returns:
            (user_response, new_access_token, new_refresh_token) 三元组

        Raises:
            AuthenticationError: refresh token 无效 / 过期 / 类型错
            ResourceNotFoundError: token 合法但用户不存在（账号已删除）

        安全设计：
        - 强制 type=refresh：拒绝用 access 换 access
        - 旋转 refresh token：每次刷新都发新 refresh，旧 refresh 应失效
          · 当前实现不维护黑名单（旧 refresh 在过期前仍可使用）
          · 未来可加 Redis 黑名单：jti → revoked
        - 查询用户存在性：防御「token 合法但用户已注销」场景
        """
        # ---- 1. 解码 refresh token ----
        try:
            payload = decode_token(
                refresh_token_str,
                expected_type=TOKEN_TYPE_REFRESH,
            )
        except Exception as exc:
            # TokenError 翻译为 AuthenticationError
            logger.info("刷新失败：refresh token 无效 | exc={}", type(exc).__name__)
            raise AuthenticationError(detail="refresh token 无效或已过期") from exc

        # ---- 2. 解析 user_id ----
        try:
            user_id = uuid.UUID(payload["sub"])
        except (KeyError, ValueError) as exc:
            # decode_token 已校验 sub 存在，此处仅防 UUID 格式异常
            logger.warning("刷新失败：sub 声明不是合法 UUID | sub={}", payload.get("sub"))
            raise AuthenticationError(detail="refresh token 无效") from exc

        # ---- 3. 查用户 ----
        user = await self._repo.get_by_id(user_id)
        if user is None:
            logger.warning("刷新失败：用户不存在 | user_id={}", user_id)
            raise ResourceNotFoundError(
                detail="用户不存在",
                extra={"user_id": str(user_id)},
            )

        logger.info("用户刷新 token 成功 | user_id={}", user.id)

        # ---- 4-5. 签发新 token + 转换 DTO ----
        new_access = create_access_token(user.id)
        new_refresh = create_refresh_token(user.id)
        user_response = UserResponse.model_validate(user)

        return user_response, new_access, new_refresh

    async def get_user_by_id(self, user_id: uuid.UUID) -> UserResponse:
        """按 ID 查用户（用于 /api/users/me 等已鉴权接口）

        Args:
            user_id: 用户 UUID（来自 JWT sub 声明）

        Returns:
            UserResponse（剥离敏感字段）

        Raises:
            ResourceNotFoundError: 用户不存在
        """
        user = await self._repo.get_by_id(user_id)
        if user is None:
            raise ResourceNotFoundError(
                detail="用户不存在",
                extra={"user_id": str(user_id)},
            )
        return UserResponse.model_validate(user)

    # ==================== 辅助方法 ====================

    @staticmethod
    def build_token_response(
        user: UserResponse,
        access_token: str,
    ) -> TokenResponse:
        """构造 TokenResponse（router 层用）

        refresh_token 不在 TokenResponse 中（详见 models.TokenResponse docstring），
        由 router 层通过 Set-Cookie 单独设置。

        Args:
            user: 用户公开信息 DTO
            access_token: 已签发的 access token

        Returns:
            TokenResponse 对象
        """
        return TokenResponse(user=user, access_token=access_token)


__all__ = [
    "UserService",
    "TokenResponse",
    "UserResponse",
    "User",
]
