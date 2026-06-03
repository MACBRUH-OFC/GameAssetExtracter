import os

class Config:
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB
    # Vercel requires writing to /tmp
    UPLOAD_FOLDER = '/tmp/uploads'
    OUTPUT_FOLDER = '/tmp/extractions'
    SESSION_LOGS_DIR = '/tmp/logs/sessions'
    ALLOWED_EXTENSIONS = {'bundle', 'unity3d', 'assets', 'unitybundle', 'assetbundle', 'ress'}
    DEBUG_MODE = False
    GLOBAL_LOG_LEVEL = 'INFO'
    GLOBAL_LOG_FILE = 'global.log'
    SESSION_LOG_LEVEL = 'DEBUG'
    SECRET_KEY = 'vercel-secret'
    RATE_LIMIT_ENABLED = False
    WEB_CONCURRENCY = 0 # No workers in serverless
