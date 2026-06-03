import os, json, logging
from typing import Any, Optional

def _calculate_bounds(vertices) -> Optional[dict]:
    if not vertices: return None
    min_coords = [float('inf')] * 3
    max_coords = [float('-inf')] * 3
    for vertex in vertices:
        if len(vertex) >= 3:
            for i in range(3):
                min_coords[i] = min(min_coords[i], vertex[i])
                max_coords[i] = max(max_coords[i], vertex[i])
    return {
        'min': min_coords, 'max': max_coords,
        'center': [(min_coords[i] + max_coords[i]) / 2 for i in range(3)],
        'size': [max_coords[i] - min_coords[i] for i in range(3)]
    }

def export_mesh_obj(data: Any, output_path: str, debug_mode: bool, local_logger: logging.Logger) -> bool:
    try:
        vertices = getattr(data, 'm_Vertices', [])
        indices = getattr(data, 'm_IndexBuffer', [])
        normals = getattr(data, 'm_Normals', [])
        uvs = getattr(data, 'm_UV', [])
        if not vertices: return False
        
        obj_lines = [f"# Wavefront OBJ\n# Vertices: {len(vertices)}\n"]
        for v in vertices: obj_lines.append(f"v {v[0]} {v[1]} {v[2]}")
        if normals:
            for n in normals: obj_lines.append(f"vn {n[0]} {n[1]} {n[2]}")
        if uvs:
            for uv in uvs: obj_lines.append(f"vt {uv[0]} {uv[1]}")

        obj_lines.append("\ng mesh")
        for i in range(0, len(indices) - 2, 3):
            v1, v2, v3 = indices[i] + 1, indices[i+1] + 1, indices[i+2] + 1
            if normals and uvs: obj_lines.append(f"f {v1}/{v1}/{v1} {v2}/{v2}/{v2} {v3}/{v3}/{v3}")
            elif uvs: obj_lines.append(f"f {v1}/{v1} {v2}/{v2} {v3}/{v3}")
            else: obj_lines.append(f"f {v1} {v2} {v3}")

        with open(f"{output_path}.obj", 'w', encoding='utf-8') as f:
            f.write('\n'.join(obj_lines))
        
        metadata = {'vertex_count': len(vertices), 'bounds': _calculate_bounds(vertices)}
        with open(f"{output_path}_meta.json", 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2)
        return True
    except Exception as e:
        local_logger.error(f"Mesh export failed: {e}")
        return False
