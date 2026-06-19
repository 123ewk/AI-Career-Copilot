"""App core 模块

导出：
- exceptions：异常体系（含 DuplicateMessageError 控制流异常）
- settings：配置加载
- logger：结构化日志
- constants：项目常量
"""

from app.core.exceptions import DuplicateMessageError  # noqa: F401
