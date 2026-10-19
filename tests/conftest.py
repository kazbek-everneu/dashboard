"""
Test configuration and fixtures.

Sets required environment variables before any app modules are imported
so pydantic-settings can build the Settings object without a real .env file.
"""
import os

# Must be set before importing any app module that touches settings
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://qa:qa@localhost/qa_test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")
os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
os.environ.setdefault("DATA_DIR", "/tmp/qa_test_data")
os.environ.setdefault("FRONTEND_BASE_URL", "http://localhost:15173")
os.environ.setdefault("SMTP_FROM_EMAIL", "no-reply@example.com")
