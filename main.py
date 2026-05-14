import os
import sys
import subprocess
import threading
import tempfile
import time
import shutil
import ctypes
from pathlib import Path
from typing import Optional, List

import customtkinter as ctk
from tkinter import filedialog, messagebox
import tkinter as tk

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    DND_AVAILABLE = True
except ImportError:
    DND_AVAILABLE = False

try:
    import pygame
    pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=2048)
    PYGAME_AVAILABLE = True
except ImportError:
    PYGAME_AVAILABLE = False

APP_NAME = "NXAudioTool"
APP_VERSION = "1.0.0"
ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("NXAudioTool")

SUPPORTED_EXTENSIONS = {".bfstm", ".bfwav", ".bfsar", ".bfbnk", ".bfseq", ".bwav"}
FORMAT_LABELS = {
    ".bfstm": "BFSTM",
    ".bfwav": "BFWAV",
    ".bfsar": "BFSAR",
    ".bfbnk": "BFBNK",
    ".bfseq": "BFSEQ",
    ".bwav":  "BWAV",
}

CREATE_NO_WINDOW = 0x08000000

def get_bin_dir() -> Path:
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).parent.parent
    return base / "bin"

def get_tool(name: str) -> Optional[Path]:
    for p in [get_bin_dir() / name, Path(name)]:
        if p.exists():
            return p
        found = shutil.which(str(p))
        if found:
            return Path(found)
    return None

VGMSTREAM = get_tool("vgmstream-cli.exe") or get_tool("vgmstream-cli")
FFMPEG = get_tool("ffmpeg.exe") or get_tool("ffmpeg")

COLORS = {
    "bg_dark":       "#0D0D12",
    "bg_mid":        "#13131A",
    "bg_card":       "#1A1A25",
    "bg_sidebar":    "#111118",
    "accent":        "#7C6EE8",
    "accent_hover":  "#9B8FFF",
    "accent_dim":    "#3D3680",
    "text_primary":  "#E8E8F0",
    "text_secondary":"#8888A8",
    "text_dim":      "#4A4A66",
    "border":        "#252535",
    "success":       "#4ECDC4",
    "danger":        "#EF476F",
    "highlight":     "#2A2A40",
    "selected":      "#252545",
}


class AudioFile:
    def __init__(self, path: Path):
        self.path = path
        self.name = path.name
        self.ext = path.suffix.lower()
        self.format_label = FORMAT_LABELS.get(self.ext, self.ext.upper())
        self.duration: Optional[str] = None
        self.duration_secs: float = 0.0
        self.sub_tracks: List[str] = []
        self._probe_done = False

    def probe_async(self, callback=None):
        def _probe():
            self.probe()
            if callback:
                callback(self)
        threading.Thread(target=_probe, daemon=True).start()

    def probe(self):
        if self._probe_done or VGMSTREAM is None:
            return
        try:
            result = subprocess.run(
                [str(VGMSTREAM), "-m", str(self.path)],
                capture_output=True, text=True, timeout=10,
                creationflags=CREATE_NO_WINDOW
            )
            out = result.stdout + result.stderr
            for line in out.splitlines():
                line = line.strip()
                if "stream count:" in line.lower():
                    try:
                        count = int(line.split(":")[-1].strip())
                        self.sub_tracks = [f"Track {i+1}" for i in range(count)]
                    except ValueError:
                        pass
                if "samples at" in line:
                    parts = line.split("samples at")
                    if len(parts) == 2:
                        try:
                            samples = int(parts[0].split()[-1].replace(",", ""))
                            rate = int(parts[1].strip().split()[0].replace(",", ""))
                            secs = samples / rate
                            self.duration_secs = secs
                            self.duration = f"{int(secs//60)}:{int(secs%60):02d}"
                        except Exception:
                            pass
        except Exception:
            pass
        self._probe_done = True

    def decode_to_wav(self, output_path: Path, sub_track: int = 0) -> bool:
        if VGMSTREAM is None:
            return False
        try:
            cmd = [str(VGMSTREAM), "-o", str(output_path), "-s", str(sub_track + 1), str(self.path)]
            result = subprocess.run(cmd, capture_output=True, timeout=60, creationflags=CREATE_NO_WINDOW)
            return output_path.exists() and result.returncode == 0
        except Exception:
            return False


