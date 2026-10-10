# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: đóng gói Edit Studio thành thư mục dist/EditStudio (onedir).

macOS: thêm dist/Edit Studio.app (BUNDLE) — bin/ffmpeg + app/assets + presets nằm TRONG gói (xem bên dưới);
dữ liệu người dùng ở ~/Library/Application Support/Edit Studio (app/config/paths.py).

    pip install pyinstaller
    pyinstaller EditStudio.spec

Sau khi build, copy bin/ffmpeg.exe + bin/ffprobe.exe vào dist/EditStudio/bin/ và app/assets vào
dist/EditStudio/app/assets (build.bat làm sẵn). VieNeu (ONNX, không cần torch) được đóng gói kèm; Demucs (cần torch ~2 GB)
chỉ có ở bản chạy mã nguồn.
"""

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

block_cipher = None
ROOT = Path(SPECPATH)

MAC = sys.platform == "darwin"

datas = []   # Windows: app/assets được build.bat chép cạnh EXE (người dùng tự thêm nhạc/sfx/texture vào đó)
if MAC:
    # gói .app chỉ đọc → assets/presets mẫu + ffmpeg đi trong gói; lần chạy đầu app chép assets/presets ra thư mục dữ liệu
    datas += [(str(ROOT / "app" / "assets"), "app/assets"), (str(ROOT / "presets"), "presets")]
# Qwen3-ASR chạy bằng python của môi trường riêng (models/qwen_asr_env) → file worker phải nằm thật trên đĩa cạnh
# qwen_asr (Path(__file__).with_name), không chỉ trong gói PYZ
datas += [(str(ROOT / "app" / "services" / "qwen_asr_worker.py"), "app/services")]
datas += collect_data_files("customtkinter")
datas += collect_data_files("tkinterdnd2")
datas += collect_data_files("faster_whisper")
datas += collect_data_files("edge_tts")

hiddenimports = (
    collect_submodules("edge_tts")
    + collect_submodules("faster_whisper")
    + collect_submodules("ctranslate2")
    + collect_submodules("av")
    + collect_submodules("librosa")          # librosa nạp module con qua lazy_loader → phải khai báo tường minh
    + ["tkinterdnd2", "PIL.ImageTk", "winsound", "requests", "huggingface_hub", "tokenizers", "onnxruntime",
       "soxr", "soundfile", "yaml", "lazy_loader", "pooch", "msgpack", "joblib", "decorator", "hf_xet", "scipy.signal", "scipy.fft"]
)
binaries = []
if MAC:
    binaries += [(str(ROOT / "bin" / "ffmpeg"), "bin"), (str(ROOT / "bin" / "ffprobe"), "bin")]
# VieNeu v3 Turbo (ONNX): gói + dữ liệu giọng mẫu, G2P (sea_g2p có .pyd + .bin), fbank
for _pkg in ("vieneu", "vieneu_utils", "sea_g2p", "kaldi_native_fbank"):
    _d, _b, _h = collect_all(_pkg)
    datas += _d
    binaries += _b
    hiddenimports += _h

a = Analysis(
    ["main.py"],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["torch", "torchaudio", "demucs", "gradio", "matplotlib", "IPython", "notebook", "transformers"],
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="EditStudio",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(ROOT / "app" / "assets" / ("icon.icns" if MAC else "icon.ico")),
    target_arch=None,          # build theo kiến trúc của máy build (macOS: arm64 — chip Apple; Mac Intel đã ngừng)
)
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, strip=False, upx=False, name="EditStudio")
if MAC:
    sys.path.insert(0, str(ROOT))
    from app.config.paths import APP_VERSION

    app = BUNDLE(
        coll,
        name="Edit Studio.app",
        icon=str(ROOT / "app" / "assets" / "icon.icns"),
        bundle_identifier="io.github.danganh0613.editstudio",
        version=APP_VERSION,
        info_plist={
            "CFBundleDisplayName": "Edit Studio",
            "CFBundleShortVersionString": APP_VERSION,
            "LSMinimumSystemVersion": "12.0",      # CI đo lại từ thư viện thật trong gói rồi ghi đè
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,
        },
    )
