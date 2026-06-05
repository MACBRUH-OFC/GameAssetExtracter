import os
import shutil
import uuid
import time
import threading
import logging
from datetime import datetime
from typing import Tuple

from flask import Flask, request, jsonify, send_file, after_this_request
from werkzeug.utils import secure_filename
from werkzeug.exceptions import RequestEntityTooLarge
import UnityPy

from api.utils import is_allowed_file_extension, get_file_info
from api.extractor import build_asset_inventory, extract_single_asset, create_zip_archive

# Initialize Flask application
app = Flask(__name__)

# Configure logs
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration settings (mirroring Config class of original project)
MAX_CONTENT_LENGTH = 500 * 1024 * 1024  # 500 MB
ALLOWED_EXTENSIONS = {
    'bundle', 'unity3d', 'assets', 'unitybundle', 'assetbundle', 'ress', 
    'resource', 'dat', 'bin', 'txt', 'bytes', 'json', 'xml', 'yaml', 
    'csv', 'shader', 'font', 'audio', 'video'
}
RATE_LIMIT_ENABLED = True
RATE_LIMIT_PER_MINUTE = 20
RATE_LIMIT_WINDOW_SECONDS = 60

# In-memory dictionary for rate limiting (local to serverless container instances)
last_request_times = {}
rate_limit_lock = threading.Lock()

def _check_rate_limit(ip_address: str) -> Tuple[bool, int]:
    if not RATE_LIMIT_ENABLED:
        return False, 0

    with rate_limit_lock:
        current_time = time.time()
        # Evict old timestamps
        last_request_times[ip_address] = [
            t for t in last_request_times.get(ip_address, [])
            if current_time - t < RATE_LIMIT_WINDOW_SECONDS
        ]

        if len(last_request_times[ip_address]) >= RATE_LIMIT_PER_MINUTE:
            oldest_request_time = last_request_times[ip_address][0]
            retry_after = int(RATE_LIMIT_WINDOW_SECONDS - (current_time - oldest_request_time))
            return True, max(1, retry_after)
        
        last_request_times[ip_address].append(current_time)
        return False, 0

@app.route('/api/upload', methods=['POST'])
def upload_and_analyze():
    """
    Synchronously uploads a Unity asset bundle, parses its structure, and returns inventory metadata.
    """
    client_ip = request.remote_addr
    is_rate_limited, retry_after = _check_rate_limit(client_ip)
    if is_rate_limited:
        logger.warning(f"Rate limit exceeded for IP: {client_ip}")
        response = jsonify({
            'error': f"Too many requests. Please try again after {retry_after} seconds. Limit: {RATE_LIMIT_PER_MINUTE} requests per minute."
        })
        response.headers['Retry-After'] = str(retry_after)
        return response, 429

    if 'files' not in request.files:
        return jsonify({'error': 'No file part in the request'}), 400
    
    files = request.files.getlist('files')
    if not files or not files[0].filename:
        return jsonify({'error': 'No file selected for uploading'}), 400

    session_id = str(uuid.uuid4())
    session_upload_dir = os.path.join('/tmp', 'uploads', session_id)
    os.makedirs(session_upload_dir, exist_ok=True)
    
    try:
        all_uploaded_files = []
        for file_item in files:
            if file_item and file_item.filename:
                if not is_allowed_file_extension(file_item.filename, ALLOWED_EXTENSIONS):
                    shutil.rmtree(session_upload_dir, ignore_errors=True)
                    return jsonify({
                        'error': f'Invalid file type: {file_item.filename}. Allowed extensions are: {", ".join(ALLOWED_EXTENSIONS)}'
                    }), 400

                filename_secured = secure_filename(file_item.filename)
                save_path = os.path.join(session_upload_dir, filename_secured)
                file_item.save(save_path)
                all_uploaded_files.append({'path': save_path, 'name': file_item.filename})
        
        # Identify the primary bundle or assets file
        primary_file = None
        bundle_extensions = ['.bundle', '.unity3d', '.assets', '.unitybundle', '.assetbundle']
        for f in all_uploaded_files:
            if any(f['name'].lower().endswith(ext) for ext in bundle_extensions):
                primary_file = f
                break
        
        if not primary_file:
            for f in all_uploaded_files:
                 if f['name'].lower().endswith('.assets'):
                    primary_file = f
                    break
        
        if not primary_file:
            shutil.rmtree(session_upload_dir, ignore_errors=True)
            raise ValueError("No main Unity bundle/asset file (.bundle, .unity3d, .assets, .unitybundle, .assetbundle) was found in the upload.")

        # Load environment and parse metadata synchronously
        logger.info(f"Loading Unity environment for {primary_file['name']}")
        env = UnityPy.load(primary_file['path'])
        objects = list(env.objects)
        logger.info(f"Found {len(objects)} objects in bundle.")
        
        # Build inventory
        asset_inventory = build_asset_inventory(objects, logger, debug_mode=False)
        asset_classes = sorted(list(asset_inventory.keys()))
        
        file_info = get_file_info(primary_file['path'])
        
        metadata = {
            'bundle_info': {
                'filename': primary_file['name'],
                'size': file_info['size'],
                'signature': file_info['signature'],
                'compression': file_info['compression'],
                'unity_version': str(getattr(env, 'unity_version', 'Unknown')),
                'platform': str(getattr(env, 'platform', 'Unknown')),
                'object_count': len(objects)
            },
            'assets': asset_inventory,
            'asset_classes': asset_classes,
            'analyzed_at': datetime.now().isoformat()
        }
        
        logger.info(f"Analysis completed successfully for session {session_id}.")
        return jsonify({
            'session_id': session_id,
            'status': 'completed',
            'metadata': metadata
        })

    except RequestEntityTooLarge:
        shutil.rmtree(session_upload_dir, ignore_errors=True)
        return jsonify({'error': f'File size exceeds the limit of {MAX_CONTENT_LENGTH // 1024 // 1024}MB'}), 413
    except Exception as e:
        shutil.rmtree(session_upload_dir, ignore_errors=True)
        logger.error(f"Upload and analysis failed: {e}", exc_info=True)
        return jsonify({'error': f'An unexpected error occurred during processing: {str(e)}'}), 500

