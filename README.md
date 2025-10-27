# SageDialogEnhance
Python batch that makes dialog clearer in mkv or mp4 files using FFProbe and FFMpeg

This Python script processes all video files in a specified videos folder to enhance their audio dialogue clarity without altering the video stream.

Key features:

Scans the videos folder for supported video files (.mkv, .mp4, .mov).

Extracts audio codec and bitrate information from each video using ffprobe to preserve original audio quality settings.

Re-encodes only the audio stream with advanced audio filters to improve dialogue clarity, including:

Dynamic range compression tuned for speech,

Reduces 150Hz and below frequncies. 

Equalization to boost vocal frequencies and reduce muddiness,

Loudness normalization,

Optional volume boost.

Copies the video stream unchanged to preserve original video quality.

Uses ffmpeg with -threads 0 to leverage all CPU cores for faster processing.

Saves the enhanced output videos with an _enhanced suffix inside a processed subfolder.

Output: Videos with clearer, more intelligible dialogue and the same video quality as the original.



Instructions:

Download the Full FFMPEG (which includes FFProbe).
Put them in the same folder as the script.
Make a "videos" subfolder with the source videos.
Drag and Drop the files into the GUI or click 'Add Files" - Click Start Processing. See Debug tab to for details as it executes.
Execute the script. It will leave the original files untouched and add a new folder called "processed".


v6 - Added "Settings" Tab for easy adjustment of parameters
