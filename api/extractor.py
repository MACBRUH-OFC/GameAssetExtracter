import os
import io
import json
import logging
import zipfile
import UnityPy
from collections import defaultdict
from typing import Any, List, Dict

from api.utils import sanitize_filename, get_file_info
from api.exporters import (
    export_texture, export_mesh_obj, export_audio, export_font,
    export_shader, export_text_asset, export_mono_script,
    export_material, export_video, export_generic
)

logger = logging.getLogger(__name__)

def get_object_name(obj: Any) -> str:
    """
    Infers a sanitized, descriptive name for a Unity object from its properties.
    """
    if isinstance(obj, str):
        return sanitize_filename(obj)

    try:
        data = obj.read()
        
        # 1. Direct name attributes
        name_attributes = ["m_Name", "name"]
        for attr in name_attributes:
            if hasattr(data, attr) and getattr(data, attr):
                return sanitize_filename(getattr(data, attr))

        # 2. MonoBehaviour Gameobject link name fallback
        if hasattr(data, 'm_GameObject') and getattr(data, 'm_GameObject') and getattr(data.m_GameObject, 'path_id', 0) != 0:
            try:
                game_object = data.m_GameObject.read()
                if hasattr(game_object, 'm_Name') and game_object.m_Name:
                    return sanitize_filename(f"{game_object.m_Name}_{obj.type.name}")
            except Exception:
                pass
        
        # 3. MonoScript ClassName
        if obj.type.name == "MonoScript" and hasattr(data, "m_ClassName") and data.m_ClassName:
            return sanitize_filename(data.m_ClassName)
            
        # 4. MonoBehaviour Script ClassName fallback
        if obj.type.name == "MonoBehaviour" and hasattr(data, 'm_Script') and getattr(data, 'm_Script') and getattr(data.m_Script, 'path_id', 0) != 0:
            try:
                script = data.m_Script.read()
                if hasattr(script, 'm_ClassName') and script.m_ClassName:
                     return sanitize_filename(script.m_ClassName)
            except Exception:
                pass

    except Exception as e:
        logger.warning(f"Could not read object properties for {obj.path_id}: {e}")

    # Fallback to unique class name and path id
    return f"{obj.type.name}_{obj.path_id}"

def build_asset_inventory(objects: List[Any], local_logger: logging.Logger, debug_mode: bool) -> Dict[str, List[Dict]]:
    """
    Iterates over all bundle objects and builds a categorized list of assets with size estimations.
    """
    asset_categories = defaultdict(list)
    
    for i, obj in enumerate(objects):
        try:
            obj_type_name = obj.type.name
            obj_size = 0

            try:
                # Attempt to calculate realistic export size without writing to disk
                data = obj.read()
                temp_buffer = io.BytesIO()
                
                if obj_type_name in ["Texture2D", "Sprite"]:
                    if hasattr(data, 'image') and data.image:
                        img = data.image
                        img.save(temp_buffer, format='PNG')
                
                elif obj_type_name == "AudioClip":
                    if hasattr(data, "m_AudioData") and data.m_AudioData:
                        temp_buffer.write(data.m_AudioData)

                elif obj_type_name == "Font":
                    if hasattr(data, "m_FontData") and data.m_FontData:
                        temp_buffer.write(data.m_FontData)

                elif obj_type_name == "Mesh":
                    if hasattr(data, 'export'):
                        obj_data = data.export().encode('utf-8')
                        temp_buffer.write(obj_data)

                elif obj_type_name in ["Shader", "TextAsset", "MonoScript"]:
                     if hasattr(data, 'm_Script') and isinstance(data.m_Script, (bytes, str)):
                        script_data = data.m_Script.encode('utf-8', errors='replace') if isinstance(data.m_Script, str) else data.m_Script
                        temp_buffer.write(script_data)

                elif obj_type_name in ["MovieTexture", "VideoClip"]:
                    if hasattr(data, "m_MovieData") and data.m_MovieData:
                        temp_buffer.write(data.m_MovieData)

                else:
                    try:
                        typetree_json = json.dumps(data.read_typetree(), default=str).encode('utf-8', errors='replace')
                        temp_buffer.write(typetree_json)
                    except Exception:
                        obj_size = obj.data_size
                
                if temp_buffer.tell() > 0:
                    obj_size = temp_buffer.tell()
                else:
                    obj_size = obj.data_size
                temp_buffer.close()
            
            except Exception as size_e:
                local_logger.debug(f"Could not accurately estimate size for {obj.path_id} ({obj_type_name}): {size_e}")
                obj_size = obj.data_size
            
            asset_info = {
                'index': i,
                'path_id': str(obj.path_id),
                'name': get_object_name(obj),
                'type': obj_type_name,
                'estimated_size': obj_size,
                'class_id': obj.type.value if hasattr(obj.type, 'value') else 0
            }
            asset_categories[obj_type_name].append(asset_info)
        except Exception as e:
            local_logger.error(f"Error processing object {i} (PathID: {obj.path_id}) for inventory: {e}", exc_info=debug_mode)
            
    return dict(asset_categories)

