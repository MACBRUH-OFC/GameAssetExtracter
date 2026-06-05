import os
import json
import logging
import io
from typing import Any, Optional
from PIL import Image

from api.utils import sanitize_filename

logger = logging.getLogger(__name__)

# --- UTILS FOR EXPORTERS ---

def _serialize_object(data: Any) -> Any:
    """
    Recursively serializes complex UnityPy objects into a JSON-friendly format.
    """
    if data is None:
        return None
    if isinstance(data, (str, int, float, bool)):
        return data
    if isinstance(data, bytes):
        try:
            return data.decode('utf-8', errors='replace')
        except Exception:
            return f"<binary data: {len(data)} bytes>"
    if isinstance(data, (list, tuple)):
        return [_serialize_object(item) for item in data]
    if isinstance(data, dict):
        return {key: _serialize_object(value) for key, value in data.items()}
    
    if hasattr(data, 'path_id'):
        if hasattr(data, 'file_id'):
            return {
                'type': 'ObjectReference',
                'file_id': getattr(data, 'file_id', 0),
                'path_id': str(data.path_id)
            }
        else:
            return {'type': 'ObjectReference', 'path_id': str(data.path_id)}

    if hasattr(data, '__dict__'):
        return {
            key: _serialize_object(value)
            for key, value in data.__dict__.items()
            if not key.startswith('_')
        }
    try:
        return str(data)
    except Exception:
        return 'Unserializable Object'


# --- 10 SPECIALIZED EXPORTERS ---

# 1. Texture/Sprite
def export_texture(data: Any, output_path: str, debug_mode: bool, local_logger: logging.Logger) -> bool:
    try:
        local_logger.debug(f"Exporting Texture2D/Sprite to {output_path}")
        
        def _save_metadata(exported_format: str):
            meta = {
                'width': getattr(data, 'm_Width', 'Unknown'),
                'height': getattr(data, 'm_Height', 'Unknown'),
                'format_unity': str(getattr(data, 'm_Format', 'Unknown')),
                'exported_format': exported_format,
                'filter_mode': str(getattr(data, 'm_FilterMode', 'Unknown')),
                'wrap_mode': str(getattr(data, 'm_WrapMode', 'Unknown')),
                'mip_count': getattr(data, 'm_MipCount', 1),
                'readable': getattr(data, 'm_IsReadable', False)
            }
            with open(f"{output_path}_meta.json", 'w', encoding='utf-8') as f:
                json.dump(meta, f, indent=2)

        if hasattr(data, 'image') and data.image:
            img = data.image
            
            # If RGB/L or RGBA with fully opaque alpha channel, save as JPG to reduce zip payload
            if img.mode in ('RGB', 'L') or (img.mode == 'RGBA' and img.getextrema()[3][0] == 255):
                try:
                    if img.mode == 'RGBA':
                        img = img.convert('RGB')
                    img.save(f"{output_path}.jpg", optimize=True, quality=90)
                    _save_metadata('jpg')
                    return True
                except Exception as jpg_e:
                    local_logger.warning(f"Failed saving JPG for {output_path}, falling back to PNG: {jpg_e}")
            
            img.save(f"{output_path}.png", optimize=True)
            _save_metadata('png')
            return True

        elif hasattr(data, 'm_StreamData') and data.m_StreamData:
            with open(f"{output_path}.raw", 'wb') as f:
                f.write(data.m_StreamData)
            _save_metadata('raw_stream')
            return True

        elif hasattr(data, 'image_data') and data.image_data:
            with open(f"{output_path}.raw_imgdata", 'wb') as f:
                f.write(data.image_data)
            _save_metadata('raw_imagedata')
            return True
        
        return False
    except Exception as e:
        local_logger.error(f"Failed exporting texture {output_path}: {e}", exc_info=debug_mode)
        return False

