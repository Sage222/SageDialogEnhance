import os
import subprocess
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
from threading import Thread
import queue
import sys

# === CONFIGURATION ===
SUPPORTED_EXTENSIONS = [".mkv", ".mp4", ".mov"]
AUDIO_FILTERS = (
    "equalizer=f=50:t=q:w=2:g=-12,"     # STRONGER bass cut @50Hz
    "equalizer=f=100:t=q:w=2:g=-12,"   # STRONGER bass cut @100Hz
    "equalizer=f=150:t=q:w=2:g=-8,"     # Additional cut at 150Hz
    "speechnorm=e=6.25:r=0.00001:l=1"   #Moderate and slow amplification: hxxps://ffmpeg.org/ffmpeg-filters.html#speechnorm
)

# A special token to signal that processing is complete
PROCESSING_DONE_TOKEN = "__PROCESSING_DONE__"


class VideoProcessor:
    def __init__(self, log_queue, debug_queue, progress_queue):
        self.log_queue = log_queue
        self.debug_queue = debug_queue
        self.progress_queue = progress_queue
        self._stop_event = False
        self._current_process = None

    def stop_processing(self):
        self._stop_event = True
        if self._current_process:
            try:
                # Terminate sends a SIGTERM, which is the standard way to stop a process
                self._current_process.terminate()
            except OSError:
                # Process may have already terminated, which is fine
                pass

    def get_audio_info(self, input_file):
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "a:0",
            "-show_entries", "stream=codec_name,bit_rate",
            "-of", "default=noprint_wrappers=1:nokey=1",
            input_file
        ]
        try:
            # Add creationflags here as well to prevent console window flashing
            creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            result = subprocess.run(cmd, capture_output=True, text=True, check=True, creationflags=creation_flags)
            self.debug_queue.put(f"[DEBUG] Audio info: {result.stdout.strip()}")
            lines = result.stdout.strip().splitlines()
            codec = lines[0] if len(lines) > 0 else "aac"
            bitrate = f"{int(lines[1]) // 1000}k" if len(lines) > 1 and lines[1].isdigit() else "192k"
            return codec, bitrate
        except Exception as e:
            self.debug_queue.put(f"[DEBUG] Audio info error: {str(e)}")
            return "aac", "192k"

    # THIS METHOD CONTAINS THE CRITICAL FIX
    def process_video(self, input_path, output_path):
        if self._stop_event:
            return False

        codec, bitrate = self.get_audio_info(input_path)
        cmd = [
            "ffmpeg", "-y", "-i", input_path,
            "-c:v", "copy", "-c:a", codec, "-b:a", bitrate,
            "-af", AUDIO_FILTERS,
            "-progress", "pipe:1",
            output_path
        ]

        self.log_queue.put(f"\n[PROCESSING] {os.path.basename(input_path)}")
        self.debug_queue.put(f"[DEBUG] Command: {' '.join(cmd)}")

        try:
            creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            self._current_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,  # <-- MERGE STDERR INTO STDOUT
                universal_newlines=True,
                encoding='utf-8',
                errors='replace', # Prevent crashes on weird characters
                creationflags=creation_flags
            )

            # Read the combined output stream line by line
            for line in self._current_process.stdout:
                if self._stop_event:
                    # If stop is requested, we still need to let the process terminate
                    break
                line = line.strip()
                if not line:
                    continue
                
                # Check if it's progress info or other ffmpeg output
                if "progress=" in line or "out_time_ms=" in line:
                    self._parse_progress(line)
                
                # Send ALL output to the debug log for inspection
                self.debug_queue.put(line)
            
            # Wait for the process to fully terminate
            self._current_process.wait()

            # Check the outcome
            if self._stop_event:
                self.log_queue.put("[STOPPED] Process was stopped by user.")
                return False

            if self._current_process.returncode == 0:
                self.log_queue.put(f"[SUCCESS] {os.path.basename(output_path)}")
                self.progress_queue.put(("file_progress", 100)) # Ensure bar is full on success
                return True
            else:
                self.log_queue.put(f"[FAILED] FFmpeg exited with error code: {self._current_process.returncode}")
                return False

        except Exception as e:
            self.debug_queue.put(f"[DEBUG] Process crash: {str(e)}")
            self.log_queue.put(f"[CRASH] {str(e)}")
            return False
        finally:
            self._current_process = None

    def _parse_progress(self, line):
        # This function is simplistic. A real implementation would parse
        # total duration and calculate a percentage from 'out_time_ms'.
        if "out_time_ms=" in line:
            self.progress_queue.put(("file_progress", 50)) # Show activity
        elif "progress=end" in line:
            self.progress_queue.put(("file_progress", 100))

