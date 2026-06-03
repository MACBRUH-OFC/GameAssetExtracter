import os, json, logging

def _detect_audio_format(audio_data: bytes) -> str:
    if len(audio_data) < 4: return 'unknown'
    header = audio_data[:4]
    if header == b'OggS': return 'ogg'
    if header == b'RIFF': return 'wav'
    if audio_data.startswith(b'ID3'): return 'mp3'
    return 'unknown'

def export_audio(data, output_path, debug_mode, local_logger) -> bool:
    try:
        if not hasattr(data, 'm_AudioData') or not data.m_AudioData: return False
        audio_data = data.m_AudioData
        fmt = _detect_audio_format(audio_data)
        ext = f".{fmt}" if fmt != 'unknown' else '.audio'
        
        with open(f"{output_path}{ext}", 'wb') as f:
            f.write(audio_data)
        
        meta = {'format': fmt, 'length': getattr(data, 'm_Length', 0)}
        with open(f"{output_path}_meta.json", 'w') as f:
            json.dump(meta, f, indent=2)
        return True
    except Exception as e:
        local_logger.error(f"Audio error: {e}")
        return False