class PlayerEngine:
    def __init__(self):
        self.current_file: Optional[AudioFile] = None
        self.temp_dir = Path(tempfile.mkdtemp(prefix="nxaudio_"))
        self._wav_counter = 0
        self.state = "stopped"
        self.position_secs: float = 0.0
        self.duration_secs: float = 0.0
        self.volume: float = 0.8
        self._stop_pos_thread = threading.Event()
        self.on_position_update = None
        self.on_state_change = None
        self.on_finished = None

    def _new_wav_path(self) -> Path:
        self._wav_counter += 1
        return self.temp_dir / f"preview_{self._wav_counter}.wav"

    def _release(self):
        if PYGAME_AVAILABLE:
            try:
                pygame.mixer.music.stop()
                pygame.mixer.music.unload()
            except Exception:
                pass
        time.sleep(0.15)

    def load(self, audio_file: AudioFile, sub_track: int = 0) -> bool:
        self._stop_pos_thread.set()
        self._release()
        self.state = "stopped"
        self.position_secs = 0.0
        wav_path = self._new_wav_path()
        if not audio_file.decode_to_wav(wav_path, sub_track):
            return False
        self.current_file = audio_file
        self.duration_secs = audio_file.duration_secs
        if PYGAME_AVAILABLE:
            try:
                pygame.mixer.music.load(str(wav_path))
                pygame.mixer.music.set_volume(self.volume)
                if self.duration_secs == 0:
                    snd = pygame.mixer.Sound(str(wav_path))
                    self.duration_secs = snd.get_length()
                    del snd
                return True
            except Exception:
                return False
        return False

    def play(self):
        if not PYGAME_AVAILABLE:
            return
        if self.state == "paused":
            pygame.mixer.music.unpause()
        else:
            pygame.mixer.music.play()
            self.position_secs = 0.0
        self.state = "playing"
        self._stop_pos_thread.clear()
        threading.Thread(target=self._track_position, daemon=True).start()
        if self.on_state_change:
            self.on_state_change("playing")

    def pause(self):
        if not PYGAME_AVAILABLE or self.state != "playing":
            return
        pygame.mixer.music.pause()
        self.state = "paused"
        self._stop_pos_thread.set()
        if self.on_state_change:
            self.on_state_change("paused")

    def stop(self):
        self._stop_pos_thread.set()
        if PYGAME_AVAILABLE:
            try:
                pygame.mixer.music.stop()
            except Exception:
                pass
        self.state = "stopped"
        self.position_secs = 0.0
        if self.on_state_change:
            self.on_state_change("stopped")
        if self.on_position_update:
            self.on_position_update(0, self.duration_secs)

    def seek(self, seconds: float):
        if not PYGAME_AVAILABLE or self.state == "stopped":
            return
        try:
            pygame.mixer.music.set_pos(seconds)
            self.position_secs = seconds
        except Exception:
            pass

    def set_volume(self, vol: float):
        self.volume = max(0.0, min(1.0, vol))
        if PYGAME_AVAILABLE:
            try:
                pygame.mixer.music.set_volume(self.volume)
            except Exception:
                pass

    def _track_position(self):
        while not self._stop_pos_thread.is_set():
            if self.state == "playing":
                if not pygame.mixer.music.get_busy():
                    self.state = "stopped"
                    self.position_secs = 0.0
                    if self.on_finished:
                        self.on_finished()
                    if self.on_state_change:
                        self.on_state_change("stopped")
                    break
                try:
                    pos_ms = pygame.mixer.music.get_pos()
                    if pos_ms >= 0:
                        self.position_secs = pos_ms / 1000.0
                except Exception:
                    pass
                if self.on_position_update:
                    self.on_position_update(self.position_secs, self.duration_secs)
            time.sleep(0.1)

    def cleanup(self):
        self._stop_pos_thread.set()
        if PYGAME_AVAILABLE:
            try:
                pygame.mixer.music.stop()
                pygame.mixer.music.unload()
            except Exception:
                pass
        time.sleep(0.2)
        shutil.rmtree(self.temp_dir, ignore_errors=True)


class Converter:
    def __init__(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="nxconv_"))

    def convert(self, audio_file: AudioFile, output_dir: Path, target_format: str,
                sub_track: int = 0, progress_callback=None) -> bool:
        if VGMSTREAM is None or FFMPEG is None:
            return False
        wav_path = self.temp_dir / f"{audio_file.path.stem}_{sub_track}.wav"
        if progress_callback:
            progress_callback(0.3, "Decoding...")
        if not audio_file.decode_to_wav(wav_path, sub_track):
            return False
        if progress_callback:
            progress_callback(0.6, "Converting...")
        ext = "mp3" if target_format == "MP3" else "wav"
        out_name = audio_file.path.stem + (f"_track{sub_track+1}" if sub_track > 0 else "")
        output_path = output_dir / f"{out_name}.{ext}"
        cmd = [str(FFMPEG), "-y", "-i", str(wav_path)]
        cmd += ["-codec:a", "libmp3lame", "-qscale:a", "2"] if target_format == "MP3" else ["-codec:a", "pcm_s16le"]
        cmd.append(str(output_path))
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=120, creationflags=CREATE_NO_WINDOW)
            success = result.returncode == 0 and output_path.exists()
        except Exception:
            success = False
        try:
            wav_path.unlink()
        except Exception:
            pass
        if progress_callback:
            progress_callback(1.0, "Done!")
        return success

    def batch_convert(self, files, output_dir, target_format, progress_callback=None, done_callback=None):
        def _run():
            total = len(files)
            for i, f in enumerate(files):
                def sub_cb(p, msg, _i=i, _total=total):
                    if progress_callback:
                        progress_callback((_i + p) / _total, f"[{_i+1}/{_total}] {f.name} – {msg}")
                self.convert(f, output_dir, target_format, 0, sub_cb)
            if done_callback:
                done_callback()
        threading.Thread(target=_run, daemon=True).start()

    def cleanup(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)


