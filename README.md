# Digitalinos — Video Batch Pro

A fully-offline, open-source desktop batch video processor — like a simplified Wondershare UniConverter — written in Python + PyQt5.

- **Batch video upload** (drag-and-drop or file chooser) with per-file duration / resolution / size.
- **Watermarking**: image logo *or* text overlay with drop-shadow, custom font file, opacity / scale / size / position controls. Image watermarks are now **frame-stable** (no bounce).
- **Quality templates** — *Original*, *YouTube 1080p Clean*, *CapCut Ultra HD (1440p)*, *4K Crisp (2160p)*, *Fast Preview*. Applies Lanczos scale + `unsharp` sharpening for clean, non-blurry output.
- **AI upscaling** via **Real-ESRGAN** (ncnn-vulkan, local). Falls back to FFmpeg Lanczos if Real-ESRGAN isn't installed.
- **Sequential export naming** (`01_ab12cd.mp4`, `02_ef34gh.mp4`, …) — ideal for bulk uploads.
- **Batch engine** using FFmpeg with per-file + overall progress bars and cancellation.
- **ZIP export** of all processed videos.
- **Presets** — save / load your favourite settings as JSON.
- **In-app Help tab** with full step-by-step usage tips.
- **1-click launcher** on Windows (`launch.bat`) — no terminal required.
- **Offline-only**: no network calls, no telemetry.

Primary target: **Windows**. Also runs on macOS and Linux.

---

## Quick Start (Windows — 1-click)

1. Install **Python 3.11** from <https://www.python.org/downloads/windows/> — tick *"Add python.exe to PATH"*.
2. Install **FFmpeg** (see [FFmpeg setup](#ffmpeg-required) below).
3. Download the repo (Code → Download ZIP) or `git clone https://github.com/munnataiwan123-gif/video-batch-pro.git`.
4. **Double-click `launch.bat`**. First run creates the virtual env and installs dependencies automatically (~30s). Subsequent runs launch instantly.

That's it — no terminal, no commands.

On macOS/Linux use `./launch.sh` instead.

---

## Updating to a new version

```powershell
cd $HOME\video-batch-pro
git pull
.\launch.bat
```

`launch.bat` automatically re-runs `pip install -r requirements.txt` if it detects missing dependencies.

If you prefer not to use `launch.bat`:

```powershell
cd $HOME\video-batch-pro
git pull
.venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

---

## Advanced / Manual installation

You need **Python 3.9+**.

```bash
git clone https://github.com/munnataiwan123-gif/video-batch-pro.git
cd video-batch-pro

python -m venv .venv
# Windows:   .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate

pip install -r requirements.txt
python run.py
```

### FFmpeg (required)

Digitalinos shells out to `ffmpeg` and `ffprobe`. Both must be on `PATH`.

**Windows**

1. Download a static build from <https://www.gyan.dev/ffmpeg/builds/> (*release essentials* is fine) or <https://github.com/BtbN/FFmpeg-Builds/releases>.
2. Extract to `C:\ffmpeg`. Confirm `C:\ffmpeg\bin\ffmpeg.exe` exists.
3. Add `C:\ffmpeg\bin` to your **System PATH**: *Start ▶ "Edit the system environment variables" ▶ Environment Variables ▶ Path ▶ Edit ▶ New ▶ `C:\ffmpeg\bin`*.
4. Open a new terminal and verify:
   ```powershell
   ffmpeg -version
   ffprobe -version
   ```

**macOS**

```bash
brew install ffmpeg
```

**Linux (Debian/Ubuntu)**

```bash
sudo apt-get update
sudo apt-get install -y ffmpeg
```

### Real-ESRGAN (optional, AI upscaling)

Download the portable `realesrgan-ncnn-vulkan` build from <https://github.com/xinntao/Real-ESRGAN/releases>, extract to a folder like `C:\realesrgan`, and add that folder to `PATH`. Relaunch the app — the header will show *Real-ESRGAN ✓*.

Without Real-ESRGAN the app still upscales perfectly well using FFmpeg Lanczos + the *CapCut Ultra HD* / *4K Crisp* sharpening templates.

---

## In-app help

Open the **Help** tab inside the app for a full walkthrough including tips on:

- Video duration & performance (no hard limit; longer clips take more CPU / disk).
- Supported formats: `.mp4 .mov .mkv .avi .webm .flv .m4v .mpg .mpeg .wmv .ts`.
- Watermark tips: text shadow, font file picker, image positioning.
- Upscaling guidance.
- Preset selection tips (which template for which use case).

---

## Quality templates — when to use which

| Template | Height | CRF | Sharpen | Use case |
|---|---|---|---|---|
| Original (no changes) | source | 20 | off | Keep exactly what you had, just apply the watermark |
| YouTube 1080p Clean | 1080 | 19 | 0.6 | YouTube uploads, social posts |
| **CapCut Ultra HD (1440p)** | 1440 | 18 | 0.9 | Crisp, clean, CapCut-style output |
| **4K Crisp (2160p)** | 2160 | 17 | 1.0 | Archival / big-screen / premium channel |
| Fast Preview | 720 | 26 | off | Quick draft to check the watermark placement |

The template dropdown in the **Output** tab auto-updates preset / CRF / sharpen values so you can see exactly what's being applied.

---

## Output naming

- **Default**: `<sourcename>_processed.mp4`.
- **Sequential** (tick the checkbox in the Output tab): `01_ab12cd.mp4`, `02_ef34gh.mp4`, …
  - Index is 1-based, zero-padded to fit the batch size (batch of 12 → `01..12`).
  - Each file gets a random 6-char hex code to avoid collisions across runs.

---

## Typical workflow

1. Add videos (drag & drop or *Add Videos…*).
2. Tick **Enable watermark / overlay**, pick text or image, tweak position / opacity / scale, choose a font file (optional).
3. Go to the **Output** tab and pick a quality template (start with *CapCut Ultra HD* for clean output).
4. Tick **Rename outputs sequentially** if you want numbered files.
5. Click **Process All** (enabled automatically once you've added at least one video).
6. Click **Export All as ZIP** once processing completes.
7. Save your configuration on the **Presets** tab.

---

## Project layout

```
video_batch_pro/
├── app/
│   ├── main.py
│   ├── ui/          main window, widgets, styles (QSS)
│   ├── processing/  ffmpeg_handler, upscale_handler, queue_manager
│   └── utils/       file_manager, zip_export, presets
├── tests/           pytest suite
├── launch.bat       Windows 1-click launcher
├── launch.sh        macOS / Linux 1-click launcher
├── run.py
├── requirements.txt
└── README.md
```

---

## Running the tests

```bash
pip install -r requirements-dev.txt
pytest
```

The suite auto-skips FFmpeg integration tests if FFmpeg isn't on PATH. UI smoke tests run offscreen via `QT_QPA_PLATFORM=offscreen`.

---

## Error handling

- Invalid videos are rejected with a clear dialog.
- Missing FFmpeg blocks processing with an explanatory message (action buttons stay disabled).
- **Process All** is disabled until at least one video is loaded and FFmpeg is detected.
- **Cancel** is only active while a batch is running.
- **Export All as ZIP** is disabled until at least one file completes successfully.
- Cancellation mid-batch terminates the current FFmpeg process cleanly.

---

## License

MIT — see `LICENSE`.
