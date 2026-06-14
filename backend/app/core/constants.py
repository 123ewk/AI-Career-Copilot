"""全局常量与枚举定义

职责：
- 定义岗位状态枚举（JobStatus），对应 PRD 第 6 节求职状态机
- 定义资历等级（SeniorityLevel），对应 JD 分析输出的 seniority 字段
- 定义难度等级（DifficultyLevel），对应 JD 分析输出的 difficulty 字段
- 定义投递状态（ApplicationStatus），对应投递记录的状态流转
- 定义 Agent 任务状态（AgentTaskStatus），对应 Agent 执行生命周期
- 定义记忆类型（MemoryType），对应 Agent 记忆的分类
- 定义沟通话术类型（GreetingType），对应 Communication Agent 输出
- 定义优化建议优先级（SuggestionPriority），对应简历优化建议
- 定义来源平台（JobSource），对应招聘网站适配器

设计动机：
- 枚举集中管理，避免魔法字符串散落在各层
- 使用 str + Enum 继承，序列化时直接得到字符串值，前端无需额外映射
- 每个枚举值附带中文 label，方便日志和 API 响应中展示
"""

from enum import Enum


# ==================== 岗位状态（PRD 6.1 状态机）====================

class JobStatus(str, Enum):
    """岗位生命周期状态

    状态流转（PRD 6.1）：
    DISCOVERED → ANALYZED → MATCHED → RECOMMENDED → COMMUNICATION_READY
                                                              │
                         ┌───────────────────────────────────┤
                         ↓                                   ↓
                   APPLIED                              SKIPPED
                     │
         ┌───────────┼───────────┐
         ↓           ↓           ↓
      VIEWED     REJECTED    INTERVIEW
                             │
                    ┌────────┼────────┐
                    ↓        ↓        ↓
              SCHEDULED  PASSED   FAILED
                    │
                    ↓
               OFFERED
    """

    DISCOVERED = "discovered"
    ANALYZED = "analyzed"
    MATCHED = "matched"
    RECOMMENDED = "recommended"
    COMMUNICATION_READY = "communication_ready"
    APPLIED = "applied"
    SKIPPED = "skipped"
    VIEWED = "viewed"
    REJECTED = "rejected"
    INTERVIEW = "interview"
    SCHEDULED = "scheduled"
    PASSED = "passed"
    FAILED = "failed"
    OFFERED = "offered"


# 岗位状态中文标签映射，日志和响应中使用
JOB_STATUS_LABELS: dict[JobStatus, str] = {
    JobStatus.DISCOVERED: "已发现",
    JobStatus.ANALYZED: "已分析",
    JobStatus.MATCHED: "已匹配",
    JobStatus.RECOMMENDED: "已推荐",
    JobStatus.COMMUNICATION_READY: "沟通就绪",
    JobStatus.APPLIED: "已投递",
    JobStatus.SKIPPED: "已跳过",
    JobStatus.VIEWED: "HR已查看",
    JobStatus.REJECTED: "已拒绝",
    JobStatus.INTERVIEW: "进入面试",
    JobStatus.SCHEDULED: "面试已安排",
    JobStatus.PASSED: "面试通过",
    JobStatus.FAILED: "面试未通过",
    JobStatus.OFFERED: "已发Offer",
}

# 合法的状态流转映射，用于校验状态变更是否合法
# key: 当前状态, value: 允许转移到的状态集合
JOB_STATUS_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.DISCOVERED: {JobStatus.ANALYZED, JobStatus.SKIPPED},
    JobStatus.ANALYZED: {JobStatus.MATCHED, JobStatus.SKIPPED},
    JobStatus.MATCHED: {JobStatus.RECOMMENDED, JobStatus.SKIPPED},
    JobStatus.RECOMMENDED: {JobStatus.COMMUNICATION_READY, JobStatus.SKIPPED},
    JobStatus.COMMUNICATION_READY: {JobStatus.APPLIED, JobStatus.SKIPPED},
    JobStatus.APPLIED: {JobStatus.VIEWED, JobStatus.REJECTED, JobStatus.INTERVIEW},
    JobStatus.VIEWED: {JobStatus.REJECTED, JobStatus.INTERVIEW},
    JobStatus.INTERVIEW: {JobStatus.SCHEDULED, JobStatus.PASSED, JobStatus.FAILED},
    JobStatus.SCHEDULED: {JobStatus.PASSED, JobStatus.FAILED},
    JobStatus.PASSED: {JobStatus.OFFERED},
    # 终态，不可再转移
    JobStatus.SKIPPED: set(),
    JobStatus.REJECTED: set(),
    JobStatus.FAILED: set(),
    JobStatus.OFFERED: set(),
}


# ==================== 资历等级（PRD 5.2 JD 分析输出）====================

class SeniorityLevel(str, Enum):
    """岗位资历要求等级

    对应 JD 分析输出的 seniority 字段（PRD 5.2）
    """

    INTERN = "intern"
    ENTRY = "entry"
    JUNIOR = "junior"
    MID = "mid"
    SENIOR = "senior"
    LEAD = "lead"
    PRINCIPAL = "principal"


SENIORITY_LEVEL_LABELS: dict[SeniorityLevel, str] = {
    SeniorityLevel.INTERN: "实习",
    SeniorityLevel.ENTRY: "应届/入门",
    SeniorityLevel.JUNIOR: "初级(1-3年)",
    SeniorityLevel.MID: "中级(3-5年)",
    SeniorityLevel.SENIOR: "高级(5-8年)",
    SeniorityLevel.LEAD: "专家/负责人(8-10年)",
    SeniorityLevel.PRINCIPAL: "首席/总监(10年+)",
}