class FileListItem(ctk.CTkFrame):
    def __init__(self, parent, audio_file: AudioFile, index: int, on_select=None, on_remove=None, **kwargs):
        super().__init__(parent, fg_color=COLORS["bg_card"], corner_radius=8,
                         border_width=1, border_color=COLORS["border"], **kwargs)
        self.audio_file = audio_file
        self.index = index
        self.on_select = on_select
        self.on_remove = on_remove
        self.selected = False
        self._build()

    def _build(self):
        self.grid_columnconfigure(1, weight=1)
        badge = ctk.CTkLabel(self, text=self.audio_file.format_label,
                             fg_color=COLORS["accent_dim"], text_color=COLORS["accent"],
                             corner_radius=4, font=ctk.CTkFont("Consolas", 10, "bold"), width=52, height=22)
        badge.grid(row=0, column=0, padx=(10, 8), pady=10)
        name_lbl = ctk.CTkLabel(self, text=self.audio_file.name,
                                font=ctk.CTkFont("Segoe UI", 12),
                                text_color=COLORS["text_primary"], anchor="w")
        name_lbl.grid(row=0, column=1, sticky="ew", padx=4)
        self.dur_lbl = ctk.CTkLabel(self, text=self.audio_file.duration or "—",
                                    font=ctk.CTkFont("Consolas", 11),
                                    text_color=COLORS["text_secondary"], width=50)
        self.dur_lbl.grid(row=0, column=2, padx=8)
        remove_btn = ctk.CTkButton(self, text="✕", width=28, height=28,
                                   fg_color="transparent", text_color=COLORS["text_dim"],
                                   hover_color=COLORS["danger"],
                                   font=ctk.CTkFont("Segoe UI", 11), command=self._on_remove)
        remove_btn.grid(row=0, column=3, padx=(0, 8))
        for w in [self, badge, name_lbl, self.dur_lbl]:
            w.bind("<Button-1>", self._on_click)
            w.bind("<Double-Button-1>", self._on_double_click)

    def _on_click(self, _=None):
        if self.on_select:
            self.on_select(self.index)

    def _on_double_click(self, _=None):
        if self.on_select:
            self.on_select(self.index, play=True)

    def _on_remove(self):
        if self.on_remove:
            self.on_remove(self.index)

    def set_selected(self, selected: bool):
        self.selected = selected
        self.configure(
            fg_color=COLORS["selected"] if selected else COLORS["bg_card"],
            border_color=COLORS["accent_dim"] if selected else COLORS["border"]
        )

    def update_duration(self, audio_file: AudioFile):
        if audio_file.duration:
            self.dur_lbl.configure(text=audio_file.duration)


