import os

# Set required env vars before any module imports config.Settings()
os.environ.setdefault("SARVAM_API_KEY", "test-sarvam-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
