import json, os, logging
from PIL import Image

def export_texture(data, output_path, debug_mode, local_logger):
    try:
        if hasattr(data, 'image') and data.image:
            img = data.image
            # If image is opaque, save as JPG to save space, else PNG
            if img.mode in ('RGB', 'L') or (img.mode == 'RGBA' and img.getextrema()[3][0] == 255):
                out = f"{output_path}.jpg"
                img.save(out, optimize=True, quality=90)
            else:
                out = f"{output_path}.png"
                img.save(out, optimize=True)
            return True
        return False
    except Exception as e:
        local_logger.error(f"Texture error: {e}")
        return False
