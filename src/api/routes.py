import os, uuid, logging
from flask import Blueprint, request, jsonify, send_file, current_app
from werkzeug.utils import secure_filename
from src.utils import is_allowed_file_extension
from src.bundle_processing.core_processor import BundleProcessor
from src.session.manager import get_session_data, add_session_data, update_session_status

api_bp = Blueprint('api', __name__, url_prefix='/api')
logger = logging.getLogger(__name__)

@api_bp.route('/upload', methods=['POST'])
def upload_bundle():
    if 'files' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    
    files = request.files.getlist('files')
    session_id = str(uuid.uuid4())
    session_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], session_id)
    os.makedirs(session_dir, exist_ok=True)
    
    primary_file_path = ""
    primary_file_name = ""
    
    for file in files:
        if file and is_allowed_file_extension(file.filename, current_app.config['ALLOWED_EXTENSIONS']):
            filename = secure_filename(file.filename)
            path = os.path.join(session_dir, filename)
            file.save(path)
            if not primary_file_path:
                primary_file_path = path
                primary_file_name = filename

    processor = BundleProcessor(session_id, primary_file_path, primary_file_name, session_dir, current_app.config)
    add_session_data(session_id, processor)
    
    # Process immediately for Serverless
    processor.analyze_bundle()
    
    return jsonify({
        'session_id': session_id,
        'status': processor.processing_status,
        'metadata': processor.metadata
    })

@api_bp.route('/status/<session_id>')
def get_status(session_id):
    session_data = get_session_data(session_id)
    if not session_data: return jsonify({'error': 'Expired'}), 404
    p = session_data['processor']
    return jsonify({'status': p.processing_status, 'progress': p.progress, 'metadata': p.metadata})

@api_bp.route('/extract', methods=['POST'])
def extract_assets():
    data = request.get_json()
    session_id = data.get('session_id')
    indices = data.get('selected_assets')
    session_data = get_session_data(session_id)
    
    processor = session_data['processor']
    zip_path = processor.extract_selected_assets(indices)
    update_session_status(session_id, 'zip_path', zip_path)
    
    return jsonify({'status': 'completed'})

@api_bp.route('/download/<session_id>')
def download_assets(session_id):
    session_data = get_session_data(session_id)
    return send_file(session_data['zip_path'], as_attachment=True)
