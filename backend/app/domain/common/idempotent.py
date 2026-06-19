"""幂等插入辅助函数（业务表 unique 约束方案）

为什么需要这个模块：
- 业务表（如 Task、Notification）已有 unique 约束（task_id、notification_id 主键），
  INSERT 天然就是幂等检查
- 不引入额外的 Redis 去重表 / Postgres 去重表
- 配合消费者基类（MessageConsumer）识别 DuplicateMessageError 静默 ACK

为什么用 unique 约束而非应用层查重：
- 天然事务一致：唯一性由 DB 强保证，不存在「查重 + 插入」之间的竞态
- 零额外依赖：复用业务表 PK / unique index
- 性能足够：unique 索引 B-tree 查找 O(log n)，PK 还可走主键索引
- 失败行为可预测：IntegrityError 是同步、确定的，调用方 try/except 即可

为什么用 flush() 而非 commit()：
- flush 触发 SQL 执行（包括 unique 检查），但不提交事务
- 调用方可以在外层 unit of work 中一起 commit
- 与 SQLAlchemy async session 模式一致：add() + flush() + commit()
- 失败时 caller 控制 rollback 时机（避免回滚掉整个事务）
"""

from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import DuplicateMessageError

# 业务 ID 候选 key 列表（与 publisher._BATCH_ID_KEYS 保持一致）
# 提取顺序：business_id > task_id > notification_id > id > message_id
# 业务表 unique 约束应建在其中的一个字段上
# 为什么 business_id 排第一：
# - business_id 是业务方传入的稳定 ID，专门用于幂等
# - id 在 Task 等表中是 UUID PK（每次新生成），不参与业务去重
# - 如果同时传了 business_id 和 id，business_id 才是有意义的业务键
_ID_KEYS: tuple[str, ...] = (
    "business_id",
    "task_id",
    "notification_id",
    "id",
    "message_id",
)


def _extract_business_id(values: dict[str, Any]) -> str:
    """从 INSERT values 中提取业务 ID（用于异常日志）

    优先按 _ID_KEYS 顺序查找；都无值则返回 "unknown"。
    提取失败不影响主流程（异常 message 已包含 model 类名）。
    """
    for key in _ID_KEYS:
        value = values.get(key)
        if value:
            return str(value)
    return "unknown"


async def insert_idempotent[T](
    session: AsyncSession,
    model: type[T],
    **values: Any,
) -> T:
    """幂等插入：重复时抛 DuplicateMessageError

    使用前确保 model 表有 unique 约束在 values 中的某个字段（通常是 task_id / id / business_id）。

    参数：
    - session：SQLAlchemy async session（外层事务）
    - model：ORM Model 类
    - **values：要插入的字段值，至少包含一个 unique 字段

    返回：
    - 已 flush 的 model 实例（尚未 commit，由 caller 控制事务）

    异常：
    - DuplicateMessageError：unique 约束拒绝（消息重复）
    - IntegrityError：其他约束冲突（外键、check 等），由 caller 处理
    - SQLAlchemyError：DB 不可用等基础设施错误

    用法 1：基于业务主键（User / Job 等）
        try:
            user = await insert_idempotent(session, User, id=user_id, email=..., ...)
        except DuplicateMessageError:
            return  # 消费者基类会静默 ACK，不计入失败

    用法 2：基于业务方传入的 business_id（Task 推荐方案）
        try:
            task = await insert_idempotent(
                session, Task,
                user_id=user_id,
                session_id=session_id,
                business_id=f"analyze_jd:{job_id}",  # ← 业务方生成稳定 ID
                task_type="analyze_jd",
                status="PENDING",
            )
        except DuplicateMessageError:
            return  # MQ 重投时同 business_id 触发 unique 冲突

    business_id 命名建议：
    - 格式：f"{task_type}:{business_key}"，如 "analyze_jd:job-uuid-123"
    - 必须稳定：同一业务操作重试时 ID 一致
    - 必须按用户隔离：(user_id, business_id) 联合 unique，多用户环境避免冲突
    """
    instance = model(**values)
    session.add(instance)
    try:
        await session.flush()
        return instance
    except IntegrityError as e:
        # IntegrityError 触发后 session 进入 failed state，
        # 必须在同一 session 内继续使用前 rollback，
        # 否则后续 SQL 会抛 InvalidRequestError
        await session.rollback()

        business_id = _extract_business_id(values)
        # 用 raise from 保留原异常 traceback，便于排查是哪个 unique 约束拒绝
        raise DuplicateMessageError(
            message_id=business_id,
            original_error=e,
        ) from e
