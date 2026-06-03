import json

def export_material(data, output_path, debug_mode, local_logger):
    try:
        material_info = {
            'name': getattr(data, 'm_Name', 'Unknown'),
            'properties': {}
        }
        # Attempt to read shader properties
        with open(f"{output_path}.mat.json", 'w') as f:
            json.dump(material_info, f, indent=2)
        return True
    except Exception:
        return False
