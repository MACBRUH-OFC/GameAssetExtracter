def export_mesh_obj(data, output_path, debug_mode, local_logger):
    try:
        vertices = getattr(data, 'm_Vertices', [])
        indices = getattr(data, 'm_IndexBuffer', [])
        if not vertices: return False
        
        with open(f"{output_path}.obj", 'w') as f:
            f.write(f"# Exported by UnityBundleExtractor\n")
            for v in vertices:
                f.write(f"v {v[0]} {v[1]} {v[2]}\n")
            
            # Faces (1-based index)
            for i in range(0, len(indices), 3):
                f.write(f"f {indices[i]+1} {indices[i+1]+1} {indices[i+2]+1}\n")
        return True
    except Exception:
        return False
