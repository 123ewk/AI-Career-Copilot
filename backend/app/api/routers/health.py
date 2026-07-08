"""Health Router（健康检查）

职责：
- 暴露 /health 端点，供 Docker HEALTHCHECK、负载均衡、监控探针使用
- 仅做最轻量的存活探测，不查询数据库/缓存/MQ，避免探针本身成为故障源

设计动机：
- 健康检查应区分「存活（liveness）」与「就绪（readiness）」
- 当前 /health 只做 liveness：进程在、HTTP 通即返回 200
- 如需 readiness（依赖服务全可用），应另开 /ready 并检查 PG/Redis/MQ

潜在风险：
- 若 /health 做太重（如查数据库），依赖故障时会导致探针失败、容器被误杀
- 本端点被认证/限流/日志中间件白名单放行，返回固定 JSON，不泄露内部状态
"""

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

router = APIRouter(tags=["健康检查"])


@router.get("/health")
async def health_check() -> JSONResponse:
    """存活探针

    返回：
        status: "ok" 的 JSON 响应

    设计说明：
        - 不访问外部依赖，确保即使 PG/Redis/MQ 故障也能返回 200
        - Docker HEALTHCHECK 和 K8s livenessProbe 都依赖此接口
    """
    return JSONResponse(
        content={"status": "ok"},
        status_code=status.HTTP_200_OK,
    )
