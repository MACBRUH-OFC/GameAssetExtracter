import re
import os
import logging

logger = logging.getLogger(__name__)

def sanitize_filename(name: str) -> str:
    """
    Sanitizes a string to be a valid, filesystem-safe filename.
    """
    if not isinstance(name, str):
        name = str(name)
    # Remove characters illegal on Windows/Linux systems
    sane_name = re.sub(r'[<>:"/\\|?*]', '_', name)
    sane_name = sane_name.replace(' ', '_')
    sane_name = sane_name.strip('_').strip()
    return sane_name if sane_name else "Untitled"

def detect_compression_type(data: bytes) -> str:
    """
    Identifies bundle compression using binary magic number headers.
    """
    if len(data) < 8:
        return "unknown"
    
    signatures = {
        b'UnityFS\x00': "unityfs",
        b'UnityRaw': "raw",
        b'LZ4\x00': "lz4",
        b'\x78\x9c': "zlib",
        b'\x78\x01': "zlib",
        b'\x78\xda': "zlib",
        b'\x1f\x8b': "gzip",
    }
    
    for sig, comp_type in signatures.items():
        if data.startswith(sig):
            return comp_type
    
    return "unknown"

def get_file_info(filepath: str) -> dict:
    """
    Analyzes basic metadata properties of a file.
    """
    try:
        with open(filepath, 'rb') as f:
            header = f.read(32)
        
        return {
            'signature': header[:8].hex(),
            'size': os.path.getsize(filepath),
            'compression': detect_compression_type(header),
            'version_header_guess': 'Unknown'
        }
    except Exception as e:
        logger.error(f"Failed to retrieve file info for {filepath}: {e}", exc_info=True)
        return {'signature': '', 'size': 0, 'compression': 'unknown', 'version_header_guess': 'Unknown'}

def is_allowed_file_extension(filename: str, allowed_extensions: set) -> bool:
    """
    Verifies if a file's extension resides in the permitted set.
    """
    if '.' not in filename:
        return False
    return filename.rsplit('.', 1)[1].lower() in allowed_extensions