# 2. Wavefront Mesh OBJ
def export_mesh_obj(data: Any, output_path: str, debug_mode: bool, local_logger: logging.Logger) -> bool:
    try:
        local_logger.debug(f"Exporting Mesh to {output_path}")
        vertices = getattr(data, 'm_Vertices', [])
        indices = getattr(data, 'm_IndexBuffer', [])
        normals = getattr(data, 'm_Normals', [])
        uvs = getattr(data, 'm_UV', [])
        
        if not vertices:
            return False
        
        obj_lines = [
            "# Wavefront OBJ file exported by UnityBundleExtractor",
            f"# Source Mesh: {getattr(data, 'm_Name', 'Unknown')}",
            f"# Vertices: {len(vertices)}",
            f"# Faces: {len(indices) // 3 if indices else 0}",
            ""
        ]
        
        for v in vertices:
            if len(v) >= 3:
                obj_lines.append(f"v {v[0]} {v[1]} {v[2]}")
        
        if normals:
            obj_lines.append("")
            for n in normals:
                if len(n) >= 3:
                    obj_lines.append(f"vn {n[0]} {n[1]} {n[2]}")
        
        if uvs:
            obj_lines.append("")
            for uv in uvs:
                if len(uv) >= 2:
                    obj_lines.append(f"vt {uv[0]} {uv[1]}")

        if indices:
            obj_lines.append("\ng mesh")
            for i in range(0, len(indices) - 2, 3):
                v1, v2, v3 = indices[i] + 1, indices[i+1] + 1, indices[i+2] + 1
                if normals and uvs:
                    obj_lines.append(f"f {v1}/{v1}/{v1} {v2}/{v2}/{v2} {v3}/{v3}/{v3}")
                elif uvs:
                    obj_lines.append(f"f {v1}/{v1} {v2}/{v2} {v3}/{v3}")
                elif normals:
                    obj_lines.append(f"f {v1}//{v1} {v2}//{v2} {v3}//{v3}")
                else:
                    obj_lines.append(f"f {v1} {v2} {v3}")

        with open(f"{output_path}.obj", 'w', encoding='utf-8') as f:
            f.write('\n'.join(obj_lines))
        
        # Calculate bounding box
        bounds = None
        if vertices:
            min_coords = [float('inf')] * 3
            max_coords = [float('-inf')] * 3
            for vertex in vertices:
                if len(vertex) >= 3:
                    for i in range(3):
                        min_coords[i] = min(min_coords[i], vertex[i])
                        max_coords[i] = max(max_coords[i], vertex[i])
            bounds = {
                'min': min_coords,
                'max': max_coords,
                'center': [(min_coords[i] + max_coords[i]) / 2 for i in range(3)],
                'size': [max_coords[i] - min_coords[i] for i in range(3)]
            }

        metadata = {
            'vertex_count': len(vertices),
            'triangle_count': len(indices) // 3 if indices else 0,
            'has_normals': bool(normals),
            'has_uvs': bool(uvs),
            'bounds': bounds
        }
        with open(f"{output_path}_meta.json", 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2)

        return True
    except Exception as e:
        local_logger.error(f"Failed exporting mesh {output_path}: {e}", exc_info=debug_mode)
        return False

# 3. AudioClip
def export_audio(data: Any, output_path: str, debug_mode: bool, local_logger: logging.Logger) -> bool:
    try:
        local_logger.debug(f"Exporting AudioClip to {output_path}")
        if not hasattr(data, 'm_AudioData') or not data.m_AudioData:
            return False
        
        audio_data = data.m_AudioData
        
        # Detect format
        audio_format = 'unknown'
        if len(audio_data) >= 4:
            header = audio_data[:4]
            if header == b'OggS':
                audio_format = 'ogg'
            elif header == b'RIFF' and len(audio_data) >= 12 and audio_data[8:12] == b'WAVE':
                audio_format = 'wav'
            elif header == b'fLaC':
                audio_format = 'flac'
            elif audio_data.startswith(b'ID3') or audio_data[0:2] in (b'\xff\xfb', b'\xff\xf3'):
                audio_format = 'mp3'
                
        ext = f".{audio_format}" if audio_format != 'unknown' else '.audio'
        with open(f"{output_path}{ext}", 'wb') as f:
            f.write(audio_data)
            
        metadata = {
            'format': audio_format,
            'size_bytes': len(audio_data),
            'channels': getattr(data, 'm_Channels', 0),
            'frequency': getattr(data, 'm_Frequency', 0),
            'length_seconds': getattr(data, 'm_Length', 0.0),
            'compression': str(getattr(data, 'm_CompressionFormat', 'Unknown'))
        }
        with open(f"{output_path}_meta.json", 'w') as f:
            json.dump(metadata, f, indent=2)

        return True
    except Exception as e:
        local_logger.error(f"Failed exporting audio {output_path}: {e}", exc_info=debug_mode)
        return False