class ConvertDialog(ctk.CTkToplevel):
    def __init__(self, parent, files: List[AudioFile], converter: Converter):
        super().__init__(parent)
        self.files = files
        self.converter = converter
        self.title("Convert")
        self.geometry("480x460")
        self.resizable(False, False)
        self.configure(fg_color=COLORS["bg_dark"])
        self.grab_set()
        self._build()

    def _build(self):
        ctk.CTkLabel(self, text="Convert", font=ctk.CTkFont("Segoe UI", 18, "bold"),
                     text_color=COLORS["text_primary"]).pack(padx=24, pady=(24, 4), anchor="w")
        ctk.CTkLabel(self, text=f"{len(self.files)} file(s) selected",
                     font=ctk.CTkFont("Segoe UI", 11),
                     text_color=COLORS["text_secondary"]).pack(padx=24, anchor="w", pady=(0, 16))

        ctk.CTkLabel(self, text="Mode", font=ctk.CTkFont("Segoe UI", 11, "bold"),
                     text_color=COLORS["text_dim"]).pack(padx=24, anchor="w")
        self.mode_var = ctk.StringVar(value="single" if len(self.files) == 1 else "all")
        for text, val in [("Selected file", "single"), ("All files", "all")]:
            ctk.CTkRadioButton(self, text=text, variable=self.mode_var, value=val,
                               font=ctk.CTkFont("Segoe UI", 12), text_color=COLORS["text_secondary"],
                               fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"]
                               ).pack(padx=32, anchor="w", pady=2)

        ctk.CTkLabel(self, text="Target format", font=ctk.CTkFont("Segoe UI", 11, "bold"),
                     text_color=COLORS["text_dim"]).pack(padx=24, anchor="w", pady=(12, 4))
        self.fmt_var = ctk.StringVar(value="MP3")
        fmt_frame = ctk.CTkFrame(self, fg_color="transparent")
        fmt_frame.pack(padx=32, anchor="w")
        for fmt in ["MP3", "WAV"]:
            ctk.CTkRadioButton(fmt_frame, text=fmt, variable=self.fmt_var, value=fmt,
                               font=ctk.CTkFont("Segoe UI", 12), text_color=COLORS["text_secondary"],
                               fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"]
                               ).pack(side="left", padx=(0, 16))

        ctk.CTkLabel(self, text="Output folder", font=ctk.CTkFont("Segoe UI", 11, "bold"),
                     text_color=COLORS["text_dim"]).pack(padx=24, anchor="w", pady=(12, 4))
        dir_frame = ctk.CTkFrame(self, fg_color="transparent")
        dir_frame.pack(padx=24, fill="x")
        dir_frame.grid_columnconfigure(0, weight=1)
        self.outdir_var = ctk.StringVar(value=str(Path.home() / "Music" / "NXAudioTool"))
        ctk.CTkEntry(dir_frame, textvariable=self.outdir_var, font=ctk.CTkFont("Segoe UI", 11),
                     fg_color=COLORS["bg_card"], border_color=COLORS["border"],
                     text_color=COLORS["text_secondary"], height=32
                     ).grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ctk.CTkButton(dir_frame, text="...", width=36, height=32, fg_color=COLORS["accent_dim"],
                      hover_color=COLORS["accent"], text_color=COLORS["text_primary"],
                      command=self._pick_dir).grid(row=0, column=1)

        self.progress_label = ctk.CTkLabel(self, text="", font=ctk.CTkFont("Segoe UI", 10),
                                           text_color=COLORS["text_secondary"])
        self.progress_label.pack(padx=24, anchor="w", pady=(16, 4))
        self.progress_bar = ctk.CTkProgressBar(self, fg_color=COLORS["border"],
                                               progress_color=COLORS["accent"], height=8, corner_radius=4)
        self.progress_bar.pack(padx=24, fill="x")
        self.progress_bar.set(0)

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=24, pady=20)
        self.start_btn = ctk.CTkButton(btn_frame, text="▶  Start Convert",
                                       fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
                                       text_color=COLORS["text_primary"],
                                       font=ctk.CTkFont("Segoe UI", 13, "bold"),
                                       height=40, command=self._start)
        self.start_btn.pack(side="right", padx=(8, 0))
        ctk.CTkButton(btn_frame, text="Cancel", fg_color=COLORS["bg_card"],
                      hover_color=COLORS["highlight"], text_color=COLORS["text_secondary"],
                      border_width=1, border_color=COLORS["border"],
                      font=ctk.CTkFont("Segoe UI", 13), height=40, command=self.destroy).pack(side="right")

    def _pick_dir(self):
        d = filedialog.askdirectory(title="Choose output folder")
        if d:
            self.outdir_var.set(d)

    def _start(self):
        out = Path(self.outdir_var.get())
        out.mkdir(parents=True, exist_ok=True)
        files = self.files if self.mode_var.get() == "all" else self.files[:1]
        self.start_btn.configure(state="disabled", text="Converting...")

        def progress_cb(pct, msg):
            self.progress_bar.set(pct)
            self.progress_label.configure(text=msg)

        def done_cb():
            self.progress_label.configure(text="✓ Done!")
            messagebox.showinfo("Conversion complete", f"{len(files)} file(s) saved to\n{out}", parent=self)
            self.after(1500, self.destroy)

        self.converter.batch_convert(files, out, self.fmt_var.get(), progress_cb, done_cb)


