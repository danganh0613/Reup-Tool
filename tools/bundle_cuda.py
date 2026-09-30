"""Chép cuBLAS 12 + cuDNN 9 (+ NVRTC) từ gói pip nvidia-* vào dist/EditStudio/cuda để bản .exe chạy Whisper GPU.

    python tools/bundle_cuda.py [dist/EditStudio]

Không cài gói nvidia-* thì bỏ qua (bản .exe vẫn chạy, Whisper dùng CPU). Xoá thư mục cuda/ nếu gửi cho
máy không có GPU NVIDIA để gói nhẹ hơn (~1.7 GB).
"""

from __future__ import annotations

import shutil
import site
import sys
from pathlib import Path

PKGS = ("cublas", "cudnn", "cuda_nvrtc")
# Không cần cho Whisper (ctranslate2 chỉ dùng GEMM + conv): nvblas, bản .alt của nvrtc, cudnn_adv (RNN/attention)
SKIP = ("nvblas", ".alt.", "cudnn_adv")


def main() -> int:
    try:  # console cmd.exe (cp1252) không in được tiếng Việt/mũi tên
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    dist = Path(sys.argv[1] if len(sys.argv) > 1 else "dist/EditStudio").resolve()
    if not dist.is_dir():
        print(f"Không thấy {dist}")
        return 1
    roots = [Path(p) for p in site.getsitepackages() + [site.getusersitepackages()] if p]
    src_dirs = [r / "nvidia" / pkg / "bin" for r in roots for pkg in PKGS]
    src_dirs = [d for d in src_dirs if d.is_dir()]
    if not src_dirs:
        print("Chưa cài nvidia-cublas-cu12 / nvidia-cudnn-cu12 → bỏ qua CUDA (Whisper trên .exe sẽ dùng CPU).")
        return 0
    out = dist / "cuda"
    out.mkdir(parents=True, exist_ok=True)
    total = 0
    n = 0
    for d in src_dirs:
        for dll in d.glob("*.dll"):
            if any(k in dll.name for k in SKIP):
                continue
            dst = out / dll.name
            if not dst.exists() or dst.stat().st_size != dll.stat().st_size:
                shutil.copy2(dll, dst)
            total += dll.stat().st_size
            n += 1
    print(f"CUDA runtime: {n} DLL → {out} ({total / 1e9:.2f} GB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
