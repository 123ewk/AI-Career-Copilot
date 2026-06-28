# SQLAlchemy ORM `MissingGreenlet` 风险与 DTO 化方案

> 日期：2026-06-17
> 作者：AI Career Copilot 团队
> 状态：✅ 调查清楚，方案 1 已落地，方案 2/3 待 Q10 业务接入时实施
> 相关代码：[postgres.py:100](file:///g:/my/my_file/AI%20Career%20Copilot/backend/app/infra/database/postgres.py#L100)
> 相关代码：[idempotent.py](file:///g:/my/my_file/AI%20Career%20Copilot/backend/app/domain/common/idempotent.py)
> 相关代码：[e2e 测试脚本（已删除）](file:///g:/my/my_file/AI%20Career%20Copilot/backend/tests/)

---

## 1. 问题背景

项目使用 **FastAPI + SQLAlchemy 2.0 async + asyncpg** 栈。在 Q9 业务幂等消费真机端到端测试（真实 PostgreSQL）中，出现 **`MissingGreenlet: greenlet_spawn has not been called`** 错误。

涉及模块：
- `backend/app/infra/database/postgres.py`（session 工厂）
- `backend/app/domain/common/idempotent.py`（`insert_idempotent` 助手）
- 所有未来要写的 `domain/*/service.py`（业务服务层）

## 2. 问题现象

### 2.1 报错信息

```
sqlalchemy.exc.MissingGreenlet: greenlet_spawn has not been called;
can't await int() here. Was IO attempted in an unexpected place?
```

### 2.2 触发场景

e2e 测试脚本中，**session 关闭后**访问 ORM 实例属性：

```python
async with Sess() as session:
    user_a = User(id=uuid.uuid4(), ...)
    session.add(user_a)
    await session.flush()
    await session.commit()
# ↑ async with 结束 → session.close() → user_a 所有属性被 expire
# ↓ 访问 user_a.id → lazy load SELECT 触发
task = await insert_idempotent(session, Task, user_id=user_a.id, ...)
#                        ^^^^^^^^^
# session 已关闭，user_a.id 触发异步 IO 但 greenlet 上下文未激活
```

### 2.3 影响范围

| 场景 | 是否触发 |
|------|---------|
| **Service 层 commit 后立刻访问属性** | ❌ 不触发（`expire_on_commit=False`） |
| **Session 关闭后访问属性** | ✅ 触发 |
| **跨层（Service → Router）传 ORM 实例** | ✅ 触发（Router 层访问时 session 已关闭） |
| **跨 session 边界传 ORM 实例**（如 Consumer） | ✅ 触发 |
| **Celery/Worker 任务里取 ORM 实例** | ✅ 触发 |

## 3. 根因分析

### 3.1 SQLAlchemy 2.0 ORM Expire 机制

SQLAlchemy 默认在两个时机 **expire** ORM 实例（清空属性缓存，下次访问触发 lazy load SELECT）：

| 时机 | 是否可通过配置关闭 |
|------|-----------------|
| `session.commit()` | ✅ **是**：`expire_on_commit=False` |
| `session.close()` | ❌ **否**：SQLAlchemy 标准行为，无法配置关闭 |
| `session.expire_all()` 显式调用 | ✅ 是：避免显式调用即可 |

**关键事实**：
> `expire_on_commit=False` **只防 commit 后的 expire**，**不防 session 关闭后的 expire**。这是 SQLAlchemy 2.0 的固有行为。

### 3.2 async 模式与 expire 冲突

```
sync 模式：
  session.close() → user_a.id → 同步 SELECT → OK

async 模式：
  session.close() → user_a.id
    → SQLAlchemy 试图发 SELECT
    → 需要 greenlet 上下文（async greenlet 让 sync 代码可以 await）
    → 但 user_a.id 是在 sync 上下文（普通 . 访问）
    → MissingGreenlet 异常
```

**asyncpg + SQLAlchemy 2.0 异步模式**通过 `greenlet` 把同步 ORM API 桥接到 async IO。当 ORM 实例**在 session 内**时，访问属性会通过 greenlet spawn 来发 IO。当 session 已关闭，没有活跃的 greenlet，IO 失败。

### 3.3 为什么 `expire_on_commit=False` 不够

**我之前误判的版本**：
> "项目里 `expire_on_commit=False` 已设置，所以 ORM 访问不会有问题。"

**真实情况**：
- `expire_on_commit=False` **只解决 commit 后访问**
- **不解决** session 关闭后访问 / 跨 session 边界访问 / 跨层传 ORM
- 之前 e2e 失败是后者，**不是**前者

## 4. 排查过程

| 阶段 | 现象 | 假设 | 行动 | 结论 |
|------|------|------|------|------|
| 1 | 测试 #2/#3 OK，#4 抛 `MissingGreenlet` | ORM 字段名错（payload vs input_data）| 读 ORM 模型，对照修正 | 字段名错，修正 |
| 2 | 修正字段后 #4 仍失败 | 多个 ORM add 没按 FK 依赖 | 拆成 `add(user) → flush → add(session) → flush` | FK 顺序错，修正 |
| 3 | 全部成功 commit 后访问 `user_a.id` 仍抛 | 业务逻辑有 bug | 加 `import traceback; traceback.format_exc()` | traceback 指 `user_a.id` |
| 4 | 错误指向 `user_a.id` | session 已 close 触发 expire | 重读 `postgres.py` | 找到 `expire_on_commit=False` 已设 |
| 5 | 发现 `expire_on_commit=False` 已设但仍失败 | 不可能 → 重新理解 SQLAlchemy 机制 | 重读 SQLAlchemy 2.0 文档 | 理解 `session.close()` 也会 expire |
| 6 | 重新组织代码：所有 ORM 访问都在 session 内 + ID 提取到变量 | — | 重写脚本 | ✅ 5 个场景全过 |

**总耗时**：4 轮迭代，从字段名 → FK 顺序 → expire 机制，逐步深入。

## 5. 技术选型分析

### 方案对比

| 方案 | 解决什么 | 不解决什么 | 代价 | 适用阶段 |
|------|---------|----------|------|---------|
| **1. `expire_on_commit=False`** | commit 后立刻访问 | session 关闭后 / 跨层 / 跨 session 边界 | 1 行配置，**已实施** | ✅ 已落地 |
| **2. Service 返回 DTO** | 所有边界场景 | 团队约定 | DTO 维护 + 每层 `model_validate()` | ⏳ Q10 业务接入时强制 |
| **3. 架构规约：跨层禁传 ORM** | 团队一致性 | 单人项目不强制也无所谓 | Lint 规则 + CI 测试 | ⏳ Q10 业务接入时实施 |
| **4. `session.refresh()` 显式重读** | 单个字段访问 | 多次访问的 N+1 风险 | 每次 SELECT 一次 | 临时兜底，**不推荐** |
| **5. `selectinload` / `joinedload`** | relationship 懒加载 | 基础字段的 expire | 需预知访问路径 | 部分场景 |

### 为什么选方案 2 + 3 组合

**理由**：
- 方案 1 已被前序工作实施，但**只解决 1/4 场景**（commit 后）
- 方案 2 是**唯一**能彻底解决所有 4 类场景的方案（DTO 是纯数据，不依赖 session）
- 方案 3 用 Lint 规则把方案 2 的约束**自动化**到 CI，避免靠人记

**反方案分析**：
- ❌ 方案 4（`session.refresh()`）= N+1 风险，且每个 Service 都要记得加
- ❌ 方案 5（预加载）= 不解决 expire 本身，且不适用所有关系
- ❌ 退回 sync 模式 = 阻塞 Event Loop，项目规则禁止

### 方案 1 是不是浪费？

不是。`expire_on_commit=False` 有独立价值：
- **commit 后立刻返回 ORM 属性**（如 FastAPI handler 内 `return task`）→ 无额外 SELECT
- **同 session 内多次访问相同属性** → 缓存命中
- 与方案 2/3 **互补**，不冲突

## 6. 最终解决方案

### 6.1 已落地

| 改动 | 位置 | 状态 |
|------|------|------|
| `expire_on_commit=False` | [postgres.py:100](file:///g:/my/my_file/AI%20Career%20Copilot/backend/app/infra/database/postgres.py#L100) | ✅ |
| 端到端测试 5 场景全过 | Q9 验证 | ✅ |

### 6.2 待落地（Q10 业务接入时强制）

**Step 1.16.9 业务 DTO 化规约**（计划文档）：

```python
# domain/task/service.py
async def create_task(...) -> TaskDTO:  # ← 返回 DTO
    async with pg_session_factory() as session:
        task = await insert_idempotent(session, Task, ...)
        await session.commit()
        return TaskDTO.model_validate(task)  # ← 立刻序列化
```

**Step 1.16.10 跨层禁传 ORM**（计划文档）：
- Router handler 返回类型必须是 Pydantic BaseModel
- Service 跨方法传数据用 DTO 或 Pydantic
- Consumer 接收的 message body 已经是 dict/Pydantic

## 7. 风险与副作用

### 7.1 方案 1 已落地的副作用

- **数据陈旧风险**：commit 后属性不过期，若同事务外有其他连接修改同条记录，本 session 拿到的还是旧值
- **本项目评估**：单进程内没有跨连接修改同一 task 的场景，**风险可接受**
- **未来多读副本 / CQRS 场景需重新评估**

### 7.2 方案 2/3 落地的代价

- **DTO 维护成本**：每张表都要写对应 DTO（可考虑 `automapper` 库但增加依赖）
- **类型映射成本**：ORM 字段 → Pydantic 字段，ENUM / JSONB / 关系都要映射
- **学习成本**：新成员需要理解"为什么不能直接返回 ORM"

### 7.3 不做的风险

如果不实施方案 2/3：
- 业务 Service 长大后，commit 后访问属性 `MissingGreenlet` 仍会偶发
- Bug 难复现（只在 session 关闭后触发）
- 错误堆栈指向 ORM 反射，定位耗时

## 8. 如何预防

### 8.1 代码 Review 检查表

- [ ] Service 方法返回值类型是否是 Pydantic DTO？
- [ ] Router handler 是否直接 return ORM 实例？
- [ ] 跨 session 边界（如 Consumer）是否传了 ORM 实例？
- [ ] Celery / 后台任务是否在取 ORM 实例？

### 8.2 CI / Lint 规则

```python
# 伪 ruff 规则（未来实现）
def check_no_orm_return(tree):
    """禁止 ORM Model 作为函数返回值类型"""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            if "Task" in ast.unparse(node.returns) and "DTO" not in ast.unparse(node.returns):
                yield LintError("禁止返回 ORM Task，应返回 TaskDTO")
```

### 8.3 集成测试

```python
# tests/test_layer_isolation.py
def test_no_orm_leak_to_router():
    """扫描所有 Router handler，确保返回值是 Pydantic DTO"""
    for router_file in glob("api/routers/*.py"):
        tree = ast.parse(router_file.read_text())
        for handler in find_handlers(tree):
            assert_returns_pydantic(handler)
```

### 8.4 监控（未来）

- 生产环境如果出现 `MissingGreenlet` 告警 → 立即定位 ORM 泄露点
- 加结构化日志：每次 ORM 实例跨层传输时记录调用栈

## 9. 核心知识

### 9.1 SQLAlchemy ORM Expire 机制

```
session.commit()           → 触发 expire（可用 expire_on_commit=False 关）
session.close()            → 触发 expire（无法关闭）
session.expire_all()       → 显式触发 expire
session.expire(obj, attr)  → 显式触发单实例单属性 expire
session.refresh(obj)       → 立即重新加载（不 expire，是 reload）
```

### 9.2 async 模式下 ORM 的隐式约束

- ORM 实例**有 session 上下文**，离开 session 上下文访问属性 = 异步 IO in sync context = MissingGreenlet
- **唯一安全**的跨 session 传法：转 DTO / dict / Pydantic
- **sync 模式下无此问题**（因为没有 greenlet 桥接）

### 9.3 Python async/await + sync 互操作

- `greenlet` 是 Stackless Python 的协程机制，比 `asyncio` 更轻量
- SQLAlchemy 2.0 async 用 `greenlet` 把同步 ORM API 桥接到 async IO
- 桥接需要"active greenlet"，session 关闭后无活跃 greenlet → 桥接失败

### 9.4 架构设计原则

> **数据契约（DTO）是异步系统跨边界的"翻译层"**。同步系统内 ORM 实例随便传没问题，async + 多协程 + 多 session 环境下，数据必须序列化才能安全跨界。

## 10. 面试题沉淀

### Q1：FastAPI + SQLAlchemy 2.0 async 下，为什么 `session.close()` 后访问 ORM 属性会报错？

**考察意图**：SQLAlchemy expire 机制 + async 模式

**答题思路**：
1. SQLAlchemy 默认在 `session.close()` 时 expire 所有实例属性
2. 访问 expire 后的属性触发 lazy load（SELECT）
3. async 模式下 lazy load 需要 greenlet 上下文
4. session 已关闭，没有活跃 greenlet → `MissingGreenlet`
5. 与 `expire_on_commit=False` 无关，那是控制 commit 后的 expire

**延伸问题**：
- 怎么彻底避免？答：跨边界用 DTO 序列化
- 能不能关掉 close 时的 expire？答：不能，是 SQLAlchemy 标准行为

### Q2：async Python 项目里，Service 层应该返回 ORM 实例还是 DTO？

**考察意图**：架构设计、数据契约

**答题思路**：
1. 同 session 内（Service → Repository 内部）→ ORM 实例 OK
2. 跨 session 边界 / 跨进程 / 跨层 → 必须 DTO
3. FastAPI handler 返回 ORM → 序列化时 + GIL 死锁风险 + MissingGreenlet 风险
4. 团队应通过 Lint 规则强制

**延伸问题**：
- 同步 ORM 项目呢？答：ORM 跨界问题少（无 greenlet 桥接），但仍有 N+1 / 性能问题
- DTO 库选型？答：手写 vs `pydantic-sqlalchemy` vs `ormar` vs `sqlmodel`

### Q3：什么是 greenlet？为什么 SQLAlchemy async 模式需要它？

**考察意图**：Python 协程底层

**答题思路**：
1. greenlet = Stackless Python 提供的微线程/协程原语
2. asyncio 的协程基于 generator/yield（PEP 255），greenlet 用 C 实现的栈切换
3. SQLAlchemy 2.0 async 模式：业务层是 async，ORM API 内部是 sync
4. 用 greenlet 把 sync ORM API "暂停"，让出控制权给 asyncio
5. session 关闭后 greenlet 上下文销毁，再访问 ORM 触发 IO 时无 greenlet → 报错

**延伸问题**：
- greenlet 和 asyncio.Task 的区别？答：greenlet 是手动切换，asyncio Task 是事件循环调度
- 为什么不用纯 asyncio？答：SQLAlchemy 已有大量 sync API，重写成本太高，greenlet 是折中

---

## 11. 相关资源

- [SQLAlchemy 2.0 Async Documentation](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html)
- [SQLAlchemy Session Basics - When do I construct a Session](https://docs.sqlalchemy.org/en/20/orm/session_basics.html)
- [PEP 3156 - Asyncio](https://www.python.org/dev/peps/pep-3156/)
- [Stackless Python - greenlet](https://greenlet.readthedocs.io/)
- 项目内：[idempotent.py](file:///g:/my/my_file/AI%20Career%20Copilot/backend/app/domain/common/idempotent.py)、[postgres.py:100](file:///g:/my/my_file/AI%20Career%20Copilot/backend/app/infra/database/postgres.py#L100)
