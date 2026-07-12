"""Application configuration via environment variables."""

import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "千摊智脑 API"
    app_version: str = "0.1.0"
    debug: bool = True

    # PostgreSQL (production/Docker) or SQLite (dev fallback)
    database_url: str = os.getenv(
        "DATABASE_URL",
        f"sqlite+aiosqlite:///{os.path.dirname(os.path.dirname(os.path.abspath(__file__)))}/qiantan_dev.db",
    )
    db_backend: str = os.getenv("DB_BACKEND", "sqlite")  # "postgresql" or "sqlite"

    # 启动时是否自动跑 Alembic 迁移（生产/部署=true；纯测试或特殊场景可关）。
    # 为 False 时回退到 Base.metadata.create_all（仅 dev/test 便利，生产勿用）。
    run_migrations_on_startup: bool = (
        os.getenv("RUN_MIGRATIONS_ON_STARTUP", "true").lower() == "true"
    )

    # iFlytek ASR (full credentials needed for real recognition)
    asr_api_key: str = ""
    asr_api_url: str = "https://iat-api.xfyun.cn/v2/iat"
    asr_app_id: str = ""
    asr_api_secret: str = ""

    # Weather (QWeather)
    weather_api_key: str = ""
    weather_api_url: str = "https://devapi.qweather.com/v7/weather"
    weather_city_id: str = "101020100"  # 上海 location ID
    weather_geo_url: str = "https://geoapi.qweather.com/v2/city/lookup"

    # File storage
    upload_dir: str = "./uploads"
    audio_dir: str = "./uploads/audio"

    # Default city for weather
    default_city: str = "上海"

    # Differential privacy (experience cloud)
    privacy_epsilon: float = 1.0

    # ------------------------------------------------------------------
    # 微信登录（P0-1 鉴权）
    # ------------------------------------------------------------------
    wechat_appid: str = os.getenv("WECHAT_APPID", "")
    wechat_secret: str = os.getenv("WECHAT_SECRET", "")

    # ------------------------------------------------------------------
    # JWT（P0-1 鉴权）：身份只来自 token，绝不来自请求体
    # ------------------------------------------------------------------
    # 默认 dev 密钥（仅本地用）；生产务必通过环境变量 JWT_SECRET 注入 ≥32 字节的强密钥。
    jwt_secret: str = os.getenv("JWT_SECRET", "dev-secret-please-override-with-env-in-prod")
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = int(os.getenv("JWT_EXPIRE_MINUTES", "10080"))  # 默认 7 天

    # 鉴权回退开关（仅 dev/测试用）。
    # 为 True 时 get_current_merchant 允许从 query/header 取 merchant_id（过渡兼容）。
    # 生产环境必须为 False —— 身份只能来自 token，否则多租户隔离形同虚设。
    auth_allow_fallback: bool = False

    # CORS：逗号分隔的白名单；"*" 仅允许本地 dev，生产必须收紧
    cors_origins: str = os.getenv("CORS_ORIGINS", "*")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

    # ------------------------------------------------------------------
    # 生产环境安全自检（fail-closed）：启动即拦截致命误配，避免"零鉴权"上线
    # ------------------------------------------------------------------
    def validate_security(self) -> None:
        """非 debug（生产）环境下，发现致命安全误配直接拒绝启动。

        覆盖三类会直接击穿 G1 多租户鉴权的配置：
          1. JWT_SECRET 沿用默认/弱密钥 —— 源码公开即等于零鉴权，可被任意伪造 token。
          2. auth_allow_fallback=True —— 允许从 query/header 取 merchant_id，越权黑洞重现。
          3. CORS_ORIGINS='*' —— 即便已自动关闭 credentials，仍应明确前端白名单。
        dev 环境（debug=True）跳过，避免本地开发被拦截。
        """
        if self.debug:
            return

        import logging

        # 1) JWT 密钥：默认密钥（源码公开）或长度 <32 字节都视为不安全
        if self.jwt_secret == "dev-secret-please-override-with-env-in-prod":
            raise RuntimeError(
                "生产环境禁止使用默认 JWT_SECRET，请通过环境变量 JWT_SECRET 注入 ≥32 字节强密钥"
            )
        if len(self.jwt_secret.encode()) < 32:
            raise RuntimeError("JWT_SECRET 长度不足 32 字节，存在被暴力破解风险，请更换为强密钥")

        # 2) 鉴权回退：生产必须关闭，否则多租户隔离形同虚设
        if self.auth_allow_fallback:
            raise RuntimeError(
                "生产环境 auth_allow_fallback 必须为 False，否则商户身份可被客户端伪造"
            )

        # 3) CORS 通配符：仅告警（不直接拒绝，因已自动关闭 credentials），提示配置白名单
        if self.cors_origins.strip() == "*":
            logging.getLogger("uvicorn.error").critical(
                "生产环境 CORS_ORIGINS 为 '*'，建议配置具体前端域名白名单（如 "
                "https://mp.weixin.qq.com,https://your-domain.com）"
            )


settings = Settings()
