"""
Production entry point for the AI Diary Companion.
Starts the FastAPI server via uvicorn.
"""

import uvicorn
import logging
from app.config import PORT, validate_config
from app.logging_config import setup_logging

# Initialize logging first
setup_logging()
logger = logging.getLogger(__name__)

def main():
    try:
        # Validate environment
        validate_config()
        
        logger.info("Starting server on port %d", PORT)
        
        # Start uvicorn
        # In production, uvicorn app/webhook:app is usually run via Docker CMD
        # but having a python entrypoint is good for local dev and Railway.
        uvicorn.run(
            "api.index:app",
            host="0.0.0.0",
            port=PORT,
            log_level="info",
            proxy_headers=True,
            forwarded_allow_ips="*"
        )
    except Exception as e:
        logger.critical("Failed to start application: %s", e)
        exit(1)

if __name__ == "__main__":
    main()
