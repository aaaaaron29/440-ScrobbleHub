#!/usr/bin/env python3
"""
Windows Service Runner for Last.fm Listening Tracker.

This script is designed to run the Flask application as a background service.
It includes proper signal handling for graceful shutdown and logging to a file.

Usage:
    python run_service.py          # Run normally with console output
    pythonw run_service.py         # Run without console window (background)
"""

import os
import sys
import signal
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from datetime import datetime

# Ensure we're in the right directory
os.chdir(Path(__file__).parent)

# Setup logging
LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

log_file = LOG_DIR / "service.log"
file_handler = RotatingFileHandler(
    log_file,
    maxBytes=5*1024*1024,  # 5MB
    backupCount=3
)
file_handler.setFormatter(logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
))

# Configure root logger
logging.basicConfig(
    level=logging.INFO,
    handlers=[
        file_handler,
        logging.StreamHandler(sys.stdout)  # Also log to console if running interactively
    ]
)

logger = logging.getLogger(__name__)


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    logger.info(f"Received signal {signum}, shutting down...")
    sys.exit(0)


def main():
    """Main entry point for the service."""
    logger.info("=" * 50)
    logger.info(f"Last.fm Tracker Service Starting at {datetime.now()}")
    logger.info("=" * 50)

    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        # Import and run the Flask app
        from app import app, init_scheduler

        # Initialize the scheduler
        init_scheduler()

        logger.info("Starting Flask server on http://0.0.0.0:5000")

        # Run with waitress if available (production server)
        try:
            from waitress import serve
            logger.info("Using Waitress production server")
            serve(app, host='0.0.0.0', port=5000, threads=4)
        except ImportError:
            # Fall back to Flask's development server
            logger.warning("Waitress not installed, using Flask development server")
            logger.warning("For production, install waitress: pip install waitress")
            app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)

    except Exception as e:
        logger.exception(f"Service failed with error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
