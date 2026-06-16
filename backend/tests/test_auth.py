"""Auth 模块测试

覆盖范围：
- UserService：register / login / refresh_token / get_user_by_id 正常+异常
- AuthRouter：POST /api/auth/register、/login、/refresh 正常+异常

测试策略：
- 单元测试：直接构造 UserService，注入 mock session + mock UserRepository
- 集成测试：用 FastAPI app + dependency_overrides 替换 get_db_session，
  并通过 monkeypatch 替换 UserRepository 类，构造端到端 HTTP 流程

注：
- pyproject.toml 配置 filterwarnings = ["error"]，将所有告警转错误
- exception.py 使用的 starlette.status.HTTP_422_UNPROCESSABLE_ENTITY 在
  starlette 1.x 已弃用，会触发 DeprecationWarning 导致测试失败
- 本测试用 pytestmark.filterwarnings 局部忽略该告警（仅 starlette.status 模块）
- 不修改 production code：deprecation 修复应单独 PR

运行：cd backend && pytest tests/test_auth.py -v
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock

import jwt
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.middleware.exception import add_exception_middleware
from app.api.routers.auth import router as auth_router
from app.core.exceptions import (
    AuthenticationError,
    ConflictError,
    ResourceNotFoundError,
)
from app.core.security import (
    TOKEN_TYPE_ACCESS,
    TOKEN_TYPE_REFRESH,
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
from app.domain.user.service import UserService
from app.infra.database.models.user import User
from app.infra.database.postgres import get_db_session
from app.infra.repositories.user_repo import UserRepository

# ==================== pytest 标记 ====================
# 局部忽略 starlette.status 模块的 DeprecationWarning（兼容 starlette < 1.3）
# 实际运行中可能触发 StarletteDeprecationWarning（starlette >= 1.3 引入的自定义类），
# 由 backend/tests/conftest.py 在 import starlette 后再注册过滤器（pytestmark 阶段类未注册）
# 生产代码 exception.py 引用了 starlette 1.x 已弃用的 HTTP_422_UNPROCESSABLE_ENTITY
# pyproject.toml 把所有 warning 转 error，必须用 pytestmark 局部覆盖
# 该修复属于 production code 范畴，不在本任务范围
pytestmark = pytest.mark.filterwarnings(
    "ignore::DeprecationWarning:starlette.status",
)


# ==================== 常量 ====================

# 测试用强密码：不在 WEAK_PASSWORDS 黑名单、长度 ≥ 8、含字母+数字
# "mypwd#2024strong".lower() 不在黑名单中
STRONG_PWD: Final[str] = "MyPwd#2024Strong"  # noqa: F821 - Final from typing not used for simplicity

# 测试用弱密码（黑名单中）
WEAK_PWD_BLACKLIST: Final[str] = "password"  # noqa: F821

# 测试用错误密码（符合强度但与注册时不同）
WRONG_PWD: Final[str] = "WrongPwd#1234"  # noqa: F821


# ==================== 工厂 ====================

def make_user(**overrides: Any) -> User:
    """构造测试用 User ORM 实例

    默认值遵循 user.py ORM Model 字段定义（id/email/password_hash/name/...）。
    """
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "email": "alice@example.com",
        "password_hash": "$2b$12$placeholderhash0000000000000000000000000000000000000000",
        "name": "Test User",
        "target_position": None,
        "target_industry": None,
        "created_at": datetime(2024, 1, 1, 0, 0, 0),
        "updated_at": datetime(2024, 1, 1, 0, 0, 0),
    }
    defaults.update(overrides)
    return User(**defaults)


@pytest.fixture(scope="module")
def strong_hash() -> str:
    """预计算 STRONG_PWD 的 bcrypt 哈希

    bcrypt rounds=12 哈希约 250ms，module scope 仅计算一次，
    避免 30+ 个测试各自等待。
    """
    return hash_password(STRONG_PWD)


def make_mock_session() -> AsyncMock:
    """构造 mock AsyncSession（commit/rollback 可控）"""
    s = AsyncMock(spec=AsyncSession)
    s.commit = AsyncMock(return_value=None)
    s.rollback = AsyncMock(return_value=None)
    return s


# ==================== UserService 单元测试 ====================


class TestUserServiceRegister:
    """UserService.register 单元测试"""

    @pytest.fixture
    def service(self) -> UserService:
        session = make_mock_session()
        svc = UserService(session)
        # 替换内置 repo 为 mock
        svc._repo = AsyncMock(spec=UserRepository)
        return svc

    @pytest.mark.asyncio
    async def test_returns_user_response_and_tokens(self, service, strong_hash: str) -> None:
        """正常路径：返回 UserResponse + access + refresh 三个值"""
        # 模拟 DB echo：返回的 user 带 name="Alice"（与请求一致）
        user = make_user(email="alice@example.com", password_hash=strong_hash, name="Alice")
        service._repo.exists_by_email.return_value = False
        service._repo.create.return_value = user

        request = UserRegisterRequest(
            email="alice@example.com",
            password=STRONG_PWD,
            password_confirm=STRONG_PWD,
            name="Alice",
        )
        user_response, access, refresh = await service.register(request)

        # 1. user_response 字段
        assert user_response.email == "alice@example.com"
        assert user_response.id == user.id
        assert user_response.name == "Alice"
        # 2. tokens 是非空字符串
        assert isinstance(access, str) and len(access) > 20
        assert isinstance(refresh, str) and len(refresh) > 20
        # 3. response 不暴露 password_hash
        dumped = user_response.model_dump()
        assert "password_hash" not in dumped

    @pytest.mark.asyncio
    async def test_hashes_password_with_bcrypt(self, service, strong_hash: str) -> None:
        """正常路径：明文密码被 bcrypt 哈希（不是明文存储）"""
        user = make_user(password_hash=strong_hash)
        service._repo.exists_by_email.return_value = False
        service._repo.create.return_value = user

        request = UserRegisterRequest(
            email="bob@example.com",
            password=STRONG_PWD,
            password_confirm=STRONG_PWD,
        )
        await service.register(request)

        # 验证 create 被调用，且 password_hash 是 bcrypt 哈希
        call_kwargs = service._repo.create.await_args.kwargs
        assert call_kwargs["password_hash"] != STRONG_PWD
        # bcrypt 哈希可被 verify_password 校验
        assert verify_password(STRONG_PWD, call_kwargs["password_hash"])

    @pytest.mark.asyncio
    async def test_passes_profile_fields_to_repo(self, service, strong_hash: str) -> None:
        """正常路径：name / target_position / target_industry 透传到 repo"""
        user = make_user(password_hash=strong_hash)
        service._repo.exists_by_email.return_value = False
        service._repo.create.return_value = user

        request = UserRegisterRequest(
            email="carol@example.com",
            password=STRONG_PWD,
            password_confirm=STRONG_PWD,
            name="张三",
            target_position="AI 工程师",
            target_industry="互联网",
        )
        await service.register(request)

        kwargs = service._repo.create.await_args.kwargs
        assert kwargs["email"] == "carol@example.com"
        assert kwargs["name"] == "张三"
        assert kwargs["target_position"] == "AI 工程师"
        assert kwargs["target_industry"] == "互联网"

    @pytest.mark.asyncio
    async def test_calls_commit_after_create(self, service, strong_hash: str) -> None:
        """正常路径：create 成功后 commit"""
        user = make_user(password_hash=strong_hash)
        service._repo.exists_by_email.return_value = False
        service._repo.create.return_value = user

        request = UserRegisterRequest(
            email="dave@example.com",
            password=STRONG_PWD,
            password_confirm=STRONG_PWD,
        )
        await service.register(request)
        service._session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_raises_conflict_when_email_exists(self, service) -> None:
        """异常路径：邮箱已存在 → ConflictError（409）"""
        service._repo.exists_by_email.return_value = True

        request = UserRegisterRequest(
            email="dup@example.com",
            password=STRONG_PWD,
            password_confirm=STRONG_PWD,
        )
        with pytest.raises(ConflictError) as exc_info:
            await service.register(request)

        assert "已被注册" in exc_info.value.detail
        # 短路：create 不能再被调用（避免无谓的 bcrypt 开销）
        service._repo.create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_raises_conflict_on_db_integrity_error(self, service) -> None:
        """异常路径：DB IntegrityError（race condition 兜底）→ ConflictError"""
        service._repo.exists_by_email.return_value = False
        # 模拟两个并发请求都通过 exists 检查，但 DB 唯一约束拦截
        service._repo.create.side_effect = IntegrityError(
            "INSERT", {}, Exception("unique violation"),
        )

        request = UserRegisterRequest(
            email="race@example.com",
            password=STRONG_PWD,
            password_confirm=STRONG_PWD,
        )
        with pytest.raises(ConflictError):
            await service.register(request)

        # 必须 rollback 释放事务
        service._session.rollback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_emits_valid_jwt_tokens(self, service, strong_hash: str) -> None:
        """正常路径：签发的 access/refresh 是合法 JWT，type 字段正确"""
        user = make_user(password_hash=strong_hash)
        service._repo.exists_by_email.return_value = False
        service._repo.create.return_value = user

        request = UserRegisterRequest(
            email="eve@example.com",
            password=STRONG_PWD,
            password_confirm=STRONG_PWD,
        )
        _, access, refresh = await service.register(request)

        access_payload = decode_token(access, expected_type=TOKEN_TYPE_ACCESS)
        refresh_payload = decode_token(refresh, expected_type=TOKEN_TYPE_REFRESH)

        assert access_payload["type"] == "access"
        assert refresh_payload["type"] == "refresh"
        assert access_payload["sub"] == str(user.id)
        assert refresh_payload["sub"] == str(user.id)


class TestUserServiceLogin:
    """UserService.login 单元测试"""

    @pytest.fixture
    def service(self) -> UserService:
        session = make_mock_session()
        svc = UserService(session)
        svc._repo = AsyncMock(spec=UserRepository)
        return svc

    @pytest.mark.asyncio
    async def test_returns_tokens_on_valid_credentials(
        self, service, strong_hash: str,
    ) -> None:
        """正常路径：邮箱+密码正确 → 返回 user + tokens"""
        user = make_user(email="login@example.com", password_hash=strong_hash)
        service._repo.get_by_email.return_value = user

        request = UserLoginRequest(email="login@example.com", password=STRONG_PWD)
        user_response, access, refresh = await service.login(request)

        assert user_response.email == "login@example.com"
        assert isinstance(access, str) and len(access) > 20
        assert isinstance(refresh, str) and len(refresh) > 20

    @pytest.mark.asyncio
    async def test_raises_auth_error_when_user_not_found(self, service) -> None:
        """异常路径：邮箱不存在 → AuthenticationError（401）"""
        service._repo.get_by_email.return_value = None

        request = UserLoginRequest(email="none@example.com", password=STRONG_PWD)
        with pytest.raises(AuthenticationError):
            await service.login(request)

    @pytest.mark.asyncio
    async def test_raises_auth_error_when_password_wrong(
        self, service, strong_hash: str,
    ) -> None:
        """异常路径：密码错误 → AuthenticationError（401）"""
        user = make_user(password_hash=strong_hash)
        service._repo.get_by_email.return_value = user

        request = UserLoginRequest(email="x@example.com", password=WRONG_PWD)
        with pytest.raises(AuthenticationError):
            await service.login(request)

    @pytest.mark.asyncio
    async def test_same_error_message_for_user_not_found_and_wrong_password(
        self, service, strong_hash: str,
    ) -> None:
        """防枚举攻击：用户不存在 vs 密码错误 → 完全相同的 detail

        防止攻击者通过错误信息差异确认哪些邮箱已注册。
        """
        # 场景 1：邮箱不存在
        service._repo.get_by_email.return_value = None
        try:
            await service.login(
                UserLoginRequest(email="x@example.com", password=STRONG_PWD),
            )
        except AuthenticationError as e1:
            msg_not_found = e1.detail

        # 场景 2：邮箱存在但密码错误
        user = make_user(password_hash=strong_hash)
        service._repo.get_by_email.return_value = user
        try:
            await service.login(
                UserLoginRequest(email="x@example.com", password=WRONG_PWD),
            )
        except AuthenticationError as e2:
            msg_wrong = e2.detail

        assert msg_not_found == msg_wrong
        assert "邮箱或密码错误" in msg_not_found

    @pytest.mark.asyncio
    async def test_jwt_access_token_has_correct_type(
        self, service, strong_hash: str,
    ) -> None:
        """正常路径：access token 的 type 声明是 access"""
        user = make_user(password_hash=strong_hash)
        service._repo.get_by_email.return_value = user

        _, access, _ = await service.login(
            UserLoginRequest(email="x@example.com", password=STRONG_PWD),
        )
        payload = decode_token(access, expected_type=TOKEN_TYPE_ACCESS)
        assert payload["type"] == TOKEN_TYPE_ACCESS
        assert payload["sub"] == str(user.id)

    @pytest.mark.asyncio
    async def test_jwt_refresh_token_has_correct_type(
        self, service, strong_hash: str,
    ) -> None:
        """正常路径：refresh token 的 type 声明是 refresh"""
        user = make_user(password_hash=strong_hash)
        service._repo.get_by_email.return_value = user

        _, _, refresh = await service.login(
            UserLoginRequest(email="x@example.com", password=STRONG_PWD),
        )
        payload = decode_token(refresh, expected_type=TOKEN_TYPE_REFRESH)
        assert payload["type"] == TOKEN_TYPE_REFRESH


class TestUserServiceRefreshToken:
    """UserService.refresh_token 单元测试"""

    @pytest.fixture
    def service(self) -> UserService:
        session = make_mock_session()
        svc = UserService(session)
        svc._repo = AsyncMock(spec=UserRepository)
        return svc

    @pytest.mark.asyncio
    async def test_returns_new_tokens_on_valid_refresh(
        self, service, strong_hash: str,
    ) -> None:
        """正常路径：合法 refresh token → 新 access + 新 refresh（旋转）"""
        user = make_user(password_hash=strong_hash)
        service._repo.get_by_id.return_value = user

        old_refresh = create_refresh_token(user.id)
        user_response, new_access, new_refresh = await service.refresh_token(old_refresh)

        assert user_response.id == user.id
        # 旋转：每次刷新生成新的 token
        assert new_access != old_refresh
        assert new_refresh != old_refresh

    @pytest.mark.asyncio
    async def test_raises_auth_error_for_access_token_used_as_refresh(self, service) -> None:
        """异常路径：用 access token 换 refresh → AuthenticationError（防类型混淆）"""
        from app.core.security import create_access_token

        user = make_user()
        access = create_access_token(user.id)
        with pytest.raises(AuthenticationError):
            await service.refresh_token(access)

    @pytest.mark.asyncio
    async def test_raises_auth_error_for_malformed_token(self, service) -> None:
        """异常路径：非 JWT 字符串 → AuthenticationError"""
        with pytest.raises(AuthenticationError):
            await service.refresh_token("not.a.valid.jwt")

    @pytest.mark.asyncio
    async def test_raises_auth_error_for_garbage_token(self, service) -> None:
        """异常路径：JWT 格式但无意义 → AuthenticationError"""
        with pytest.raises(AuthenticationError):
            await service.refresh_token("totally-garbage-string")

    @pytest.mark.asyncio
    async def test_raises_auth_error_for_expired_token(self, service) -> None:
        """异常路径：过期 refresh token → AuthenticationError"""
        now = datetime.now(tz=UTC)
        payload = {
            "sub": str(uuid.uuid4()),
            "type": "refresh",
            "iat": int((now - timedelta(days=10)).timestamp()),
            "exp": int((now - timedelta(days=1)).timestamp()),
            "jti": "expired-jti",
        }
        expired = jwt.encode(payload, "test-secret-key-do-not-use-in-prod", algorithm="HS256")

        with pytest.raises(AuthenticationError):
            await service.refresh_token(expired)

    @pytest.mark.asyncio
    async def test_raises_not_found_when_user_deleted(
        self, service, strong_hash: str,
    ) -> None:
        """异常路径：refresh 合法但用户已被删除 → ResourceNotFoundError（404）"""
        service._repo.get_by_id.return_value = None

        refresh = create_refresh_token(uuid.uuid4())
        with pytest.raises(ResourceNotFoundError):
            await service.refresh_token(refresh)

    @pytest.mark.asyncio
    async def test_user_payload_not_include_password_hash(
        self, service, strong_hash: str,
    ) -> None:
        """正常路径：响应不包含 password_hash"""
        user = make_user(password_hash=strong_hash)
        service._repo.get_by_id.return_value = user

        refresh = create_refresh_token(user.id)
        user_response, _, _ = await service.refresh_token(refresh)

        dumped = user_response.model_dump()
        assert "password_hash" not in dumped


class TestUserServiceGetUserById:
    """UserService.get_user_by_id 单元测试"""

    @pytest.fixture
    def service(self) -> UserService:
        session = make_mock_session()
        svc = UserService(session)
        svc._repo = AsyncMock(spec=UserRepository)
        return svc

    @pytest.mark.asyncio
    async def test_returns_user_response(self, service, strong_hash: str) -> None:
        """正常路径：返回 UserResponse（不包含 password_hash）"""
        user = make_user(password_hash=strong_hash)
        service._repo.get_by_id.return_value = user

        result = await service.get_user_by_id(user.id)
        assert result.id == user.id
        assert result.email == user.email
        assert "password_hash" not in result.model_dump()

    @pytest.mark.asyncio
    async def test_raises_not_found_when_missing(self, service) -> None:
        """异常路径：用户不存在 → ResourceNotFoundError"""
        service._repo.get_by_id.return_value = None
        with pytest.raises(ResourceNotFoundError):
            await service.get_user_by_id(uuid.uuid4())


class TestUserServiceBuildTokenResponse:
    """UserService.build_token_response 静态方法"""

    def test_builds_response_with_user_and_access_token(self, strong_hash: str) -> None:
        """正常路径：构造的 TokenResponse 包含 user + access_token"""
        user = make_user(password_hash=strong_hash)
        user_response = UserResponse.model_validate(user)
        access = "fake-access-token-string"

        result = UserService.build_token_response(user_response, access)

        assert isinstance(result, TokenResponse)
        assert result.user.id == user.id
        assert result.access_token == access
        # 默认值由 Pydantic model 提供
        assert result.token_type == "bearer"
        assert result.expires_in == 900  # 15min


# ==================== AuthRouter 集成测试 ====================


def _build_test_app(
    mock_session: AsyncMock,
    mock_repo: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> FastAPI:
    """构造最小化测试用 FastAPI app

    - 注册异常中间件：让 422/409/401 响应格式与生产一致
    - 注入 auth_router
    - 通过 dependency_overrides 替换 get_db_session
    - 通过 monkeypatch 替换 UserRepository 类（已在上层调用）
    """
    app = FastAPI()
    add_exception_middleware(app)
    app.include_router(auth_router)

    async def override_session() -> AsyncSession:
        yield mock_session

    app.dependency_overrides[get_db_session] = override_session
    return app


@pytest.fixture
def make_app(monkeypatch: pytest.MonkeyPatch):
    """工厂 fixture：返回构造 test app 的可调用对象

    返回函数 (mock_session, mock_repo) -> FastAPI，
    允许每个测试类自定义 mock 行为。
    """
    def _factory(mock_session: AsyncMock, mock_repo: AsyncMock) -> FastAPI:
        # 把 UserRepository 类替换为返回我们 mock 的 lambda
        monkeypatch.setattr(
            "app.domain.user.service.UserRepository",
            lambda session: mock_repo,
        )
        return _build_test_app(mock_session, mock_repo, monkeypatch)
    return _factory


@pytest.fixture
async def http_client():
    """httpx AsyncClient 工厂 fixture

    用法：
        async def test_x(http_client, make_app):
            mock_session = make_mock_session()
            mock_repo = AsyncMock(spec=UserRepository)
            mock_repo.<method>.return_value = ...
            app = make_app(mock_session, mock_repo)
            client = await http_client(app)
            r = await client.post(...)
    """
    clients: list[AsyncClient] = []

    async def _factory(app: FastAPI) -> AsyncClient:
        transport = ASGITransport(app=app)
        c = AsyncClient(transport=transport, base_url="http://test")
        clients.append(c)
        return c

    yield _factory

    for c in clients:
        await c.aclose()


class TestAuthRouterRegister:
    """POST /api/auth/register 集成测试"""

    @pytest.mark.asyncio
    async def test_201_with_tokens_and_refresh_cookie(
        self, make_app, http_client, strong_hash: str,
    ) -> None:
        """正常路径：201 + access_token（响应体）+ Set-Cookie(refresh_token)"""
        mock_session = make_mock_session()
        mock_repo = AsyncMock(spec=UserRepository)
        user = make_user(
            email="newuser1@example.com",
            password_hash=strong_hash,
            name="New User",
        )
        mock_repo.exists_by_email.return_value = False
        mock_repo.create.return_value = user
        app = make_app(mock_session, mock_repo)
        client = await http_client(app)

        r = await client.post(
            "/api/auth/register",
            json={
                "email": "newuser1@example.com",
                "password": STRONG_PWD,
                "password_confirm": STRONG_PWD,
                "name": "New User",
            },
        )

        assert r.status_code == 201
        body = r.json()
        # 1. 响应体字段
        assert body["user"]["email"] == "newuser1@example.com"
        assert body["user"]["id"] == str(user.id)
        assert body["user"]["name"] == "New User"
        assert body["token_type"] == "bearer"
        assert body["expires_in"] == 900
        assert isinstance(body["access_token"], str) and len(body["access_token"]) > 20
        assert "password_hash" not in body["user"]
        # 2. Set-Cookie
        set_cookie = r.headers.get("set-cookie", "")
        assert "refresh_token=" in set_cookie
        assert "HttpOnly" in set_cookie
        assert "Path=/api/auth/refresh" in set_cookie
        assert "SameSite=lax" in set_cookie

    @pytest.mark.asyncio
    async def test_409_when_email_exists(
        self, make_app, http_client,
    ) -> None:
        """异常路径：邮箱已注册 → 409 + error_code=RES_002"""
        mock_session = make_mock_session()
        mock_repo = AsyncMock(spec=UserRepository)
        mock_repo.exists_by_email.return_value = True
        app = make_app(mock_session, mock_repo)
        client = await http_client(app)

        r = await client.post(
            "/api/auth/register",
            json={
                "email": "dup@example.com",
                "password": STRONG_PWD,
                "password_confirm": STRONG_PWD,
            },
        )
        assert r.status_code == 409
        body = r.json()
        assert body["error_code"] == "RES_002"
        assert "已被注册" in body["detail"]
        # create 不该被调用
        mock_repo.create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_422_when_email_format_invalid(
        self, make_app, http_client,
    ) -> None:
        """异常路径：邮箱格式不合法 → 422 + VAL_001"""
        mock_session = make_mock_session()
        mock_repo = AsyncMock(spec=UserRepository)
        app = make_app(mock_session, mock_repo)
        client = await http_client(app)

        r = await client.post(
            "/api/auth/register",
            json={
                "email": "not-an-email",
                "password": STRONG_PWD,
                "password_confirm": STRONG_PWD,
            },
        )
        assert r.status_code == 422
        assert r.json()["error_code"] == "VAL_001"

    @pytest.mark.asyncio
    async def test_422_when_password_in_blacklist(
        self, make_app, http_client,
    ) -> None:
        """异常路径：密码在黑名单 → 422 + VAL_001"""
        mock_session = make_mock_session()
        mock_repo = AsyncMock(spec=UserRepository)
        app = make_app(mock_session, mock_repo)
        client = await http_client(app)

        r = await client.post(
            "/api/auth/register",
            json={
                "email": "weak@example.com",
                "password": WEAK_PWD_BLACKLIST,  # "password" 在黑名单
                "password_confirm": WEAK_PWD_BLACKLIST,
            },
        )
        assert r.status_code == 422
        body = r.json()
        assert body["error_code"] == "VAL_001"
        # 实际错误消息是「密码必须同时包含字母和数字」，需保证 422 状态码 + VAL_001
        assert "密码" in body["detail"]

    @pytest.mark.asyncio
    async def test_422_when_password_too_short(
        self, make_app, http_client,
    ) -> None:
        """异常路径：密码长度 < 8 → 422 + VAL_001"""
        mock_session = make_mock_session()
        mock_repo = AsyncMock(spec=UserRepository)
        app = make_app(mock_session, mock_repo)
        client = await http_client(app)

        r = await client.post(
            "/api/auth/register",
            json={
                "email": "short@example.com",
                "password": "Ab1",  # 3 位
                "password_confirm": "Ab1",
            },
        )
        assert r.status_code == 422
        body = r.json()
        assert body["error_code"] == "VAL_001"
        # 实际错误消息是「String should have at least 8 characters」，需保证 422 状态码 + VAL_001
        assert "8" in body["detail"] or "characters" in body["detail"]

    @pytest.mark.asyncio
    async def test_422_when_passwords_mismatch(
        self, make_app, http_client,
    ) -> None:
        """异常路径：两次密码不一致 → 422 + VAL_001"""
        mock_session = make_mock_session()
        mock_repo = AsyncMock(spec=UserRepository)
        app = make_app(mock_session, mock_repo)
        client = await http_client(app)

        r = await client.post(
            "/api/auth/register",
            json={
                "email": "mismatch@example.com",
                "password": STRONG_PWD,
                "password_confirm": "DifferentPwd#1234",
            },
        )
        assert r.status_code == 422
        body = r.json()
        assert body["error_code"] == "VAL_001"
        assert "不一致" in body["detail"]

    @pytest.mark.asyncio
    async def test_422_when_password_contains_email_local(
        self, make_app, http_client,
    ) -> None:
        """异常路径：密码包含邮箱 local part → 422 + VAL_001"""
        mock_session = make_mock_session()
        mock_repo = AsyncMock(spec=UserRepository)
        app = make_app(mock_session, mock_repo)
        client = await http_client(app)

        r = await client.post(
            "/api/auth/register",
            json={
                "email": "alice@example.com",
                "password": "alice123Strong",  # 包含 "alice"
                "password_confirm": "alice123Strong",
            },
        )
        assert r.status_code == 422
        body = r.json()
        assert body["error_code"] == "VAL_001"
        assert "邮箱" in body["detail"]


class TestAuthRouterLogin:
    """POST /api/auth/login 集成测试"""

    @pytest.mark.asyncio
    async def test_200_with_tokens_and_refresh_cookie(
        self, make_app, http_client, strong_hash: str,
    ) -> None:
        """正常路径：200 + access_token + Set-Cookie"""
        mock_session = make_mock_session()
        mock_repo = AsyncMock(spec=UserRepository)
        user = make_user(email="loginuser@example.com", password_hash=strong_hash)
        mock_repo.get_by_email.return_value = user
        app = make_app(mock_session, mock_repo)
        client = await http_client(app)

        r = await client.post(
            "/api/auth/login",
            json={"email": "loginuser@example.com", "password": STRONG_PWD},
        )

        assert r.status_code == 200
        body = r.json()
        assert body["user"]["email"] == "loginuser@example.com"
        assert body["user"]["id"] == str(user.id)
        assert body["token_type"] == "bearer"
        assert body["expires_in"] == 900
        assert isinstance(body["access_token"], str)
        # Cookie
        set_cookie = r.headers.get("set-cookie", "")
        assert "refresh_token=" in set_cookie
        assert "HttpOnly" in set_cookie

    @pytest.mark.asyncio
    async def test_401_when_user_not_found(
        self, make_app, http_client,
    ) -> None:
        """异常路径：邮箱不存在 → 401 + AUTH_001"""
        mock_session = make_mock_session()
        mock_repo = AsyncMock(spec=UserRepository)
        mock_repo.get_by_email.return_value = None
        app = make_app(mock_session, mock_repo)
        client = await http_client(app)

        r = await client.post(
            "/api/auth/login",
            json={"email": "none@example.com", "password": STRONG_PWD},
        )
        assert r.status_code == 401
        body = r.json()
        assert body["error_code"] == "AUTH_001"
        assert "邮箱或密码错误" in body["detail"]

    @pytest.mark.asyncio
    async def test_401_when_password_wrong(
        self, make_app, http_client, strong_hash: str,
    ) -> None:
        """异常路径：密码错误 → 401 + AUTH_001"""
        mock_session = make_mock_session()
        mock_repo = AsyncMock(spec=UserRepository)
        user = make_user(password_hash=strong_hash)
        mock_repo.get_by_email.return_value = user
        app = make_app(mock_session, mock_repo)
        client = await http_client(app)

        r = await client.post(
            "/api/auth/login",
            json={"email": "x@example.com", "password": WRONG_PWD},
        )
        assert r.status_code == 401
        body = r.json()
        assert body["error_code"] == "AUTH_001"
        # 与"用户不存在"返回相同 detail，防枚举
        assert "邮箱或密码错误" in body["detail"]

    @pytest.mark.asyncio
    async def test_422_when_email_invalid(
        self, make_app, http_client,
    ) -> None:
        """异常路径：邮箱格式不合法 → 422 + VAL_001"""
        mock_session = make_mock_session()
        mock_repo = AsyncMock(spec=UserRepository)
        app = make_app(mock_session, mock_repo)
        client = await http_client(app)

        r = await client.post(
            "/api/auth/login",
            json={"email": "invalid", "password": STRONG_PWD},
        )
        assert r.status_code == 422
        assert r.json()["error_code"] == "VAL_001"

    @pytest.mark.asyncio
    async def test_422_when_password_empty(
        self, make_app, http_client,
    ) -> None:
        """异常路径：密码为空 → 422 + VAL_001"""
        mock_session = make_mock_session()
        mock_repo = AsyncMock(spec=UserRepository)
        app = make_app(mock_session, mock_repo)
        client = await http_client(app)

        r = await client.post(
            "/api/auth/login",
            json={"email": "x@example.com", "password": ""},
        )
        assert r.status_code == 422


class TestAuthRouterRefresh:
    """POST /api/auth/refresh 集成测试"""

    @pytest.mark.asyncio
    async def test_200_with_new_tokens_and_rotated_cookie(
        self, make_app, http_client, strong_hash: str,
    ) -> None:
        """正常路径：200 + 新 access + 旋转的 Set-Cookie（新值 != 旧值）"""
        mock_session = make_mock_session()
        mock_repo = AsyncMock(spec=UserRepository)
        user = make_user(password_hash=strong_hash)
        mock_repo.get_by_id.return_value = user
        app = make_app(mock_session, mock_repo)
        client = await http_client(app)

        old_refresh = create_refresh_token(user.id)
        r = await client.post(
            "/api/auth/refresh",
            headers={"Cookie": f"refresh_token={old_refresh}"},
        )

        assert r.status_code == 200
        body = r.json()
        assert body["user"]["id"] == str(user.id)
        assert isinstance(body["access_token"], str)
        # Set-Cookie 旋转：新值 != 旧值
        set_cookie = r.headers.get("set-cookie", "")
        assert "refresh_token=" in set_cookie
        new_value = set_cookie.split("refresh_token=", 1)[1].split(";", 1)[0]
        assert new_value != old_refresh
        # Cookie 属性正确
        assert "HttpOnly" in set_cookie
        assert "Path=/api/auth/refresh" in set_cookie

    @pytest.mark.asyncio
    async def test_401_without_cookie(
        self, make_app, http_client,
    ) -> None:
        """异常路径：缺少 refresh_token Cookie → 401 + AUTH_001"""
        mock_session = make_mock_session()
        mock_repo = AsyncMock(spec=UserRepository)
        app = make_app(mock_session, mock_repo)
        client = await http_client(app)

        r = await client.post("/api/auth/refresh")
        assert r.status_code == 401
        body = r.json()
        assert body["error_code"] == "AUTH_001"
        # 不该去查 DB
        mock_repo.get_by_id.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_401_with_malformed_cookie(
        self, make_app, http_client,
    ) -> None:
        """异常路径：refresh token 是垃圾字符串 → 401 + AUTH_001"""
        mock_session = make_mock_session()
        mock_repo = AsyncMock(spec=UserRepository)
        app = make_app(mock_session, mock_repo)
        client = await http_client(app)

        r = await client.post(
            "/api/auth/refresh",
            headers={"Cookie": "refresh_token=garbage-not-a-jwt"},
        )
        assert r.status_code == 401
        body = r.json()
        assert body["error_code"] == "AUTH_001"
        assert "无效" in body["detail"] or "缺少" in body["detail"] or "失败" in body["detail"]

    @pytest.mark.asyncio
    async def test_401_with_access_token_used_as_refresh(
        self, make_app, http_client, strong_hash: str,
    ) -> None:
        """异常路径：用 access token 当 refresh → 401（类型错）"""
        from app.core.security import create_access_token

        mock_session = make_mock_session()
        mock_repo = AsyncMock(spec=UserRepository)
        user = make_user(password_hash=strong_hash)
        mock_repo.get_by_id.return_value = user
        app = make_app(mock_session, mock_repo)
        client = await http_client(app)

        # 即使 DB 中有用户，type=access 也被拒绝
        access = create_access_token(user.id)
        r = await client.post(
            "/api/auth/refresh",
            headers={"Cookie": f"refresh_token={access}"},
        )
        assert r.status_code == 401
        body = r.json()
        assert body["error_code"] == "AUTH_001"
        # 实际错误消息是「refresh token 无效或已过期」（Service 层防枚举统一文案）
        assert "无效" in body["detail"] or "过期" in body["detail"]

    @pytest.mark.asyncio
    async def test_401_with_expired_cookie(
        self, make_app, http_client, test_settings,
    ) -> None:
        """异常路径：过期 refresh token → 401 + AUTH_001"""
        mock_session = make_mock_session()
        mock_repo = AsyncMock(spec=UserRepository)
        app = make_app(mock_session, mock_repo)
        client = await http_client(app)

        # 手动签发一个已过期的 refresh token
        now = datetime.now(tz=UTC)
        payload = {
            "sub": str(uuid.uuid4()),
            "type": "refresh",
            "iat": int((now - timedelta(days=10)).timestamp()),
            "exp": int((now - timedelta(days=1)).timestamp()),
            "jti": "expired-jti",
        }
        expired = jwt.encode(
            payload,
            test_settings.jwt_secret_key,
            algorithm=test_settings.jwt_algorithm,
        )
        r = await client.post(
            "/api/auth/refresh",
            headers={"Cookie": f"refresh_token={expired}"},
        )
        assert r.status_code == 401
        body = r.json()
        assert body["error_code"] == "AUTH_001"
        assert "过期" in body["detail"]

    @pytest.mark.asyncio
    async def test_404_when_user_deleted(
        self, make_app, http_client,
    ) -> None:
        """异常路径：refresh 合法但用户已被删除 → 404 + RES_001"""
        mock_session = make_mock_session()
        mock_repo = AsyncMock(spec=UserRepository)
        mock_repo.get_by_id.return_value = None  # 用户已被删除
        app = make_app(mock_session, mock_repo)
        client = await http_client(app)

        refresh = create_refresh_token(uuid.uuid4())
        r = await client.post(
            "/api/auth/refresh",
            headers={"Cookie": f"refresh_token={refresh}"},
        )
        assert r.status_code == 404
        body = r.json()
        assert body["error_code"] == "RES_001"
        assert "不存在" in body["detail"]