class VideoProcessorApp:
    # No changes needed in the GUI class from the previous version
    def __init__(self, root):
        self.root = root
        self.root.title("Sage Dialog Enhancer v5")
        self.file_list = []
        self.processing_thread = None
        self.processor = None
        self.log_queue = queue.Queue()
        self.debug_queue = queue.Queue()
        self.progress_queue = queue.Queue()
        self.setup_ui()
        self.setup_drag_and_drop()
        self.root.after(100, self.process_events)

    def setup_ui(self):
        main_frame = tk.Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        file_frame = tk.LabelFrame(main_frame, text="Video Files")
        file_frame.pack(fill=tk.X, pady=5)
        tk.Label(file_frame, text="Drag and drop videos or use 'Add Files'").pack(anchor=tk.W, padx=5)
        self.file_box = scrolledtext.ScrolledText(file_frame, height=8, wrap=tk.NONE)
        self.file_box.pack(fill=tk.X, padx=5, pady=5)
        btn_frame = tk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, pady=5)
        tk.Button(btn_frame, text="Add Files", command=self.add_files).pack(side=tk.LEFT)
        tk.Button(btn_frame, text="Clear List", command=self.clear_list).pack(side=tk.LEFT, padx=5)
        self.process_btn = tk.Button(btn_frame, text="Start Processing", command=self.start_processing)
        self.process_btn.pack(side=tk.LEFT)
        self.stop_btn = tk.Button(btn_frame, text="Stop", command=self.stop_processing, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=5)
        progress_frame = tk.LabelFrame(main_frame, text="Progress")
        progress_frame.pack(fill=tk.X, pady=5)
        self.overall_progress = ttk.Progressbar(progress_frame, orient=tk.HORIZONTAL, mode='determinate')
        self.overall_progress.pack(fill=tk.X, padx=5, pady=2)
        self.file_progress = ttk.Progressbar(progress_frame, orient=tk.HORIZONTAL, mode='determinate')
        self.file_progress.pack(fill=tk.X, padx=5, pady=2)
        self.status_label = tk.Label(progress_frame, text="Ready")
        self.status_label.pack(pady=5)
        notebook = ttk.Notebook(main_frame)
        notebook.pack(fill=tk.BOTH, expand=True, pady=5)
        log_tab = tk.Frame(notebook)
        self.log_text = scrolledtext.ScrolledText(log_tab, state=tk.DISABLED, wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        notebook.add(log_tab, text="Log")
        debug_tab = tk.Frame(notebook)
        self.debug_text = scrolledtext.ScrolledText(debug_tab, state=tk.DISABLED, wrap=tk.WORD)
        self.debug_text.pack(fill=tk.BOTH, expand=True)
        notebook.add(debug_tab, text="Debug")

    def setup_drag_and_drop(self):
        try:
            from tkinterdnd2 import DND_FILES, TkinterDnD
            self.root.drop_target_register(DND_FILES)
            self.root.dnd_bind('<<Drop>>', self.on_drop)
            self.debug_queue.put("[DEBUG] Drag-and-drop enabled")
        except ImportError:
            self.log_text.config(state=tk.NORMAL)
            self.log_text.insert(tk.END, "INFO: For drag-and-drop support, install tkinterdnd2:\n"
                                          "pip install tkinterdnd2\n")
            self.log_text.config(state=tk.DISABLED)
        except Exception as e:
            self.debug_queue.put(f"[DEBUG] Drag-and-drop error: {str(e)}")

    def process_events(self):
        try:
            while not self.log_queue.empty():
                msg = self.log_queue.get_nowait()
                if msg == PROCESSING_DONE_TOKEN:
                    self.on_processing_finished()
                else:
                    self.log(msg)
            while not self.debug_queue.empty():
                msg = self.debug_queue.get_nowait()
                self.debug(msg)
            while not self.progress_queue.empty():
                progress_type, value = self.progress_queue.get_nowait()
                if progress_type == "file_progress":
                    self.file_progress['value'] = value
                elif progress_type == "overall_progress":
                    self.overall_progress['value'] = value
        except queue.Empty:
            pass
        finally:
            self.root.after(100, self.process_events)

    def log(self, message):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    def debug(self, message):
        self.debug_text.config(state=tk.NORMAL)
        self.debug_text.insert(tk.END, message + "\n")
        self.debug_text.see(tk.END)
        self.debug_text.config(state=tk.DISABLED)

    def add_files(self):
        files = filedialog.askopenfilenames(
            title="Select Video Files",
            filetypes=[("Video Files", " ".join(SUPPORTED_EXTENSIONS))]
        )
        if files:
            self.add_to_list(files)

    def on_drop(self, event):
        files = self.root.tk.splitlist(event.data)
        self.add_to_list([f for f in files if Path(f).suffix.lower() in SUPPORTED_EXTENSIONS])

    def add_to_list(self, files):
        for f in files:
            f_path = Path(f)
            if str(f_path) not in self.file_list:
                self.file_list.append(str(f_path))
                self.file_box.insert(tk.END, f_path.name + "\n")

    def clear_list(self):
        self.file_list.clear()
        self.file_box.delete(1.0, tk.END)

    def start_processing(self):
        if not self.file_list:
            messagebox.showwarning("No Files", "Please add video files to the list before processing.")
            return
        if self.processing_thread and self.processing_thread.is_alive():
            messagebox.showwarning("In Progress", "Processing is already running.")
            return

        self.overall_progress['maximum'] = len(self.file_list)
        self.overall_progress['value'] = 0
        self.file_progress['value'] = 0
        self.status_label.config(text="Starting...")
        self.process_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)

        self.processor = VideoProcessor(self.log_queue, self.debug_queue, self.progress_queue)
        self.processing_thread = Thread(target=self.run_processing, daemon=True)
        self.processing_thread.start()

    def stop_processing(self):
        if self.processor:
            self.log_queue.put("[STOPPING] User requested to stop...")
            self.processor.stop_processing()
            self.stop_btn.config(state=tk.DISABLED)

    def run_processing(self):
        try:
            total_files = len(self.file_list)
            for i, input_path in enumerate(self.file_list):
                if self.processor._stop_event:
                    break
                
                self.log_queue.put(f"Starting file {i+1} of {total_files}: {Path(input_path).name}")
                self.progress_queue.put(("file_progress", 0))

                output_folder = Path(input_path).parent / "processed"
                output_folder.mkdir(exist_ok=True)
                output_name = Path(input_path).stem + "_enhanced.mkv"
                output_path = output_folder / output_name

                if output_path.exists():
                    self.log_queue.put(f"[SKIP] Exists: {output_name}")
                else:
                    self.processor.process_video(str(input_path), str(output_path))
                
                self.progress_queue.put(("overall_progress", i + 1))
        except Exception as e:
            self.log_queue.put(f"\n[CRITICAL THREAD ERROR] {str(e)}")
        finally:
            self.log_queue.put(PROCESSING_DONE_TOKEN)

    def on_processing_finished(self):
        if self.processor and self.processor._stop_event:
            self.status_label.config(text="Stopped by user")
        else:
            self.status_label.config(text="Finished!")
        self.process_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self.file_progress['value'] = 0

if __name__ == "__main__":
    try:
        from tkinterdnd2 import TkinterDnD
        root = TkinterDnD.Tk()
    except ImportError:
        root = tk.Tk()
    
    app = VideoProcessorApp(root)
    root.mainloop()