"""API 路由聚合

集中导入所有 router 实例，供 main.py 一次性挂载。
"""

from app.api.routers import agent, auth, extension_log, jobs, match, resume, session, task, user, workflow

__all__ = [
    "agent",
    "auth",
    "extension_log",
    "jobs",
    "match",
    "resume",
    "session",
    "task",
    "user",
    "workflow",
]
