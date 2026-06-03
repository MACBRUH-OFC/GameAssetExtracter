def export_video(data, output_path, debug_mode, local_logger):
    try:
        video_data = getattr(data, 'm_VideoData', getattr(data, 'm_MovieData', None))
        if video_data:
            with open(f"{output_path}.mp4", 'wb') as f:
                f.write(video_data)
            return True
        return False
    except Exception:
        return False
