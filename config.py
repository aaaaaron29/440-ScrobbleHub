"""
Configuration management for Last.fm Listening History Tracker.
"""

import os
from pathlib import Path

# Base directory
BASE_DIR = Path(__file__).parent.absolute()
DATA_DIR = BASE_DIR / "data"

# Ensure data directory exists
DATA_DIR.mkdir(exist_ok=True)


class Config:
    """Application configuration."""

    # Flask
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-prod')

    # Database
    DATABASE_PATH = DATA_DIR / "lastfm_tracker.db"
    SQLALCHEMY_DATABASE_URI = f"sqlite:///{DATABASE_PATH}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # APScheduler - use memory store to avoid pickle issues
    SCHEDULER_API_ENABLED = False
    SCHEDULER_EXECUTORS = {
        'default': {
            'type': 'threadpool',
            'max_workers': 2
        }
    }

    # Last.fm API
    LASTFM_API_BASE = "https://ws.audioscrobbler.com/2.0/"
    MAX_SCROBBLES_PER_FETCH = 200
    INITIAL_BACKFILL_PAGES = 50  # ~10,000 scrobbles on first sync

    # Sync settings
    DEFAULT_SYNC_INTERVAL_MINUTES = 30
    SYNC_RETRY_ATTEMPTS = 3
    SYNC_RETRY_DELAY_SECONDS = 30

    # API rate limiting
    API_CALLS_PER_SECOND = 5
    API_TIMEOUT_SECONDS = 30

    # Export settings
    EXPORT_MAX_ROWS = 100000


class DevelopmentConfig(Config):
    """Development configuration."""
    DEBUG = True


class ProductionConfig(Config):
    """Production configuration."""
    DEBUG = False


# Config selector
config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'default': DevelopmentConfig
}


def get_config():
    """Get configuration based on environment."""
    env = os.environ.get('FLASK_ENV', 'development')
    return config.get(env, config['default'])
