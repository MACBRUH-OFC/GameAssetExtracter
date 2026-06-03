def export_shader(data, output_path, debug_mode, local_logger):
    try:
        content = getattr(data, 'm_Script', "")
        if content:
            with open(f"{output_path}.shader", 'w', encoding='utf-8') as f:
                f.write(content)
            return True
        return False
    except Exception:
        return False
