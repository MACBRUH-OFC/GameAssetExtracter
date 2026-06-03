def export_mono_script(data, output_path, debug_mode, local_logger):
    try:
        info = {
            'class': getattr(data, 'm_ClassName', ''),
            'namespace': getattr(data, 'm_Namespace', ''),
            'assembly': getattr(data, 'm_AssemblyName', '')
        }
        with open(f"{output_path}.script.json", 'w') as f:
            import json
            json.dump(info, f, indent=2)
        return True
    except Exception:
        return False