class NXAudioApp:
    def __init__(self):
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        if DND_AVAILABLE:
            self.root = TkinterDnD.Tk()
        else:
            self.root = ctk.CTk()

        self.root.title(APP_NAME)
        self.root.geometry("960x680")
        self.root.minsize(720, 520)
        self.root.configure(bg=COLORS["bg_dark"])

        try:
            icon = Path(__file__).parent.parent / "assets" / "icon.ico"
            if icon.exists():
                self.root.iconbitmap(str(icon))
        except Exception:
            pass

        self.files: List[AudioFile] = []
        self.selected_index: int = -1
        self.engine = PlayerEngine()
        self.converter = Converter()
        self._loading = False

        self.engine.on_state_change = lambda s: self.root.after(0, lambda: self._on_state_change(s))
        self.engine.on_position_update = lambda p, d: self.root.after(0, lambda: self._on_position_update(p, d))
        self.engine.on_finished = lambda: self.root.after(0, self._on_track_finished)

        self._build_ui()
        self._setup_dnd()
        self._check_missing_tools()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        # ── Root: sidebar left, main right ──
        self.root.grid_columnconfigure(1, weight=1)
        self.root.grid_rowconfigure(0, weight=1)

        # Sidebar
        sidebar = ctk.CTkFrame(self.root, fg_color=COLORS["bg_sidebar"], corner_radius=0, width=200)
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)
        sidebar.grid_rowconfigure(10, weight=1)
        self._build_sidebar(sidebar)

        # Main area (top bar + list + controls stacked with pack)
        main = ctk.CTkFrame(self.root, fg_color=COLORS["bg_dark"], corner_radius=0)
        main.grid(row=0, column=1, sticky="nsew")
        self._build_main(main)

    def _build_sidebar(self, sidebar):
        logo = ctk.CTkFrame(sidebar, fg_color="transparent")
        logo.grid(row=0, column=0, padx=16, pady=(24, 4), sticky="ew")
        ctk.CTkLabel(logo, text="NX", font=ctk.CTkFont("Consolas", 28, "bold"),
                     text_color=COLORS["accent"]).pack(side="left")
        ctk.CTkLabel(logo, text="Audio", font=ctk.CTkFont("Segoe UI", 18),
                     text_color=COLORS["text_primary"]).pack(side="left", padx=(2, 0), pady=(4, 0))
        ctk.CTkLabel(sidebar, text="v" + APP_VERSION, font=ctk.CTkFont("Segoe UI", 9),
                     text_color=COLORS["text_dim"]).grid(row=1, column=0, padx=16, sticky="w", pady=(0, 12))
        ctk.CTkFrame(sidebar, height=1, fg_color=COLORS["border"]).grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 12))

        ctk.CTkLabel(sidebar, text="FILES", font=ctk.CTkFont("Segoe UI", 9, "bold"),
                     text_color=COLORS["text_dim"]).grid(row=3, column=0, padx=16, sticky="w", pady=(0, 6))
        for i, (label, cmd) in enumerate([
            ("  +  Add File",   self._add_file),
            ("  +  Add Folder", self._add_folder),
            ("  ✕  Clear List", self._clear_list),
        ]):
            ctk.CTkButton(sidebar, text=label, font=ctk.CTkFont("Segoe UI", 12),
                          fg_color="transparent", text_color=COLORS["text_secondary"],
                          hover_color=COLORS["highlight"], anchor="w", height=34, corner_radius=6,
                          command=cmd).grid(row=4+i, column=0, padx=10, sticky="ew", pady=1)

        ctk.CTkFrame(sidebar, height=1, fg_color=COLORS["border"]).grid(row=7, column=0, sticky="ew", padx=12, pady=10)
        ctk.CTkLabel(sidebar, text="CONVERSION", font=ctk.CTkFont("Segoe UI", 9, "bold"),
                     text_color=COLORS["text_dim"]).grid(row=8, column=0, padx=16, sticky="w", pady=(0, 6))
        ctk.CTkButton(sidebar, text="  ⇄  Convert", font=ctk.CTkFont("Segoe UI", 12, "bold"),
                      fg_color=COLORS["accent"], text_color=COLORS["text_primary"],
                      hover_color=COLORS["accent_hover"], anchor="w", height=36, corner_radius=6,
                      command=self._open_convert).grid(row=9, column=0, padx=10, sticky="ew", pady=1)

        # Tools status
        tools_frame = ctk.CTkFrame(sidebar, fg_color=COLORS["bg_card"], corner_radius=8)
        tools_frame.grid(row=11, column=0, padx=10, pady=16, sticky="sew")
        ctk.CTkLabel(tools_frame, text="TOOLS", font=ctk.CTkFont("Segoe UI", 10, "bold"),
                     text_color=COLORS["text_dim"]).pack(anchor="w", padx=10, pady=(8, 4))
        for name, path in [("vgmstream", VGMSTREAM), ("ffmpeg", FFMPEG)]:
            row = ctk.CTkFrame(tools_frame, fg_color="transparent")
            row.pack(fill="x", padx=10, pady=2)
            ctk.CTkLabel(row, text="●", font=ctk.CTkFont("Segoe UI", 10),
                         text_color=COLORS["success"] if path else COLORS["danger"]).pack(side="left")
            ctk.CTkLabel(row, text=f" {name}", font=ctk.CTkFont("Consolas", 10),
                         text_color=COLORS["text_secondary"]).pack(side="left")
        ctk.CTkFrame(tools_frame, height=8, fg_color="transparent").pack()

    def _build_main(self, main):
        # Top bar
        topbar = ctk.CTkFrame(main, fg_color=COLORS["bg_mid"], height=48, corner_radius=0)
        topbar.pack(side="top", fill="x")
        topbar.pack_propagate(False)
        self.file_count_lbl = ctk.CTkLabel(topbar, text="0 files loaded",
                                           font=ctk.CTkFont("Segoe UI", 11),
                                           text_color=COLORS["text_secondary"])
        self.file_count_lbl.pack(side="left", padx=16)
        self.search_var = ctk.StringVar()
        self.search_var.trace_add("write", self._on_search)
        ctk.CTkEntry(topbar, placeholder_text="🔍  Search...", textvariable=self.search_var,
                     font=ctk.CTkFont("Segoe UI", 11), fg_color=COLORS["bg_card"],
                     border_color=COLORS["border"], text_color=COLORS["text_primary"],
                     placeholder_text_color=COLORS["text_dim"], width=200, height=30
                     ).pack(side="right", padx=16, pady=9)

        # Player controls at bottom
        controls = ctk.CTkFrame(main, fg_color=COLORS["bg_mid"], corner_radius=0)
        controls.pack(side="bottom", fill="x")
        self._build_controls(controls)

        # File list in the middle (takes remaining space)
        list_frame = ctk.CTkFrame(main, fg_color=COLORS["bg_dark"], corner_radius=0)
        list_frame.pack(side="top", fill="both", expand=True)

        # Scrollable list with mouse wheel support
        self.list_canvas = tk.Canvas(list_frame, bg=COLORS["bg_dark"], highlightthickness=0, bd=0)
        scrollbar = ctk.CTkScrollbar(list_frame, command=self.list_canvas.yview,
                                     button_color=COLORS["border"],
                                     button_hover_color=COLORS["accent_dim"])
        self.list_canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.list_canvas.pack(side="left", fill="both", expand=True)

        self.list_inner = ctk.CTkFrame(self.list_canvas, fg_color=COLORS["bg_dark"], corner_radius=0)
        self.list_inner.grid_columnconfigure(0, weight=1)
        self._canvas_window = self.list_canvas.create_window((0, 0), window=self.list_inner, anchor="nw")

        self.list_inner.bind("<Configure>", self._on_list_configure)
        self.list_canvas.bind("<Configure>", self._on_canvas_configure)
        self.list_canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.list_inner.bind("<MouseWheel>", self._on_mousewheel)

        self.empty_lbl = ctk.CTkLabel(self.list_inner,
                                      text="Drag files here\nor use the sidebar to add files",
                                      font=ctk.CTkFont("Segoe UI", 14),
                                      text_color=COLORS["text_dim"])
        self.empty_lbl.grid(row=0, column=0, pady=80)

    def _on_list_configure(self, _=None):
        self.list_canvas.configure(scrollregion=self.list_canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self.list_canvas.itemconfig(self._canvas_window, width=event.width)

    def _on_mousewheel(self, event):
        self.list_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _build_controls(self, parent):
        # Track name + format
        info = ctk.CTkFrame(parent, fg_color="transparent")
        info.pack(fill="x", padx=20, pady=(12, 4))
        self.track_name_lbl = ctk.CTkLabel(info, text="No file loaded",
                                           font=ctk.CTkFont("Segoe UI", 13, "bold"),
                                           text_color=COLORS["text_primary"], anchor="w")
        self.track_name_lbl.pack(side="left")
        self.track_format_lbl = ctk.CTkLabel(info, text="",
                                             font=ctk.CTkFont("Consolas", 10),
                                             text_color=COLORS["accent"], anchor="e")
        self.track_format_lbl.pack(side="right")

        # Progress bar row
        prog_row = ctk.CTkFrame(parent, fg_color="transparent")
        prog_row.pack(fill="x", padx=20, pady=2)
        self.pos_lbl = ctk.CTkLabel(prog_row, text="0:00", font=ctk.CTkFont("Consolas", 10),
                                    text_color=COLORS["text_secondary"], width=36)
        self.pos_lbl.pack(side="left")
        self.progress_slider = ctk.CTkSlider(prog_row, from_=0, to=100, number_of_steps=1000,
                                             progress_color=COLORS["accent"], fg_color=COLORS["border"],
                                             button_color=COLORS["accent"],
                                             button_hover_color=COLORS["accent_hover"],
                                             height=14, command=self._on_seek)
        self.progress_slider.pack(side="left", fill="x", expand=True, padx=8)
        self.progress_slider.set(0)
        self.dur_lbl = ctk.CTkLabel(prog_row, text="0:00", font=ctk.CTkFont("Consolas", 10),
                                    text_color=COLORS["text_secondary"], width=36)
        self.dur_lbl.pack(side="right")

        # Buttons row
        btn_row = ctk.CTkFrame(parent, fg_color="transparent")
        btn_row.pack(fill="x", padx=20, pady=(4, 12))

        btn_style = dict(width=40, height=40, corner_radius=20, fg_color=COLORS["bg_card"],
                         hover_color=COLORS["highlight"], border_width=1, border_color=COLORS["border"])

        ctk.CTkButton(btn_row, text="⏮", font=ctk.CTkFont("Segoe UI", 14),
                      text_color=COLORS["text_secondary"], command=self._play_prev, **btn_style
                      ).pack(side="left", padx=3)

        self.play_btn = ctk.CTkButton(btn_row, text="▶", font=ctk.CTkFont("Segoe UI", 14),
                                      text_color=COLORS["text_primary"],
                                      fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
                                      width=52, height=52, corner_radius=26,
                                      command=self._play_pause)
        self.play_btn.pack(side="left", padx=3)

        ctk.CTkButton(btn_row, text="⏭", font=ctk.CTkFont("Segoe UI", 14),
                      text_color=COLORS["text_secondary"], command=self._play_next, **btn_style
                      ).pack(side="left", padx=3)

        self.auto_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(btn_row, text="Auto-next", variable=self.auto_var,
                        font=ctk.CTkFont("Segoe UI", 11), text_color=COLORS["text_secondary"],
                        fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
                        checkmark_color=COLORS["text_primary"], width=20
                        ).pack(side="left", padx=14)

        # Volume
        vol = ctk.CTkFrame(btn_row, fg_color="transparent")
        vol.pack(side="right")
        ctk.CTkLabel(vol, text="🔊", font=ctk.CTkFont("Segoe UI", 13),
                     text_color=COLORS["text_secondary"]).pack(side="left", padx=(0, 4))
        self.vol_slider = ctk.CTkSlider(vol, from_=0, to=1, number_of_steps=100, width=100,
                                        progress_color=COLORS["accent_dim"], fg_color=COLORS["border"],
                                        button_color=COLORS["text_secondary"],
                                        button_hover_color=COLORS["accent"],
                                        height=12, command=lambda v: self.engine.set_volume(v))
        self.vol_slider.set(0.8)
        self.vol_slider.pack(side="left")

    def _setup_dnd(self):
        if not DND_AVAILABLE:
            return
        self.root.drop_target_register(DND_FILES)
        self.root.dnd_bind("<<Drop>>", self._on_drop)

    def _on_drop(self, event):
        import re
        raw = event.data
        paths = re.findall(r'\{([^}]+)\}', raw) if raw.startswith("{") else raw.split()
        for p in paths:
            path = Path(p)
            if path.is_dir():
                self._add_folder_path(path)
            elif path.suffix.lower() in SUPPORTED_EXTENSIONS:
                self._add_file_path(path)

    def _on_state_change(self, state: str):
        if state == "playing":
            self.play_btn.configure(text="⏸")
        else:
            self.play_btn.configure(text="▶")

    def _on_position_update(self, pos: float, dur: float):
        self.progress_slider.set((pos / dur) * 100 if dur > 0 else 0)
        def fmt(s): return f"{int(s//60)}:{int(s%60):02d}"
        self.pos_lbl.configure(text=fmt(pos))
        self.dur_lbl.configure(text=fmt(dur) if dur > 0 else "—")

    def _on_track_finished(self):
        if self.auto_var.get():
            self._play_next()

    def _on_seek(self, val):
        if self.engine.duration_secs > 0:
            self.engine.seek((val / 100) * self.engine.duration_secs)

    def _add_file(self):
        paths = filedialog.askopenfilenames(
            title="Open audio files",
            filetypes=[("Nintendo Audio", "*.bfstm *.bfwav *.bfsar *.bfbnk *.bfseq *.bwav"),
                       ("All files", "*.*")])
        for p in paths:
            self._add_file_path(Path(p))

    def _add_folder(self):
        folder = filedialog.askdirectory(title="Add folder")
        if folder:
            self._add_folder_path(Path(folder))

    def _add_folder_path(self, folder: Path):
        found = []
        for ext in SUPPORTED_EXTENSIONS:
            found.extend(folder.rglob(f"*{ext}"))
        for p in sorted(found):
            self._add_file_path(p)

    def _add_file_path(self, path: Path):
        if any(f.path == path for f in self.files):
            return
        af = AudioFile(path)
        self.files.append(af)
        idx = len(self.files) - 1
        self._insert_list_item(af, idx)
        self._update_file_count()
        af.probe_async(callback=lambda a: self.root.after(0, lambda: self._update_duration_in_ui(a)))

    def _insert_list_item(self, af: AudioFile, idx: int):
        if self.empty_lbl.winfo_ismapped():
            self.empty_lbl.grid_forget()
        item = FileListItem(self.list_inner, af, idx,
                            on_select=self._select_file, on_remove=self._remove_file)
        item.grid(row=idx, column=0, sticky="ew", padx=8, pady=3)
        item.bind("<MouseWheel>", self._on_mousewheel)
        for child in item.winfo_children():
            child.bind("<MouseWheel>", self._on_mousewheel)
        self._list_items = getattr(self, "_list_items", [])
        self._list_items.append(item)

    def _update_duration_in_ui(self, af: AudioFile):
        for item in getattr(self, "_list_items", []):
            if item.audio_file is af:
                item.update_duration(af)
                break

    def _remove_file(self, idx: int):
        if 0 <= idx < len(self.files):
            self.files.pop(idx)
            self._rebuild_list()
            if self.selected_index == idx:
                self.selected_index = -1
                self.engine.stop()

    def _clear_list(self):
        self.files.clear()
        self.selected_index = -1
        self.engine.stop()
        self._rebuild_list()

    def _rebuild_list(self):
        self._list_items = getattr(self, "_list_items", [])
        for item in self._list_items:
            item.destroy()
        self._list_items = []
        if not self.files:
            self.empty_lbl.grid(row=0, column=0, pady=80)
        else:
            self.empty_lbl.grid_forget()
        for i, af in enumerate(self.files):
            item = FileListItem(self.list_inner, af, i,
                                on_select=self._select_file, on_remove=self._remove_file)
            item.grid(row=i, column=0, sticky="ew", padx=8, pady=3)
            item.bind("<MouseWheel>", self._on_mousewheel)
            for child in item.winfo_children():
                child.bind("<MouseWheel>", self._on_mousewheel)
            self._list_items.append(item)
        self._update_file_count()

    def _update_file_count(self):
        n = len(self.files)
        self.file_count_lbl.configure(text=f"{n} file{'s' if n != 1 else ''} loaded")

    def _on_search(self, *_):
        query = self.search_var.get().lower()
        for item in getattr(self, "_list_items", []):
            if query in item.audio_file.name.lower():
                item.grid()
            else:
                item.grid_remove()

    def _select_file(self, idx: int, play: bool = False):
        items = getattr(self, "_list_items", [])
        if 0 <= self.selected_index < len(items):
            items[self.selected_index].set_selected(False)
        self.selected_index = idx
        if 0 <= idx < len(items):
            items[idx].set_selected(True)
        if 0 <= idx < len(self.files):
            af = self.files[idx]
            self.track_name_lbl.configure(text=af.name)
            self.track_format_lbl.configure(text=af.format_label)
            if play:
                self._load_and_play(idx)

    def _load_and_play(self, idx: int, sub_track: int = 0):
        if idx < 0 or idx >= len(self.files) or self._loading:
            return
        af = self.files[idx]
        self._loading = True
        self.play_btn.configure(text="…")

        def _do():
            if not PYGAME_AVAILABLE:
                messagebox.showerror("pygame missing", "pygame is not installed.\npip install pygame")
                self._loading = False
                return
            if VGMSTREAM is None:
                messagebox.showerror("vgmstream missing", "vgmstream-cli.exe not found in bin/")
                self._loading = False
                return
            ok = self.engine.load(af, sub_track)
            self._loading = False
            if ok:
                self.engine.play()
            else:
                self.root.after(0, lambda: self.play_btn.configure(text="▶"))
                messagebox.showerror("Load error", f"Could not decode '{af.name}'.")

        threading.Thread(target=_do, daemon=True).start()

    def _play_pause(self):
        if self._loading:
            return
        if self.engine.state == "playing":
            self.engine.pause()
        elif self.engine.state == "paused":
            self.engine.play()
        else:
            if self.selected_index >= 0:
                self._load_and_play(self.selected_index)
            elif self.files:
                self._select_file(0, play=True)

    def _stop(self):
        self.engine.stop()

    def _play_prev(self):
        if not self.files or self._loading:
            return
        self._select_file(max(0, self.selected_index - 1), play=True)

    def _play_next(self):
        if not self.files or self._loading:
            return
        self._select_file(min(len(self.files) - 1, self.selected_index + 1), play=True)

    def _open_convert(self):
        if not self.files:
            messagebox.showinfo("No files", "Please load files first.")
            return
        sel = self.files[self.selected_index:self.selected_index+1] if self.selected_index >= 0 else []
        ConvertDialog(self.root, sel if sel else self.files, self.converter)

    def _check_missing_tools(self):
        missing = [n for n, p in [("vgmstream-cli.exe", VGMSTREAM), ("ffmpeg.exe", FFMPEG)] if p is None]
        if missing:
            self.root.after(500, lambda: messagebox.showwarning(
                "Missing tools",
                f"Not found: {', '.join(missing)}\n\nPlace them in the bin/ folder."))

    def _on_close(self):
        self.engine.cleanup()
        self.converter.cleanup()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = NXAudioApp()
    app.run()