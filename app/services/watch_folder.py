"""Thư mục theo dõi: video mới chép vào thư mục → tự vào hàng đợi (kèm mẫu), tuỳ chọn tự chuẩn bị / tự xuất.

Chỉ nhận file đã chép xong: kích thước và giờ sửa không đổi qua hai lần quét liên tiếp và đã đứng yên vài giây.
"""

from __future__ import annotations

import time
from pathlib import Path

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".ts", ".flv", ".wmv", ".mpg", ".mpeg"}
WATCH_ACTIONS = ["Chỉ thêm vào hàng đợi", "Thêm & tự chuẩn bị (chờ duyệt)", "Thêm & tự xuất luôn"]
SHARED = "— Dùng mẫu chung —"


class FolderWatcher:
    def __init__(self) -> None:
        self._sizes: dict[str, tuple[int, int]] = {}

    def existing(self, folder: str) -> list[str]:
        try:
            return sorted(str(p) for p in Path(folder).iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTS)
        except OSError:
            return []

    def scan(self, folder: str, skip: set[str]) -> list[str]:
        """File video mới, đã chép xong (``skip``: đường dẫn viết thường đã có / đã thấy)."""
        ready: list[str] = []
        for key in self.existing(folder):
            if key.lower() in skip:
                continue
            try:
                st = Path(key).stat()
            except OSError:
                continue
            sig = (st.st_size, int(st.st_mtime))
            if st.st_size > 0 and self._sizes.get(key) == sig and time.time() - st.st_mtime > 2:
                ready.append(key)
                self._sizes.pop(key, None)
            else:
                self._sizes[key] = sig
        return ready
