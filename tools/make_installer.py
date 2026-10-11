"""Đóng gói bộ cài Edit Studio bằng WinRAR SFX (máy build cần WinRAR; máy nhận KHÔNG cần gì).

    python tools\\make_installer.py            → release\\EditStudio-<ver>-Setup.exe (đủ, có CUDA)
                                                 release\\EditStudio-<ver>-Setup-Lite.exe (không có cuda\\)
    python tools\\make_installer.py --lite     → chỉ bản Lite
    python tools\\make_installer.py --full     → chỉ bản đủ

Bộ cài = file .exe tự giải nén: mở lên có hộp thoại chọn thư mục (mặc định %LocalAppData%\\Programs\\Edit Studio),
tự tạo lối tắt Desktop + Start Menu. Yêu cầu dist\\EditStudio đã build (build.bat).
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.config.paths import APP_VERSION  # noqa: E402

DIST = ROOT / "dist" / "EditStudio"
RELEASE = ROOT / "release"


def _arg(name: str) -> str | None:
    """--dist <thư mục build> / --out <thư mục ra>: đóng gói bản build khác, xuất sang thư mục khác (không đè bộ cài cũ)."""
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return None


if _arg("--dist"):
    DIST = Path(_arg("--dist")).resolve()
if _arg("--out"):
    RELEASE = Path(_arg("--out")).resolve()
RAR = Path(r"C:\Program Files\WinRAR\Rar.exe")
ICON = ROOT / "app" / "assets" / "icon.ico"
#: file/thư mục KHÔNG được lọt vào bộ cài (dữ liệu cá nhân, cache, model tải về)
FORBIDDEN = ("settings.json", "output", "logs", "cache", "models", "voices")

DOC = f"""EDIT STUDIO {APP_VERSION} — TỰ ĐỘNG DỰNG VIDEO
=========================================

CÀI ĐẶT
  1. Chạy file EditStudio-{APP_VERSION}-Setup.exe (hoặc bản -Lite).
  2. Nếu Windows hiện "Windows đã bảo vệ PC của bạn": bấm "Thông tin khác" → "Vẫn chạy".
     (Phần mềm dùng cá nhân, chưa mua chứng chỉ ký số nên SmartScreen cảnh báo.)
  3. Chọn thư mục cài (mặc định: C:\\Users\\<tên bạn>\\AppData\\Local\\Programs\\Edit Studio) → bấm Cài đặt.
  4. Mở bằng lối tắt "Edit Studio" trên Desktop hoặc Start Menu.

HAI BẢN
  • Setup.exe (đủ, ~1.1 GB): có sẵn CUDA runtime → máy có card NVIDIA nhận dạng giọng nói (Whisper) nhanh gấp 5–10 lần.
  • Setup-Lite.exe (~300 MB): không có CUDA. Máy nào cũng chạy được, Whisper chạy bằng CPU (chậm hơn, vẫn đúng).
    Máy có card NVIDIA vẫn dùng được GPU để RENDER video (NVENC) ở cả hai bản.

MÁY KHÁC CẦN GÌ?
  • Windows 10/11 64-bit. Không cần cài Python, FFmpeg hay gì thêm — có sẵn trong bộ cài.
  • Card màn hình: tự nhận (NVIDIA → Intel → AMD → CPU). Không có card rời vẫn chạy, chỉ chậm hơn.
  • Internet: cần khi tải model Whisper lần đầu (vài trăm MB, tự tải), khi dịch AI/giọng cloud.
    Giọng đọc Edge-TTS (miễn phí, cần mạng) và VieNeu (giọng Việt offline, tải model 300 MB lần đầu) không cần key.
  • API key (Gemini miễn phí / DeepSeek trả phí — để dịch): vào ☰ Cài đặt › AI dùng API key dán vào. Key lưu trong
    settings.json tại thư mục cài, KHÔNG có sẵn trong bộ cài — mỗi máy tự nhập.

CẬP NHẬT
  Chạy bộ cài mới đè lên thư mục cũ. Cài đặt, key và video đã xuất (thư mục output) được giữ nguyên.

GỠ
  Xoá thư mục cài và lối tắt. Phần mềm không ghi gì vào Registry.

