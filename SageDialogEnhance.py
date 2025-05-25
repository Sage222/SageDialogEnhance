import os
import subprocess
from pathlib import Path

# === CONFIGURATION ===
SOURCE_FOLDER = "./videos"
OUTPUT_FOLDER = os.path.join(SOURCE_FOLDER, "processed")
SUPPORTED_EXTENSIONS = [".mkv", ".mp4", ".mov"]

# Enhanced audio filter chain for clearer dialog
AUDIO_FILTERS = (
    "acompressor=threshold=-25dB:ratio=6:attack=10:release=200,"
    "equalizer=f=3000:t=q:w=1:g=3,"
    "equalizer=f=100:t=q:w=1:g=-3,"
    "loudnorm,"
    "volume=5dB"
)

def get_audio_info(input_file):
    """Extract codec and bitrate from the first audio stream using ffprobe."""
    print(f"[DEBUG] Getting audio info for: {input_file}")
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=codec_name,bit_rate",
        "-of", "default=noprint_wrappers=1:nokey=1",
        input_file
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        print(f"[DEBUG] ffprobe output:\n{result.stdout}")
        lines = result.stdout.strip().splitlines()
        codec_name = lines[0] if len(lines) > 0 else "aac"
        bitrate_bps = int(lines[1]) if len(lines) > 1 else None
        bitrate_kbps = f"{bitrate_bps // 1000}k" if bitrate_bps else None
        return codec_name, bitrate_kbps
    except Exception as e:
        print(f"[ERROR] Failed to get audio info: {e}")
        return "aac", "192k"

def process_video(input_file, output_file, codec, bitrate):
    """Re-encode audio with enhanced dialog filters and copy the video stream using all CPU cores."""
    cmd = [
        "ffmpeg", "-y",
        "-i", input_file,
        "-c:v", "copy",
        "-c:a", codec,
    ]

    if bitrate:
        cmd += ["-b:a", bitrate]

    cmd += [
        "-af", AUDIO_FILTERS,
        "-threads", "0",  # Use all CPU cores
        output_file
    ]

    print(f"\n[INFO] Processing: {input_file}")
    print(f"[DEBUG] Output path: {output_file}")
    print(f"[DEBUG] Codec: {codec}, Bitrate: {bitrate}")
    print(f"[DEBUG] Running FFmpeg command:\n{' '.join(cmd)}")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        print(f"[DEBUG] FFmpeg stdout:\n{result.stdout}")
        print(f"[DEBUG] FFmpeg stderr:\n{result.stderr}")

        if result.returncode != 0:
            print(f"[ERROR] FFmpeg failed with return code {result.returncode}")
        elif os.path.exists(output_file):
            print(f"[SUCCESS] Output file created: {output_file}")
        else:
            print(f"[WARNING] FFmpeg ran but output file not found.")

    except Exception as e:
        print(f"[EXCEPTION] Exception during FFmpeg processing: {e}")

def main():
    print(f"[INFO] Scanning folder: {SOURCE_FOLDER}")
    if not os.path.isdir(SOURCE_FOLDER):
        print(f"[ERROR] Source folder does not exist: {SOURCE_FOLDER}")
        return

    os.makedirs(OUTPUT_FOLDER, exist_ok=True)

    found_files = [
        f for f in os.listdir(SOURCE_FOLDER)
        if f.lower().endswith(tuple(SUPPORTED_EXTENSIONS))
        and os.path.isfile(os.path.join(SOURCE_FOLDER, f))
    ]

    print(f"[INFO] Found {len(found_files)} video file(s):")
    for f in found_files:
        print(f" - {f}")

    if not found_files:
        print(f"[WARNING] No valid video files in {SOURCE_FOLDER}")
        return

    for file_name in found_files:
        input_path = os.path.join(SOURCE_FOLDER, file_name)
        output_name = Path(file_name).stem + "_enhanced.mkv"
        output_path = os.path.join(OUTPUT_FOLDER, output_name)

        codec, bitrate = get_audio_info(input_path)
        process_video(input_path, output_path, codec, bitrate)

    print("\n✅ Script complete.")

if __name__ == "__main__":
    main()
