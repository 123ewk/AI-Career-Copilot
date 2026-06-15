"""应用配置管理

职责：
- 集中管理所有配置项，从 .env 文件加载
- 强类型校验，启动时即发现配置错误
- 环境隔离：dev/test/staging/prod

设计动机：
- 使用 pydantic-settings 替代手动 os.getenv，获得类型校验和默认值
- 单例模式 + 缓存，避免重复解析环境变量
- 敏感信息（密码/Key）不硬编码，统一从环境变量读取
"""

from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置类

    所有配置项从 .env 文件或环境变量读取
    优先级：环境变量 > .env 文件 > 默认值
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,  # 环境变量不区分大小写
        extra="ignore",
    )

    # ==================== 应用配置 ====================
    app_name: str = "AI Career Copilot"
    app_env: str = "dev"
    debug: bool = False
    log_level: str = "INFO"

    # ==================== PostgreSQL ====================
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "copilot"
    postgres_password: str = "changeme"
    postgres_db: str = "copilot_dev"

    @property
    def postgres_url(self) -> str:
        """PostgreSQL 异步连接 URL"""
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    # ==================== Redis ====================
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: str = ""
    redis_db: int = 0

    @property
    def redis_url(self) -> str:
        """Redis 连接 URL"""
        auth = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"

    # ==================== RabbitMQ ====================
    rabbitmq_host: str = "localhost"
    rabbitmq_port: int = 5672
    rabbitmq_user: str = "guest"
    rabbitmq_password: str = "guest"
    rabbitmq_vhost: str = "/"

    @property
    def rabbitmq_url(self) -> str:
        """RabbitMQ AMQP 连接 URL"""
        return (
            f"amqp://{self.rabbitmq_user}:{self.rabbitmq_password}"
            f"@{self.rabbitmq_host}:{self.rabbitmq_port}/{self.rabbitmq_vhost}"
        )

    # ==================== JWT ====================
    jwt_secret_key: str = "changeme-to-a-random-secret"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 15
    jwt_refresh_token_expire_days: int = 7

    # ==================== LLM ====================
    # 当前使用的 LLM 提供商：deepseek / openai
    llm_provider: str = "deepseek"
    openai_api_key: str = ""
    openai_api_base: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    deepseek_api_key: str = ""
    deepseek_api_base: str = "https://api.deepseek.com/v1"
    deepseek_model: str = "deepseek-chat"

    @property
    def llm_api_key(self) -> str:
        """根据 llm_provider 返回对应的 API Key"""
        if self.llm_provider == "deepseek":
            return self.deepseek_api_key
        return self.openai_api_key

    @property
    def llm_api_base(self) -> str:
        """根据 llm_provider 返回对应的 API Base URL"""
        if self.llm_provider == "deepseek":
            return self.deepseek_api_base
        return self.openai_api_base

    @property
    def llm_model(self) -> str:
        """根据 llm_provider 返回对应的模型名称"""
        if self.llm_provider == "deepseek":
            return self.deepseek_model
        return self.openai_model

    # ==================== 文件上传 ====================
    upload_dir: str = "./uploads"
    max_upload_size_mb: int = 10

    # ==================== CORS 跨域 ====================
    # 精确白名单：Web 前端、第三方回调等固定域名
    # 环境变量格式：逗号分隔，例如
    #   CORS_ALLOW_ORIGINS=https://app.example.com,https://admin.example.com
    cors_allow_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # 是否放行浏览器扩展来源（chrome/moz/edge-extension://*）
    # 开发环境默认开启；生产环境建议关闭并配合 X-Extension-ID 二次校验
    cors_allow_extensions: bool = True

    # 是否允许携带 Cookie / Authorization 等凭据
    # True 时 allow_origins 不能包含 '*'，否则框架启动报错
    cors_allow_credentials: bool = True

    # 预检请求（OPTIONS）结果在浏览器侧缓存秒数
    # 过大：调试期间改 header 需手动清缓存；过小：频繁预检增加延迟
    cors_max_age_seconds: int = 600

    @field_validator("cors_allow_origins", mode="before")
    @classmethod
    # 方法上必须加 @classmethod，这是 Pydantic field_validator 的要求
    def _split_cors_origins(cls, value: object) -> object:
        """将环境变量中的逗号分隔字符串解析为列表

        为什么需要：
        - pydantic-settings 默认把 list[str] 字段按 JSON 解析，
          但运维同事更熟悉逗号分隔的写法（CORS_ALLOW_ORIGINS=a.com,b.com）
        - 容忍空字符串和 None，避免 .env 留空时启动失败
        """
        if value is None or value == "":
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    """获取全局配置单例

    lru_cache 保证只创建一次，后续调用直接返回缓存
    """
    return Settings()
