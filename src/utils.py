# -*- coding: utf-8 -*-
"""
UnityBundleExtractor - Utility Functions Module
Author: lenzarchive (https://github.com/lenzarchive)
License: MIT License

This module provides various general utility functions used across the application.
"""

import re
import os
import zlib
import struct
import logging

# Configure logger for this module
logger = logging.getLogger(__name__)

def sanitize_filename(name: str) -> str:
    """
    Sanitizes a string to be a valid filename for safe file system operations.
    Replaces characters typically illegal in filenames and trims whitespace.

    Args:
        name (str): The original string to sanitize.

    Returns:
        str: The sanitized filename. Returns "Untitled" if the sanitized name is empty.
    """
    if not isinstance(name, str):
        name = str(name)
    sane_name = re.sub(r'[<>:"/\\|?*]', '_', name)
    sane_name = sane_name.replace(' ', '_')
    sane_name = sane_name.strip('_').strip()
    return sane_name if sane_name else "Untitled"

def detect_compression_type(data: bytes) -> str:
    """
    Detects the file compression type based on common magic numbers or signatures
    found at the beginning of binary data.

    Args:
        data (bytes): The initial bytes of the file (e.g., file header).

    Returns:
        str: A string indicating the detected compression type (e.g., "unityfs", "lz4", "zlib", "gzip")
             or "unknown" if no signature is matched.
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
    Extracts basic information from a file, such as its size, a hexadecimal
    representation of its initial bytes (signature), and a detected compression type.

    Args:
        filepath (str): The full path to the file.

    Returns:
        dict: A dictionary containing 'signature', 'size', 'compression', and 'version_header_guess'.
              Returns default values if an error occurs during file reading.
    """
    try:
        with open(filepath, 'rb') as f:
            header = f.read(32)
        
        bundle_info = {
            'signature': header[:8].hex(),
            'size': os.path.getsize(filepath),
            'compression': detect_compression_type(header),
            'version_header_guess': 'Unknown'
        }
        return bundle_info
    except Exception as e:
        logger.error(f"Failed to extract basic file info from {filepath}: {e}", exc_info=True)
        return {'signature': '', 'size': 0, 'compression': 'unknown', 'version_header_guess': 'Unknown'}

def is_allowed_file_extension(filename: str, allowed_extensions: set) -> bool:
    """
    Checks if a file's extension is present in the set of allowed extensions.

    Args:
        filename (str): The name of the file.
        allowed_extensions (set): A set of lowercase allowed file extensions (e.g., {'bundle', 'unity3d'}).

    Returns:
        bool: True if the extension is allowed, False otherwise.
    """
    if '.' not in filename:
        return False
    return filename.rsplit('.', 1)[1].lower() in allowed_extensions
