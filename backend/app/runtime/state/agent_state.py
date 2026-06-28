"""Agent 运行状态定义

职责：
- 定义 Agent 执行过程中的细粒度状态枚举
- 与 TaskStatus（粗粒度生命周期）互补：TaskStatus 管理任务级生命周期，
  AgentState 管理 Agent 内部执行阶段

设计动机：
- Job Analysis Agent 内部是多阶段流水线（解析 → 提取 → 分析 → 完成），
  前端需要展示「正在提取技能...」等细粒度进度
- AgentState 独立于 TaskStatus：Task 从 RUNNING 到 COMPLETED 期间，
  AgentState 经历 PARSING → EXTRACTING → ANALYZING → COMPLETED
- 状态存储在 Task.result["agent_state"] 字段，不新增列

状态流转：
    PARSING → EXTRACTING → ANALYZING → COMPLETED
                                ↓
                             FAILED

- PARSING: JD 文本预处理（分段、清洗）
- EXTRACTING: 调用 LLM 提取结构化信息
- ANALYZING: Web 搜索补充分析 + 结果聚合
- COMPLETED: 所有阶段完成
- FAILED: 任意阶段失败（携带 error 信息）
"""

from __future__ import annotations

from enum import StrEnum


class AgentState(StrEnum):
    """Agent 执行阶段枚举

    继承 StrEnum：序列化直接输出字符串，可存入 JSONB / 日志 / API 响应。

    与 TaskStatus 的关系：
    - TaskStatus.PENDING: Agent 尚未启动（无 AgentState）
    - TaskStatus.RUNNING + AgentState.PARSING: 正在解析 JD
    - TaskStatus.RUNNING + AgentState.EXTRACTING: 正在调用 LLM
    - TaskStatus.RUNNING + AgentState.ANALYZING: 正在搜索补充
    - TaskStatus.COMPLETED + AgentState.COMPLETED: 全部完成
    - TaskStatus.FAILED + AgentState.FAILED: 执行失败
    """

    PARSING = "PARSING"
    EXTRACTING = "EXTRACTING"
    ANALYZING = "ANALYZING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


# 状态转换白表：定义合法的状态流转
# key=当前状态, value=允许转入的状态集合
VALID_TRANSITIONS: dict[AgentState, set[AgentState]] = {
    AgentState.PARSING: {AgentState.EXTRACTING, AgentState.FAILED},
    AgentState.EXTRACTING: {AgentState.ANALYZING, AgentState.FAILED},
    AgentState.ANALYZING: {AgentState.COMPLETED, AgentState.FAILED},
    AgentState.COMPLETED: set(),  # 终态，不允许再转换
    AgentState.FAILED: set(),  # 终态，不允许再转换
}


def validate_transition(current: AgentState, next_state: AgentState) -> bool:
    """校验状态转换是否合法

    Args:
        current: 当前状态
        next_state: 目标状态

    Returns:
        True 如果转换合法

    Raises:
        ValueError: 转换不合法
    """
    allowed = VALID_TRANSITIONS.get(current, set())
    if next_state not in allowed:
        raise ValueError(
            f"非法状态转换: {current.value} → {next_state.value}，"
            f"允许的目标状态: {[s.value for s in allowed] or '无（终态）'}"
        )
    return True


__all__ = ["AgentState", "VALID_TRANSITIONS", "validate_transition"]