# 资历等级排序值，用于比较和筛选
SENIORITY_LEVEL_ORDER: dict[SeniorityLevel, int] = {
    SeniorityLevel.INTERN: 0,
    SeniorityLevel.ENTRY: 1,
    SeniorityLevel.JUNIOR: 2,
    SeniorityLevel.MID: 3,
    SeniorityLevel.SENIOR: 4,
    SeniorityLevel.LEAD: 5,
    SeniorityLevel.PRINCIPAL: 6,
}


# ==================== 难度等级（PRD 5.2 JD 分析输出）====================

class DifficultyLevel(str, Enum):
    """岗位难度评级

    对应 JD 分析输出的 difficulty 字段（PRD 5.2）
    综合考虑：技能要求广度、经验年限、竞争激烈度
    """

    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"
    EXPERT = "expert"


DIFFICULTY_LEVEL_LABELS: dict[DifficultyLevel, str] = {
    DifficultyLevel.EASY: "简单",
    DifficultyLevel.MEDIUM: "中等",
    DifficultyLevel.HARD: "困难",
    DifficultyLevel.EXPERT: "极难",
}

DIFFICULTY_LEVEL_ORDER: dict[DifficultyLevel, int] = {
    DifficultyLevel.EASY: 0,
    DifficultyLevel.MEDIUM: 1,
    DifficultyLevel.HARD: 2,
    DifficultyLevel.EXPERT: 3,
}


# ==================== Agent 任务状态 ====================

class AgentTaskStatus(str, Enum):
    """Agent 任务执行状态

    对应 Agent Runtime 的任务生命周期（PRD 4.2.3）
    """

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


AGENT_TASK_STATUS_LABELS: dict[AgentTaskStatus, str] = {
    AgentTaskStatus.PENDING: "待执行",
    AgentTaskStatus.RUNNING: "执行中",
    AgentTaskStatus.COMPLETED: "已完成",
    AgentTaskStatus.FAILED: "执行失败",
    AgentTaskStatus.CANCELLED: "已取消",
}


# ==================== 记忆类型（PRD 5.5 / 10.2 AgentMemory）====================

class MemoryType(str, Enum):
    """Agent 记忆类型

    对应 PRD 10.2 AgentMemory 的 memory_type 字段
    - short_term: 当前会话内的临时记忆，会话结束即清除
    - long_term: 跨会话持久化的用户偏好和求职历史
    - reflection: Agent 反思总结，用于策略优化
    """

    SHORT_TERM = "short_term"
    LONG_TERM = "long_term"
    REFLECTION = "reflection"


MEMORY_TYPE_LABELS: dict[MemoryType, str] = {
    MemoryType.SHORT_TERM: "短期记忆",
    MemoryType.LONG_TERM: "长期记忆",
    MemoryType.REFLECTION: "反思总结",
}


# ==================== 沟通话术类型（PRD 5.4）====================

class GreetingType(str, Enum):
    """沟通话术类型

    对应 Communication Agent 输出的 greeting_type（PRD 5.4）
    """

    PROACTIVE = "proactive"
    REPLY = "reply"
    INTERVIEW_ACCEPT = "interview_accept"
    INTERVIEW_RESCHEDULE = "interview_reschedule"


GREETING_TYPE_LABELS: dict[GreetingType, str] = {
    GreetingType.PROACTIVE: "主动打招呼",
    GreetingType.REPLY: "回复HR",
    GreetingType.INTERVIEW_ACCEPT: "接受面试邀请",
    GreetingType.INTERVIEW_RESCHEDULE: "协商面试时间",
}


# ==================== 优化建议优先级（PRD 5.3）====================

class SuggestionPriority(str, Enum):
    """简历优化建议优先级

    对应 Resume Agent 输出的 optimization_suggestions.priority（PRD 5.3）
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


SUGGESTION_PRIORITY_LABELS: dict[SuggestionPriority, str] = {
    SuggestionPriority.HIGH: "高",
    SuggestionPriority.MEDIUM: "中",
    SuggestionPriority.LOW: "低",
}

SUGGESTION_PRIORITY_ORDER: dict[SuggestionPriority, int] = {
    SuggestionPriority.HIGH: 0,
    SuggestionPriority.MEDIUM: 1,
    SuggestionPriority.LOW: 2,
}


# ==================== 来源平台（PRD 7.2）====================

class JobSource(str, Enum):
    """招聘平台来源

    对应 PRD 7.2 平台适配器设计，Job 实体的 source 字段
    """

    BOSS = "boss"
    LIEPIN = "liepin"
    ZHILIAN = "zhilian"
    SHIXISENG = "shixiseng"


JOB_SOURCE_LABELS: dict[JobSource, str] = {
    JobSource.BOSS: "Boss直聘",
    JobSource.LIEPIN: "猎聘",
    JobSource.ZHILIAN: "智联招聘",
    JobSource.SHIXISENG: "实习僧",
}


# ==================== 匹配分数阈值 ====================

# 匹配分数区间定义，用于投递推荐决策
MATCH_SCORE_HIGH = 80  # ≥80 高度匹配，建议直接投递
MATCH_SCORE_MEDIUM = 60  # ≥60 中度匹配，建议优化后投递
# <60 低匹配，建议跳过或大幅优化


# ==================== 限流与安全常量 ====================

# API 限流：每用户每分钟最大请求数（PRD 9.2）
RATE_LIMIT_PER_MINUTE = 60

# JWT Token 过期时间（PRD 9.2）
ACCESS_TOKEN_EXPIRE_MINUTES = 15
REFRESH_TOKEN_EXPIRE_DAYS = 7

# Agent 任务最大重试次数（PRD 9.3）
AGENT_MAX_RETRIES = 3

# 策略报告自动生成的最低投递数（PRD 5.5）
STRATEGY_MIN_APPLICATIONS = 10
