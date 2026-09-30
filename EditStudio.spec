# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: đóng gói Edit Studio thành thư mục dist/EditStudio (onedir).

    pip install pyinstaller
    pyinstaller EditStudio.spec

Sau khi build, copy bin/ffmpeg.exe + bin/ffprobe.exe vào dist/EditStudio/bin/ và app/assets vào
dist/EditStudio/app/assets (build.bat làm sẵn). VieNeu (ONNX, không cần torch) được đóng gói kèm; Demucs (cần torch ~2 GB)
chỉ có ở bản chạy mã nguồn.
"""

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

block_cipher = None
ROOT = Path(SPECPATH)

datas = []   # app/assets được build.bat chép cạnh EXE (người dùng tự thêm nhạc/sfx/texture vào đó)
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
    icon=str(ROOT / "app" / "assets" / "icon.ico"),
)
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, strip=False, upx=False, name="EditStudio")
