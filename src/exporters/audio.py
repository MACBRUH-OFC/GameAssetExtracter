import os, logging

def export_audio(data, output_path, debug_mode, local_logger):
    try:
        if not hasattr(data, 'm_AudioData') or not data.m_AudioData:
            return False
        
        audio_data = data.m_AudioData
        header = audio_data[:4]
        ext = ".audio"
        if header == b'OggS': ext = ".ogg"
        elif header[:4] == b'RIFF': ext = ".wav"
        elif b'ID3' in header: ext = ".mp3"
        
        with open(f"{output_path}{ext}", 'wb') as f:
            f.write(audio_data)
        return True
    except Exception:
        return False
