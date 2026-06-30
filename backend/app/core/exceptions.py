"""统一异常体系

职责：
- 定义基础异常类 AppException，携带 error_code / detail / extra
- 定义业务异常（4xx）和基础设施异常（5xx）两大分支
- 提供 to_http_response() 方法，中间件可直接调用生成统一响应

设计动机：
- 异常是跨层传递信息的唯一合法手段，必须结构化、可追踪
- 区分 BusinessException（客户端问题）和 InfrastructureException（服务端问题），
  中间件据此决定日志级别和是否告警
- error_code 采用「模块+编号」格式，前端可据此做国际化，后端可快速定位模块
- extra 字段用于携带调试上下文（如字段名、约束值），但不会暴露给前端
"""

from typing import Any


# ==================== 基础异常 ====================

class AppException(Exception):
    """所有应用异常的基类

    不直接抛出，由子类继承使用。
    携带 HTTP 状态码、业务错误码、用户可读信息、调试上下文。

    Attributes:
        status_code: HTTP 状态码
        error_code: 业务错误码，格式如 AUTH_001、JOB_002
        detail: 用户可读的错误描述
        extra: 调试上下文，仅写入日志，不返回给前端
    """

    status_code: int = 500
    error_code: str = "SYS_000"
    detail: str = "服务内部错误"

    def __init__(
        self,
        detail: str | None = None,
        error_code: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        # 允许实例化时覆盖类默认值，避免为每种场景都建子类
        if detail is not None:
            self.detail = detail
        if error_code is not None:
            self.error_code = error_code
        self.extra = extra or {}
        super().__init__(self.detail)

    def to_http_response(self) -> dict[str, Any]:
        """生成统一格式的 HTTP 响应体

        中间件调用此方法，保证所有错误响应结构一致：
        {"error_code": "AUTH_001", "detail": "Token 已过期"}
        extra 不包含在内，防止敏感信息泄露
        """
        return {
            "error_code": self.error_code,
            "detail": self.detail,
        }

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"status_code={self.status_code}, "
            f"error_code={self.error_code!r}, "
            f"detail={self.detail!r}, "
            f"extra={self.extra})"
        )


# ==================== 业务异常（4xx）====================

class BusinessException(AppException):
    """业务异常基类

    所有 4xx 错误的父类。表示客户端请求有问题：
    参数错误、权限不足、资源不存在等。
    中间件对此类异常打 WARN 日志，不触发告警。
    """
    status_code = 400
    error_code = "BIZ_000"
    detail = "业务处理失败"


class ValidationError(BusinessException):
    """参数校验失败 (400)

    场景：请求体字段缺失、类型错误、值越界等
    典型用法：raise ValidationError(detail="邮箱格式不正确", extra={"field": "email"})
    """
    status_code = 400
    error_code = "VAL_001"
    detail = "参数校验失败"


class AuthenticationError(BusinessException):
    """认证失败 (401)

    场景：Token 缺失/过期/无效、凭证错误
    注意：不要在 detail 中泄露"用户名不存在"还是"密码错误"，防止枚举攻击
    """
    status_code = 401
    error_code = "AUTH_001"
    detail = "认证失败，请重新登录"


class AuthorizationError(BusinessException):
    """权限不足 (403)

    场景：已认证但无权访问该资源（如普通用户访问管理员接口）
    与 401 的区别：401 是"你是谁"，403 是"你不能这么做"
    """
    status_code = 403
    error_code = "AUTH_002"
    detail = "权限不足"


class ResourceNotFoundError(BusinessException):
    """资源不存在 (404)

    场景：查询的 ID 不存在、API 路径错误
    """
    status_code = 404
    error_code = "RES_001"
    detail = "请求的资源不存在"


class ConflictError(BusinessException):
    """资源冲突 (409)

    场景：唯一约束冲突（用户名已注册）、乐观锁版本冲突
    """
    status_code = 409
    error_code = "RES_002"
    detail = "资源冲突"


