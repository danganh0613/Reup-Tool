"""Kiểm tra bản FFmpeg sẽ đóng gói (dùng trong CI): có filter ``ass`` và phụ đề THẬT SỰ hiện chữ.

    python tools/ci_check_ffmpeg.py bin/ffmpeg [encoder_bắt_buộc ...]

FFmpeg build tĩnh trên macOS hay thiếu cấu hình font → libass không báo lỗi mà phụ đề trống trơn,
nên ở đây vẽ một khung có phụ đề (bằng chính ``write_ass`` của app) rồi đếm điểm ảnh sáng.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    ffmpeg = str(Path(sys.argv[1]).resolve())        # bước render chạy trong thư mục tạm → cần đường dẫn tuyệt đối
    need_encoders = sys.argv[2:]
    filters = subprocess.run([ffmpeg, "-hide_banner", "-filters"], capture_output=True, text=True).stdout
    if not any(line.split()[1:2] == ["ass"] for line in filters.splitlines() if len(line.split()) > 1):
        print("✖ FFmpeg không có filter ass (thiếu libass)")
        return 1
    encoders = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    missing = [e for e in need_encoders if f" {e} " not in encoders]
    if missing:
        print("✖ FFmpeg thiếu encoder:", ", ".join(missing))
        return 1

    from PIL import Image

    from app.core.state import SubtitleConfig
    from app.services.srt_utils import Cue
    from app.services.subtitle_style import write_ass

    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        write_ass([Cue(1, 0.0, 5.0, "Xin chào Edit Studio 123", {})], SubtitleConfig(), 640, 360, d / "sub.ass")
        res = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                              "color=c=black:s=640x360:d=1", "-vf", "ass=sub.ass", "-frames:v", "1", "out.png"],
                             cwd=d, capture_output=True, text=True)
        if res.returncode != 0:
            print("✖ Render phụ đề lỗi:", res.stderr[-800:])
            return 1
        img = Image.open(d / "out.png").convert("L")
        lit = sum(img.histogram()[61:])           # số điểm ảnh sáng hơn nền đen
    print(f"Phụ đề: {lit} điểm ảnh sáng")
    if lit < 300:
        print("✖ Phụ đề không hiện chữ (libass không tìm được font)")
        return 1
    print("✔ FFmpeg OK: ass", *need_encoders)
    return 0


if __name__ == "__main__":
    sys.exit(main())
