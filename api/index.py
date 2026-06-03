import os
import sys
from flask import Flask, render_template

# Ensure the root directory is in the path
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from src.config import Config
from src.api.routes import api_bp
from src.api.error_handlers import register_error_handlers
from src.session.manager import processing_sessions, session_lock, initialize_session_manager

app = Flask(__name__, 
            template_folder='../templates', 
            static_folder='../static')

app.config.from_object(Config)

# Ensure folders exist in /tmp
os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)
os.makedirs(Config.OUTPUT_FOLDER, exist_ok=True)
os.makedirs(Config.SESSION_LOGS_DIR, exist_ok=True)

initialize_session_manager(app, processing_sessions, session_lock)
app.register_blueprint(api_bp)
register_error_handlers(app)

@app.route('/')
def index():
    return render_template('index.html')

# Vercel needs 'app' variable
