import json

def export_generic(data, output_path, obj_type, debug_mode, local_logger):
    try:
        # UnityPy can read the internal structure (TypeTree)
        tree = data.read_typetree()
        with open(f"{output_path}.json", 'w', encoding='utf-8') as f:
            json.dump(tree, f, indent=2, ensure_ascii=False, default=str)
        return True
    except Exception:
        return False