def extract_single_asset(obj: Any, base_dir: str, local_logger: logging.Logger, debug_mode: bool) -> bool:
    """
    Extracts a single UnityPy object by routing it to its specialized exporter.
    """
    obj_type = obj.type.name
    obj_name = get_object_name(obj)
    
    type_dir = os.path.join(base_dir, sanitize_filename(obj_type))
    os.makedirs(type_dir, exist_ok=True)
    output_path = os.path.join(type_dir, obj_name)
    
    success = False
    try:
        data = obj.read()
        
        if obj_type in ["Texture2D", "Sprite"]:
            success = export_texture(data, output_path, debug_mode, local_logger)
        elif obj_type == "Mesh":
            success = export_mesh_obj(data, output_path, debug_mode, local_logger)
        elif obj_type == "AudioClip":
            success = export_audio(data, output_path, debug_mode, local_logger)
        elif obj_type == "Font":
            success = export_font(data, output_path, debug_mode, local_logger)
        elif obj_type == "Shader":
            success = export_shader(data, output_path, debug_mode, local_logger)
        elif obj_type == "TextAsset":
            success = export_text_asset(data, output_path, debug_mode, local_logger)
        elif obj_type == "MonoScript":
            success = export_mono_script(data, output_path, debug_mode, local_logger)
        elif obj_type == "Material":
            success = export_material(data, output_path, debug_mode, local_logger)
        elif obj_type in ["VideoClip", "MovieTexture"]:
            success = export_video(data, output_path, debug_mode, local_logger)
        else:
            success = export_generic(data, output_path, obj_type, debug_mode, local_logger)

    except Exception as e:
        local_logger.error(f"Decoder routing failed for '{obj_name}' ({obj_type}): {e}", exc_info=debug_mode)
        success = False

    return success

def create_zip_archive(source_dir: str, original_bundle_name: str, session_id: str, local_logger: logging.Logger) -> str:
    """
    Packs extracted files into a ZIP archive without CPU-heavy compression to prevent serverless timeouts.
    """
    base_name = os.path.splitext(original_bundle_name)[0]
    sanitized_base_name = sanitize_filename(base_name)
    
    if sanitized_base_name and sanitized_base_name != "Untitled":
         zip_filename = f"{sanitized_base_name}_extracted.zip" 
    else:
         zip_filename = f"unity_assets_{session_id}.zip"

    session_zip_dir = os.path.join("/tmp", session_id)
    os.makedirs(session_zip_dir, exist_ok=True)
    zip_path = os.path.join(session_zip_dir, zip_filename)
    
    # ZIP_STORED is extremely fast since it does not compress bytes, saving CPU cycles under Vercel execution limits
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_STORED) as zipf:
        for root, _, files in os.walk(source_dir):
            for file in files:
                file_path = os.path.join(root, file)
                arc_path = os.path.relpath(file_path, source_dir)
                zipf.write(file_path, arc_path)
                
    local_logger.info(f"Successfully zipped extracted folder contents to {zip_path}")
    return zip_path