class TaskStateError(ConflictError):
    """任务状态机非法转换 (409)

    场景：Consumer 尝试将 PENDING 任务直接标记为 COMPLETED，
    或 COMPLETED 任务再次标记为 RUNNING 等违反状态流转规则的操作。
    """
    status_code = 409
    error_code = "TASK_001"
    detail = "任务状态不允许此操作"


class RateLimitError(BusinessException):
    """请求限流 (429)

    场景：短时间内请求过多，触发限流策略
    """
    status_code = 429
    error_code = "RATE_001"
    detail = "请求过于频繁，请稍后重试"


# ==================== 基础设施异常（5xx）====================

class InfrastructureException(AppException):
    """基础设施异常基类

    所有 5xx 错误的父类。表示服务端基础设施出问题：
    数据库挂了、缓存不可用、外部服务超时等。
    中间件对此类异常打 ERROR 日志并触发告警。
    """
    status_code = 500
    error_code = "INF_000"
    detail = "服务内部错误"


class DatabaseError(InfrastructureException):
    """数据库异常 (500)

    场景：连接池耗尽、SQL 执行失败、死锁
    注意：不要把原始 SQL 或数据库错误信息暴露给前端
    """
    status_code = 500
    error_code = "DB_001"
    detail = "数据库操作失败"


class CacheError(InfrastructureException):
    """缓存异常 (500)

    场景：Redis 连接断开、序列化失败
    设计考量：缓存降级场景下可 catch 此异常后走数据库，
    不一定都要 500，但异常本身必须抛出以便监控
    """
    status_code = 500
    error_code = "CACHE_001"
    detail = "缓存服务异常"


class MessageQueueError(InfrastructureException):
    """消息队列异常 (500)

    场景：RabbitMQ 连接断开、消息发布失败
    """
    status_code = 500
    error_code = "MQ_001"
    detail = "消息队列服务异常"


class ExternalServiceError(InfrastructureException):
    """外部服务异常 (502)

    场景：LLM API 超时/返回错误、招聘平台接口不可用
    """
    status_code = 502
    error_code = "EXT_001"
    detail = "外部服务不可用"


# ==================== MQ 消费者控制流异常 ====================

class DuplicateMessageError(Exception):
    """业务表唯一约束拒绝的重复消息（消费者控制流信号）

    为什么不是 AppException 子类：
    - 不是 HTTP 错误：消费者内部信号，不返回前端
    - AppException 体系专用于 API 错误响应（4xx/5xx）
    - MQ 消费者基类识别此异常后应 ACK 丢弃，**不重试**（重试也是重复）

    使用场景：
    - MQ 重投导致同 message_id 重复到达（Publisher Confirms + 业务 ACK 竞态）
    - 业务表已有 unique 约束（如 task_id 主键），INSERT 时被数据库拒绝
    - 配合 `domain.common.idempotent.insert_idempotent` 使用，业务 INSERT 失败
      转为此异常，消费者静默 ACK

    业务层使用：
        try:
            instance = await insert_idempotent(session, Task, id=task_id, ...)
        except DuplicateMessageError:
            return  # 业务正常返回，消费者基类会 ACK

    消费者基类处理（在 MessageConsumer._on_message）：
        try:
            await self.handle_message(body)
            await message.ack()
        except DuplicateMessageError as e:
            await message.ack()
            logger.info("重复消息丢弃", extra={"message_id": e.message_id})
    """

    def __init__(self, message_id: str, original_error: Exception | None = None) -> None:
        self.message_id = message_id
        self.original_error = original_error
        super().__init__(f"Duplicate message: {message_id}")


# ==================== 异常 → HTTP 映射表 ====================

# 中间件可通过此映射快速判断异常类型对应的日志级别
# 4xx → WARNING（客户端问题，不告警）
# 5xx → ERROR（服务端问题，需告警）
EXCEPTION_LOG_LEVEL: dict[int, str] = {
    400: "WARNING",
    401: "WARNING",
    403: "WARNING",
    404: "WARNING",
    409: "WARNING",
    429: "WARNING",
    500: "ERROR",
    502: "ERROR",
}
