import os
import subprocess
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
from threading import Thread
import queue
import sys

# --- CONFIGURATION (Default values for all parameters) ---
DEFAULT_SUPPORTED_EXTENSIONS = [".mkv", ".mp4", ".mov"]

DEFAULT_EQUALIZER_BANDS = [
    {"f": "50", "t": "q", "w": "2", "g": "-12"},   # Band 1
    {"f": "100", "t": "q", "w": "2", "g": "-10"},  # Band 2
    {"f": "150", "t": "q", "w": "2", "g": "-6"},   # Band 3
]

DEFAULT_SPEECHNORM = {
    "e": "6.25",    # expansion
    "r": "0.00001", # raise
    "l": "1"        # link channels
}

DEFAULT_OUTPUT_FOLDER = "processed"
DEFAULT_OUTPUT_SUFFIX = "_enhanced.mkv"

PROCESSING_DONE_TOKEN = "__PROCESSING_DONE__"

# --- Tooltip helper class ---
class ToolTip(object):
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tipwindow = None
        widget.bind("<Enter>", self.show_tip)
        widget.bind("<Leave>", self.hide_tip)

    def show_tip(self, event=None):
        if self.tipwindow or not self.text:
            return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + 20
        self.tipwindow = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry("+%d+%d" % (x, y))
        label = tk.Label(
            tw, text=self.text, justify=tk.LEFT,
            background="#FFFFE0", relief=tk.SOLID, borderwidth=1,
            wraplength=250
        )
        label.pack(ipadx=1)

    def hide_tip(self, event=None):
        tw = self.tipwindow
        self.tipwindow = None
        if tw:
            tw.destroy()

class VideoProcessor:
    def __init__(self, log_queue, debug_queue, progress_queue,
                 supported_extensions,
                 equalizer_bands, speechnorm_params,
                 output_folder, output_suffix):
        self.log_queue = log_queue
        self.debug_queue = debug_queue
        self.progress_queue = progress_queue
        self.supported_extensions = supported_extensions
        self.equalizer_bands = equalizer_bands
        self.speechnorm_params = speechnorm_params
        self.output_folder = output_folder
        self.output_suffix = output_suffix
        self._stop_event = False
        self._current_process = None

    def stop_processing(self):
        self._stop_event = True
        if self._current_process:
            try:
                self._current_process.terminate()
            except OSError:
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

    def build_audio_filter(self):
        eqs = []
        for band in self.equalizer_bands:
            eq_str = f"equalizer=f={band['f']}:t={band['t']}:w={band['w']}:g={band['g']}"
            eqs.append(eq_str)
        sn = self.speechnorm_params
        sn_str = f"speechnorm=e={sn['e']}:r={sn['r']}:l={sn['l']}"
        return ",".join(eqs + [sn_str])

    def process_video(self, input_path, output_path):
        if self._stop_event:
            return False

        codec, bitrate = self.get_audio_info(input_path)
        audio_filters = self.build_audio_filter()
        cmd = [
            "ffmpeg", "-y", "-i", input_path,
            "-c:v", "copy", "-c:a", codec, "-b:a", bitrate,
            "-af", audio_filters,
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
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                encoding='utf-8',
                errors='replace',
                creationflags=creation_flags
            )

            for line in self._current_process.stdout:
                if self._stop_event:
                    break
                line = line.strip()
                if not line:
                    continue
                if "progress=" in line or "out_time_ms=" in line:
                    self._parse_progress(line)
                self.debug_queue.put(line)

            self._current_process.wait()

            if self._stop_event:
                self.log_queue.put("[STOPPED] Process was stopped by user.")
                return False

            if self._current_process.returncode == 0:
                self.log_queue.put(f"[SUCCESS] {os.path.basename(output_path)}")
                self.progress_queue.put(("file_progress", 100))
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
        if "out_time_ms=" in line:
            self.progress_queue.put(("file_progress", 50))
        elif "progress=end" in line:
            self.progress_queue.put(("file_progress", 100))

class VideoProcessorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Sage Dialog Enhancer v5")
        self.file_list = []
        self.processing_thread = None
        self.processor = None
        self.log_queue = queue.Queue()
        self.debug_queue = queue.Queue()
        self.progress_queue = queue.Queue()
        self.config_vars = {
            "supported_extensions": DEFAULT_SUPPORTED_EXTENSIONS.copy(),
            "equalizer_bands": [dict(band) for band in DEFAULT_EQUALIZER_BANDS],
            "speechnorm": dict(DEFAULT_SPEECHNORM),
            "output_folder": DEFAULT_OUTPUT_FOLDER,
            "output_suffix": DEFAULT_OUTPUT_SUFFIX
        }
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

        self.notebook = ttk.Notebook(main_frame)
        self.notebook.pack(fill=tk.BOTH, expand=True, pady=5)

        log_tab = tk.Frame(self.notebook)
        self.log_text = scrolledtext.ScrolledText(log_tab, state=tk.DISABLED, wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.notebook.add(log_tab, text="Log")

        debug_tab = tk.Frame(self.notebook)
        self.debug_text = scrolledtext.ScrolledText(debug_tab, state=tk.DISABLED, wrap=tk.WORD)
        self.debug_text.pack(fill=tk.BOTH, expand=True)
        self.notebook.add(debug_tab, text="Debug")

        # --- SETTINGS TAB ---
        settings_tab = tk.Frame(self.notebook)
        self.notebook.add(settings_tab, text="Settings")

        row_idx = 0
        # SUPPORTED_EXTENSIONS
        ext_label = tk.Label(settings_tab, text="Supported Extensions (comma separated):")
        ext_label.grid(row=row_idx, column=0, sticky=tk.W, pady=(10, 2), padx=8)
        self.extensions_var = tk.StringVar(value=", ".join(self.config_vars["supported_extensions"]))
        ext_entry = tk.Entry(settings_tab, textvariable=self.extensions_var, width=40)
        ext_entry.grid(row=row_idx, column=1, sticky=tk.W, padx=8, pady=(10, 2))
        ToolTip(ext_entry, "File types you want to process (e.g. .mkv, .mp4, .mov).")
        row_idx += 1

        # EQUALIZER FILTERS (each band)
        for i, band in enumerate(self.config_vars["equalizer_bands"]):
            band_label = tk.Label(settings_tab, text=f"Equalizer Band {i+1}:")
            band_label.grid(row=row_idx, column=0, sticky=tk.W, pady=(8 if i == 0 else 2, 2), padx=8)
            row_idx += 1

            # Frequency
            f_var = tk.StringVar(value=band["f"])
            f_entry = tk.Entry(settings_tab, textvariable=f_var, width=10)
            f_entry.grid(row=row_idx, column=1, sticky=tk.W, padx=(8,2))
            ToolTip(f_entry, "Central frequency in Hz for boost/cut (e.g. '50').")
            band["f_var"] = f_var

            tk.Label(settings_tab, text="Frequency (Hz):").grid(row=row_idx, column=0, sticky=tk.E, padx=8)
            row_idx += 1

            # Width type
            t_var = tk.StringVar(value=band["t"])
            t_entry = tk.Entry(settings_tab, textvariable=t_var, width=10)
            t_entry.grid(row=row_idx, column=1, sticky=tk.W, padx=(8,2))
            ToolTip(t_entry, "Bandwidth type: 'q' = Q-Factor, 'h' = Hz, etc.")
            band["t_var"] = t_var

            tk.Label(settings_tab, text="Width Type:").grid(row=row_idx, column=0, sticky=tk.E, padx=8)
            row_idx += 1

            # Width
            w_var = tk.StringVar(value=band["w"])
            w_entry = tk.Entry(settings_tab, textvariable=w_var, width=10)
            w_entry.grid(row=row_idx, column=1, sticky=tk.W, padx=(8,2))
            ToolTip(w_entry, "Bandwidth value (width), based on type above.")
            band["w_var"] = w_var

            tk.Label(settings_tab, text="Width:").grid(row=row_idx, column=0, sticky=tk.E, padx=8)
            row_idx += 1

            # Gain
            g_var = tk.StringVar(value=band["g"])
            g_entry = tk.Entry(settings_tab, textvariable=g_var, width=10)
            g_entry.grid(row=row_idx, column=1, sticky=tk.W, padx=(8,2))
            ToolTip(g_entry, "Gain (dB): positive for boost, negative for cut.")
            band["g_var"] = g_var

            tk.Label(settings_tab, text="Gain (dB):").grid(row=row_idx, column=0, sticky=tk.E, padx=8)
            row_idx += 1

        # SPEECHNORM FILTER
        sn_label = tk.Label(settings_tab, text="Speech Normalizer (speechnorm):")
        sn_label.grid(row=row_idx, column=0, sticky=tk.W, pady=(15,2), padx=8)
        row_idx += 1

        sn = self.config_vars["speechnorm"]

        # Expansion
        e_var = tk.StringVar(value=sn["e"])
        e_entry = tk.Entry(settings_tab, textvariable=e_var, width=10)
        e_entry.grid(row=row_idx, column=1, sticky=tk.W, padx=8)
        ToolTip(e_entry, "Max expansion factor. Higher expands quiet audio more.")
        sn["e_var"] = e_var
        tk.Label(settings_tab, text="Expansion (e):").grid(row=row_idx, column=0, sticky=tk.E, padx=8)
        row_idx += 1

        # Raise
        r_var = tk.StringVar(value=sn["r"])
        r_entry = tk.Entry(settings_tab, textvariable=r_var, width=10)
        r_entry.grid(row=row_idx, column=1, sticky=tk.W, padx=8)
        ToolTip(r_entry, "How quickly expansion factor raises per half-cycle. Very small values avoid distortion.")
        sn["r_var"] = r_var
        tk.Label(settings_tab, text="Raise (r):").grid(row=row_idx, column=0, sticky=tk.E, padx=8)
        row_idx += 1

        # Link channels
        l_var = tk.StringVar(value=sn["l"])
        l_entry = tk.Entry(settings_tab, textvariable=l_var, width=10)
        l_entry.grid(row=row_idx, column=1, sticky=tk.W, padx=8)
        ToolTip(l_entry, "Set to 1 to link channels for gain calculation (recommended for dialog).")
        sn["l_var"] = l_var
        tk.Label(settings_tab, text="Link channels (l):").grid(row=row_idx, column=0, sticky=tk.E, padx=8)
        row_idx += 1

        # OUTPUT_FOLDER
        folder_label = tk.Label(settings_tab, text="Output Folder Name:")
        folder_label.grid(row=row_idx, column=0, sticky=tk.W, pady=2, padx=8)
        self.folder_var = tk.StringVar(value=self.config_vars["output_folder"])
        folder_entry = tk.Entry(settings_tab, textvariable=self.folder_var, width=40)
        folder_entry.grid(row=row_idx, column=1, sticky=tk.W, padx=8, pady=2)
        ToolTip(folder_entry, "Name of subfolder in which processed files will be placed (relative to input file).")
        row_idx += 1

        # OUTPUT_SUFFIX
        suffix_label = tk.Label(settings_tab, text="Output Filename Suffix (include extension):")
        suffix_label.grid(row=row_idx, column=0, sticky=tk.W, pady=2, padx=8)
        self.suffix_var = tk.StringVar(value=self.config_vars["output_suffix"])
        suffix_entry = tk.Entry(settings_tab, textvariable=self.suffix_var, width=40)
        suffix_entry.grid(row=row_idx, column=1, sticky=tk.W, padx=8, pady=2)
        ToolTip(suffix_entry, "Filename ending for processed files (e.g. '_enhanced.mkv').")
        row_idx += 1

        apply_btn = tk.Button(settings_tab, text="Apply Settings", command=self.apply_settings)
        apply_btn.grid(row=row_idx, column=1, sticky=tk.E, pady=(12, 5), padx=8)

    def apply_settings(self):
        ext_str = self.extensions_var.get()
        self.config_vars["supported_extensions"] = [
            e.strip() for e in ext_str.split(",") if e.strip().startswith(".")
        ]
        for band in self.config_vars["equalizer_bands"]:
            band["f"] = band["f_var"].get()
            band["t"] = band["t_var"].get()
            band["w"] = band["w_var"].get()
            band["g"] = band["g_var"].get()
        sn = self.config_vars["speechnorm"]
        sn["e"] = sn["e_var"].get()
        sn["r"] = sn["r_var"].get()
        sn["l"] = sn["l_var"].get()
        self.config_vars["output_folder"] = self.folder_var.get().strip() or DEFAULT_OUTPUT_FOLDER
        self.config_vars["output_suffix"] = self.suffix_var.get().strip() or DEFAULT_OUTPUT_SUFFIX
        messagebox.showinfo("Settings Applied", "Settings have been updated.")

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
            filetypes=[("Video Files", " ".join(self.config_vars["supported_extensions"]))]
        )
        if files:
            self.add_to_list(files)

    def on_drop(self, event):
        files = self.root.tk.splitlist(event.data)
        exts = tuple(self.config_vars["supported_extensions"])
        self.add_to_list([f for f in files if Path(f).suffix.lower() in exts])

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
        self.apply_settings()
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
        self.processor = VideoProcessor(
            self.log_queue, self.debug_queue, self.progress_queue,
            self.config_vars["supported_extensions"],
            [dict(band) for band in self.config_vars["equalizer_bands"]],
            dict(self.config_vars["speechnorm"]),
            self.config_vars["output_folder"],
            self.config_vars["output_suffix"]
        )
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
                output_folder = Path(input_path).parent / self.config_vars["output_folder"]
                output_folder.mkdir(exist_ok=True)
                input_stem = Path(input_path).stem
                output_name = input_stem + self.config_vars["output_suffix"]
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