@app.route('/api/extract', methods=['POST'])
def extract_selected_assets():
    """
    Synchronously extracts selected asset indices from the uploaded bundle, packs them into a ZIP,
    cleans up temp files immediately, and streams the archive file back.
    """
    data = request.get_json() or {}
    session_id = data.get('session_id')
    selected_indices = data.get('selected_assets')

    if not session_id or not isinstance(selected_indices, list):
        return jsonify({'error': 'Missing session_id or selected_assets list in request body'}), 400

    session_upload_dir = os.path.join('/tmp', 'uploads', session_id)
    if not os.path.exists(session_upload_dir):
        return jsonify({'error': 'Session expired or not found. Please re-upload your bundle.'}), 404

    # Locate primary bundle file inside session uploads directory
    primary_file_path = None
    original_filename = "extracted_assets"
    bundle_extensions = ['.bundle', '.unity3d', '.assets', '.unitybundle', '.assetbundle']
    
    for filename in os.listdir(session_upload_dir):
        if any(filename.lower().endswith(ext) for ext in bundle_extensions):
            primary_file_path = os.path.join(session_upload_dir, filename)
            original_filename = filename
            break

    if not primary_file_path:
        return jsonify({'error': 'Primary bundle file not found in session upload directory.'}), 404

    temp_output_dir = os.path.join('/tmp', 'extractions', f"extract_{session_id}")
    os.makedirs(temp_output_dir, exist_ok=True)

    try:
        # Load environment
        env = UnityPy.load(primary_file_path)
        objects = list(env.objects)
        
        # Extract assets
        for asset_index in selected_indices:
            if 0 <= asset_index < len(objects):
                obj = objects[asset_index]
                extract_single_asset(obj, temp_output_dir, logger, debug_mode=False)

        # Pack into ZIP
        zip_path = create_zip_archive(temp_output_dir, original_filename, session_id, logger)
        
        # Clean up original bundle uploads and raw extraction folder immediately to save storage
        shutil.rmtree(temp_output_dir, ignore_errors=True)
        shutil.rmtree(session_upload_dir, ignore_errors=True)

        # Register a post-request callback to clean up the zipped archive file once download completes
        @after_this_request
        def remove_zip(response):
            try:
                if os.path.exists(zip_path):
                    os.remove(zip_path)
                    # Clean up containing directory if empty
                    parent_dir = os.path.dirname(zip_path)
                    if os.path.exists(parent_dir) and not os.listdir(parent_dir):
                        os.rmdir(parent_dir)
                    logger.info(f"Cleaned up temporary ZIP archive at {zip_path}")
            except Exception as cleanup_e:
                logger.error(f"Failed to remove temporary ZIP file: {cleanup_e}")
            return response

        logger.info(f"Streaming ZIP archive for session {session_id}")
        return send_file(zip_path, as_attachment=True, download_name=os.path.basename(zip_path))

    except Exception as e:
        shutil.rmtree(temp_output_dir, ignore_errors=True)
        shutil.rmtree(session_upload_dir, ignore_errors=True)
        logger.error(f"Asset extraction failed: {e}", exc_info=True)
        return jsonify({'error': f'An unexpected error occurred during extraction: {str(e)}'}), 500

@app.route('/api/queue/cancel', methods=['POST'])
def cancel_queue_task():
    """
    Stub compatibility endpoint. Aborts are handled client-side via AbortController.
    """
    return jsonify({'status': 'success', 'message': 'Operation cancelled.'}), 200

# Server execution block for local development
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    # Serve locally
    app.run(host='0.0.0.0', port=port, debug=True)
