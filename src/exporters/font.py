def export_font(data, output_path, debug_mode, local_logger):
    try:
        font_data = getattr(data, 'm_FontData', None)
        if not font_data: return False
        
        ext = ".font"
        if font_data[:4] == b'OTTO': ext = ".otf"
        elif font_data[:4] in [b'\x00\x01\x00\x00', b'true']: ext = ".ttf"
        
        with open(f"{output_path}{ext}", 'wb') as f:
            f.write(font_data)
        return True
    except Exception:
        return False
