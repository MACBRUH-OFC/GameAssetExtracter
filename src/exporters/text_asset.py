def export_text_asset(data, output_path, debug_mode, local_logger):
    try:
        content = getattr(data, 'm_Script', "")
        if isinstance(content, bytes):
            content = content.decode('utf-8', errors='replace')
        
        ext = ".txt"
        if content.strip().startswith('{'): ext = ".json"
        elif content.strip().startswith('<?xml'): ext = ".xml"
        
        with open(f"{output_path}{ext}", 'w', encoding='utf-8') as f:
            f.write(content)
        return True
    except Exception:
        return False
