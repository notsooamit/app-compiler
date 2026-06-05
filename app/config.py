"""
Centralized configuration for the App Compiler.
Settings extracted from environment and constants used across modules.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# DeepSeek API
DEFAULT_MODEL = "deepseek-chat"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"

# Pricing per million tokens (USD)
PRICING = {
    "deepseek-chat": {"input": 0.14, "output": 0.28},
    "deepseek-reasoner": {"input": 0.55, "output": 2.19},
}

# LLM parameters
DEFAULT_TEMPERATURE = 0.1
DEFAULT_MAX_TOKENS = 16384
MAX_RETRY_ATTEMPTS = 3

# Rate limiting
RATE_LIMIT = 5         # max requests per window
RATE_WINDOW = 60       # seconds
CLEANUP_INTERVAL = 300  # seconds between stale entry cleanup

# Input validation
MAX_PROMPT_LENGTH = 3000  # characters

# Repair engine
MAX_REPAIR_PASSES = 3

# Server port
try:
    PORT = int(os.getenv("PORT", "8000"))
except (ValueError, TypeError):
    PORT = 8000