# 4. Font
def export_font(data: Any, output_path: str, debug_mode: bool, local_logger: logging.Logger) -> bool:
    try:
        local_logger.debug(f"Exporting Font to {output_path}")
        if not hasattr(data, 'm_FontData') or not data.m_FontData:
            return False
        
        font_data = data.m_FontData
        header = font_data[:4]
        ext = '.font'
        if header == b'OTTO':
            ext = '.otf'
        elif header in [b'\x00\x01\x00\x00', b'true']:
            ext = '.ttf'
        
        with open(f"{output_path}{ext}", 'wb') as f:
            f.write(font_data)

        metadata = {
            'format': ext[1:],
            'size_bytes': len(font_data),
            'font_name': getattr(data, 'm_Name', 'Unknown'),
        }
        with open(f"{output_path}_meta.json", 'w') as f:
            json.dump(metadata, f, indent=2)

        return True
    except Exception as e:
        local_logger.error(f"Failed exporting font {output_path}: {e}", exc_info=debug_mode)
        return False

# 5. Shader
def export_shader(data: Any, output_path: str, debug_mode: bool, local_logger: logging.Logger) -> bool:
    try:
        local_logger.debug(f"Exporting Shader to {output_path}")
        shader_content = getattr(data, 'm_Script', '')
        if not shader_content:
            return False
        
        with open(f"{output_path}.shader", 'w', encoding='utf-8', errors='replace') as f:
            f.write(shader_content)
            
        properties = []
        try:
            if hasattr(data, 'm_ParsedForm') and hasattr(data.m_ParsedForm, 'm_PropInfo'):
                prop_info = data.m_ParsedForm.m_PropInfo
                if hasattr(prop_info, 'm_Props'):
                    for prop in prop_info.m_Props:
                        properties.append({
                            'name': getattr(prop, 'm_Name', ''),
                            'description': getattr(prop, 'm_Description', ''),
                            'type': str(getattr(prop, 'm_Type', 'Unknown'))
                        })
        except Exception as prop_e:
            local_logger.warning(f"Failed parsing shader properties: {prop_e}")

        metadata = {
            'name': getattr(data, 'm_Name', 'Unknown'),
            'properties': properties
        }
        with open(f"{output_path}_meta.json", 'w') as f:
            json.dump(metadata, f, indent=2)

        return True
    except Exception as e:
        local_logger.error(f"Failed exporting shader {output_path}: {e}", exc_info=debug_mode)
        return False

# 6. TextAsset
def export_text_asset(data: Any, output_path: str, debug_mode: bool, local_logger: logging.Logger) -> bool:
    try:
        local_logger.debug(f"Exporting TextAsset to {output_path}")
        if not hasattr(data, 'm_Script'):
            return False
        
        content = data.m_Script
        if isinstance(content, bytes):
            content = content.decode('utf-8', errors='replace')
        
        if not content.strip():
            return False
        
        content_stripped = content.strip()
        ext = '.txt'
        if content_stripped.startswith(('{', '[')):
            ext = '.json'
        elif content_stripped.startswith('<?xml'):
            ext = '.xml'
        elif content_stripped.startswith('---'):
            ext = '.yaml'
        
        with open(f"{output_path}{ext}", 'w', encoding='utf-8') as f:
            f.write(content)

        return True
    except Exception as e:
        local_logger.error(f"Failed exporting text asset {output_path}: {e}", exc_info=debug_mode)
        return False

