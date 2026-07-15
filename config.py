from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # App
    APP_NAME: str = "SwiftVTU"
    APP_ENV: str = "development"
    SECRET_KEY: str = "change-this-secret-key"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    # MongoDB
    MONGODB_URL: str = "mongodb://localhost:27017"
    MONGODB_DB_NAME: str = "swiftvtu"

    # Paystack
    PAYSTACK_SECRET_KEY: str = ""
    PAYSTACK_PUBLIC_KEY: str = ""
    PAYSTACK_WEBHOOK_SECRET: str = ""

    # Flutterwave
    FLW_SECRET_KEY: str = ""
    FLW_PUBLIC_KEY: str = ""
    FLW_WEBHOOK_SECRET: str = ""

    # VTpass
    VTPASS_API_KEY: str = ""
    VTPASS_PUBLIC_KEY: str = ""
    VTPASS_SECRET_KEY: str = ""
    VTPASS_BASE_URL: str = "https://sandbox.vtpass.com/api"

    # Email (SendGrid primary, SMTP fallback)
    EMAIL_PROVIDER: str = "sendgrid"          # sendgrid | smtp
    SENDGRID_API_KEY: str = ""
    EMAIL_FROM: str = "noreply@swiftvtu.com"
    EMAIL_FROM_NAME: str = "SwiftVTU"

    # SMTP fallback (Gmail, Zoho, custom)
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_USE_TLS: bool = True

    # App URLs (for email links)
    FRONTEND_URL: str = "http://localhost:5500"
    SUPPORT_EMAIL: str = "support@swiftvtu.com"

    # CORS
    ALLOWED_ORIGINS: str = "http://localhost:3000"

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",")]

    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
