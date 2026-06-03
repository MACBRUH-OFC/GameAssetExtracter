import json, os, logging
from PIL import Image

def _save_texture_metadata(data, output_path, exported_format, local_logger):
    metadata = {
        'width': getattr(data, 'm_Width', 'Unknown'),
        'height': getattr(data, 'm_Height', 'Unknown'),
        'format_unity': str(getattr(data, 'm_Format', 'Unknown')),
        'mip_count': getattr(data, 'm_MipCount', 1)
    }
    with open(f"{output_path}_meta.json", 'w', encoding='utf-8') as f:
        json.dump(metadata, f, indent=2)

def export_texture(data, output_path, debug_mode, local_logger) -> bool:
    try:
        if hasattr(data, 'image') and data.image:
            img = data.image
            if img.mode in ('RGB', 'L') or (img.mode == 'RGBA' and img.getextrema()[3][0] == 255):
                img.save(f"{output_path}.jpg", optimize=True, quality=90)
                _save_texture_metadata(data, output_path, 'jpg', local_logger)
            else:
                img.save(f"{output_path}.png", optimize=True)
                _save_texture_metadata(data, output_path, 'png', local_logger)
            return True
        return False
    except Exception as e:
        local_logger.error(f"Texture error: {e}")
        return False