Hướng dẫn sử dụng đầy đủ có ngay trong phần mềm (nút "Hướng dẫn" hoặc Ctrl+H).
"""

SFX_SCRIPT = """;Kịch bản SFX (WinRAR)
Title=Edit Studio {ver}
Path=%LocalAppData%\\Programs\\Edit Studio
Overwrite=1
Text
{{
Edit Studio {ver} — tự động dựng video ({flavor}).

Bấm "Cài đặt" để giải nén vào thư mục bên dưới (có thể đổi).
Sau khi xong sẽ có lối tắt "Edit Studio" trên Desktop và Start Menu.

Bộ cài không cần Python/FFmpeg cài sẵn. Lần đầu chạy nhận dạng giọng nói sẽ tự tải model (cần Internet).
}}
Shortcut=D,EditStudio.exe,,"Edit Studio — tự động dựng video","Edit Studio"
Shortcut=P,EditStudio.exe,,"Edit Studio — tự động dựng video","Edit Studio"
"""


def check_dist() -> None:
    if not (DIST / "EditStudio.exe").exists():
        sys.exit(f"Chưa có {DIST}\\EditStudio.exe — chạy build.bat trước.")
    if not RAR.exists():
        sys.exit(f"Không thấy WinRAR: {RAR}")
    bad = [n for n in FORBIDDEN if (DIST / n).exists()]
    if bad:
        sys.exit(f"dist\\EditStudio còn dữ liệu cá nhân, xoá trước khi đóng gói: {bad}")


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build(flavor: str) -> Path:
    lite = flavor == "lite"
    out = RELEASE / f"EditStudio-{APP_VERSION}-Setup{'-Lite' if lite else ''}.exe"
    out.unlink(missing_ok=True)
    script = RELEASE / f"sfx_{flavor}.txt"
    script.write_text(SFX_SCRIPT.format(ver=APP_VERSION, flavor="bản gọn, không CUDA" if lite else "bản đủ, có CUDA cho card NVIDIA"),
                      encoding="utf-8-sig")
    cmd = [str(RAR), "a", "-r", "-ep1", "-ma5", "-m3", "-md64m", "-sfx", "-y", "-idp",
           f"-iicon{ICON}", f"-z{script}"]
    cmd += [f"-x{DIST / n}" for n in FORBIDDEN]           # phòng hờ, ngoài check_dist()
    if lite:
        cmd.append(f"-x{DIST / 'cuda'}")
    cmd += [str(out), str(DIST / "*")]
    print(f"→ {out.name} ({'không' if lite else 'có'} cuda\\)…", flush=True)
    r = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, encoding="utf-8", errors="replace")
    if r.returncode not in (0, 1):     # 1 = cảnh báo (vd. file đang mở), vẫn ra archive
        print(r.stdout[-2000:], r.stderr[-2000:])
        sys.exit(f"Rar lỗi {r.returncode}")
    listing = subprocess.run([str(RAR), "lb", str(out)], text=True, capture_output=True, encoding="utf-8", errors="replace").stdout
    names = listing.splitlines()
    if lite and any(n.lower().startswith("cuda") for n in names):
        sys.exit("Bản Lite vẫn chứa cuda\\ — kiểm tra mặt nạ -x")
    if any(n.lower().startswith(f) for n in names for f in FORBIDDEN):
        sys.exit("Bộ cài chứa file bị cấm — kiểm tra lại")
    print(f"   {len(names)} file · {out.stat().st_size / 2**20:,.0f} MB · sha256 {sha256(out)[:16]}…", flush=True)
    return out


def main() -> None:
    flavors = ["full", "lite"]
    if "--lite" in sys.argv:
        flavors = ["lite"]
    elif "--full" in sys.argv:
        flavors = ["full"]
    check_dist()
    RELEASE.mkdir(parents=True, exist_ok=True)
    (DIST / "DOC_TRUOC.txt").write_text(DOC, encoding="utf-8-sig")
    shutil.copy(DIST / "DOC_TRUOC.txt", RELEASE / "DOC_TRUOC.txt")
    outs = [build(f) for f in flavors]
    (RELEASE / "SHA256.txt").write_text("".join(f"{sha256(o)}  {o.name}\n" for o in outs), encoding="utf-8")
    for f in RELEASE.glob("sfx_*.txt"):
        f.unlink()
    print("\nXong:", *[str(o) for o in outs], sep="\n  ")


if __name__ == "__main__":
    main()