# 7. MonoScript (C# Scripts)
def export_mono_script(data: Any, output_path: str, debug_mode: bool, local_logger: logging.Logger) -> bool:
    try:
        local_logger.debug(f"Exporting MonoScript to {output_path}")
        script_content = getattr(data, 'm_Script', '')
        if isinstance(script_content, bytes):
            script_content = ''

        if not script_content or not script_content.strip():
            return export_generic(data, output_path, "MonoScript", debug_mode, local_logger)

        with open(f"{output_path}.cs", 'w', encoding='utf-8') as f:
            f.write(script_content)

        metadata = {
            'class_name': getattr(data, 'm_ClassName', ''),
            'namespace': getattr(data, 'm_Namespace', ''),
            'assembly_name': getattr(data, 'm_AssemblyName', '')
        }
        with open(f"{output_path}_meta.json", 'w') as f:
            json.dump(metadata, f, indent=2)
        
        return True
    except Exception as e:
        local_logger.error(f"Failed exporting mono script {output_path}: {e}", exc_info=debug_mode)
        return False

# 8. Material
def export_material(data: Any, output_path: str, debug_mode: bool, local_logger: logging.Logger) -> bool:
    try:
        local_logger.debug(f"Exporting Material to {output_path}")
        material_info = {
            'name': getattr(data, 'm_Name', 'Unknown'),
            'shader_path_id': str(getattr(getattr(data, 'm_Shader', None), 'path_id', 0)) if hasattr(data, 'm_Shader') else '0',
            'properties': {}
        }
        if hasattr(data, 'm_SavedProperties'):
            props = data.m_SavedProperties
            
            if hasattr(props, 'm_TexEnvs'):
                material_info['properties']['textures'] = {
                    tex.first: {'texture_path_id': str(getattr(getattr(tex.second, 'm_Texture', None), 'path_id', 0)) if hasattr(tex.second, 'm_Texture') else '0'}
                    for tex in props.m_TexEnvs if hasattr(tex, 'first')
                }
            if hasattr(props, 'm_Floats'):
                material_info['properties']['floats'] = {
                    f.first: f.second for f in props.m_Floats if hasattr(f, 'first')
                }
            if hasattr(props, 'm_Colors'):
                material_info['properties']['colors'] = {
                    c.first: {'r': c.second.r, 'g': c.second.g, 'b': c.second.b, 'a': c.second.a}
                    for c in props.m_Colors if hasattr(c, 'first')
                }
        
        with open(f"{output_path}.mat.json", 'w', encoding='utf-8') as f:
            json.dump(material_info, f, indent=2, ensure_ascii=False)
        
        return True
    except Exception as e:
        local_logger.error(f"Failed exporting material {output_path}: {e}", exc_info=debug_mode)
        return False

# 9. Video/Movie
def export_video(data: Any, output_path: str, debug_mode: bool, local_logger: logging.Logger) -> bool:
    try:
        local_logger.debug(f"Exporting Video to {output_path}")
        video_data = getattr(data, 'm_MovieData', None)
        if not video_data:
            return False
        
        ext = '.mov'
        if len(video_data) >= 8:
            header = video_data[:8]
            if header[4:8] == b'ftyp':
                ext = '.mp4'
            elif header[:4] == b'RIFF' and len(video_data) >= 12 and video_data[8:12] == b'WAVE':
                ext = '.wav'
            elif header[:3] == b'FLV':
                ext = '.flv'
            elif header[:2] == b'\x1a\x45':
                ext = '.mkv'
                
        with open(f"{output_path}{ext}", 'wb') as f:
            f.write(video_data)

        return True
    except Exception as e:
        local_logger.error(f"Failed exporting video {output_path}: {e}", exc_info=debug_mode)
        return False

# 10. Generic (Fallback serialization)
def export_generic(data: Any, output_path: str, obj_type: str, debug_mode: bool, local_logger: logging.Logger) -> bool:
    try:
        local_logger.debug(f"Exporting Generic Object {obj_type} to {output_path}")
        try:
            type_tree_data = data.read_typetree()
        except Exception:
            type_tree_data = _serialize_object(data)
        
        if not type_tree_data:
            return False
        
        with open(f"{output_path}.json", 'w', encoding='utf-8') as f:
            json.dump(type_tree_data, f, indent=2, ensure_ascii=False, default=str)
        
        return True
    except Exception as e:
        local_logger.error(f"Failed exporting generic object {output_path} ({obj_type}): {e}", exc_info=debug_mode)
        return False
