#!/bin/bash
# Đóng gói Edit Studio cho Mac chip Apple (M1/M2/M3/M4) NGAY TRÊN MÁY MAC → release/EditStudio-<ver>-macOS-arm64.dmg
# Làm đúng các bước của .github/workflows/build.yml (job macos) — dùng khi GitHub Actions không chạy được.
#
# Cần: Mac chip Apple + Python 3.12 cài từ python.org (có sẵn Tk; Python của Homebrew phải cài thêm python-tk@3.12)
#      + mạng (tải thư viện, FFmpeg). Không cần tài khoản Apple Developer (app ký ad-hoc, máy nhận làm theo DOC TRUOC.txt).
# Chạy: bấm đúp build_mac.command ở thư mục gốc, hoặc: bash tools/build_mac.sh
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
say() { printf '\n=== %s ===\n' "$*"; }

[ "$(uname -s)" = "Darwin" ] || { echo "Script này chỉ chạy trên macOS."; exit 1; }
[ "$(uname -m)" = "arm64" ] || { echo "Cần Mac chip Apple (arm64) — Mac Intel đã ngừng hỗ trợ."; exit 1; }
PY="$(command -v python3.12 || true)"
[ -n "$PY" ] || { echo "Chưa có Python 3.12 — tải ở https://www.python.org/downloads/macos/ rồi chạy lại."; exit 1; }

say "Môi trường build riêng (.venv-mac — không đụng Python của máy)"
[ -d .venv-mac ] || "$PY" -m venv .venv-mac
# shellcheck disable=SC1091
source .venv-mac/bin/activate
python -m pip install --upgrade pip
mkdir -p build_mac
# thư viện chính (bắt buộc): phần trên dòng «Tuỳ chọn» của requirements.txt
sed '/Tuỳ chọn/,$d' requirements.txt > build_mac/req-core.txt
pip install -r build_mac/req-core.txt av pyinstaller
python -c "import tkinter, platform; print('Tk', tkinter.TkVersion, platform.machine()); assert platform.machine() == 'arm64'"

say "Thư viện tuỳ chọn (nhận diện tiếng Trung, đọc chữ in sẵn) — gói nào không cài được trên Mac thì bỏ qua"
sed -n '/Tuỳ chọn/,$p' requirements.txt | sed 's/#.*//' | while read -r pkg; do
  [ -n "$pkg" ] || continue
  pip install "$pkg" || echo "⚠ Bỏ qua $pkg — app vẫn chạy, thiếu phần dùng gói này"
done

say "VieNeu (giọng Việt chạy trên máy, tuỳ chọn — lỗi thì bỏ qua)"
pip install "sea-g2p>=0.9.1" "onnxruntime>=1.20.0" numpy soundfile soxr "kaldi-native-fbank>=1.20" "tokenizers>=0.20" \
    huggingface_hub PyYAML "librosa>=0.11.0" && pip install --no-deps vieneu || echo "⚠ Không cài được VieNeu — app vẫn chạy (giọng Edge)."

say "Kiểm tra cú pháp"
python -m compileall -q app main.py

say "FFmpeg arm64 (build tĩnh — ffmpeg.martin-riedl.de)"
mkdir -p bin
if [ ! -x bin/ffmpeg ] || ! file bin/ffmpeg | grep -q arm64; then
  for t in ffmpeg ffprobe; do
    curl -fsSL --retry 5 --retry-delay 10 --retry-all-errors -o "/tmp/$t.zip" \
      "https://ffmpeg.martin-riedl.de/redirect/latest/macos/arm64/release/$t.zip"
    unzip -o -q "/tmp/$t.zip" -d bin
  done
  chmod +x bin/ffmpeg bin/ffprobe
fi
bin/ffmpeg -hide_banner -version | head -1
python tools/ci_check_ffmpeg.py bin/ffmpeg h264_videotoolbox hevc_videotoolbox

say "PyInstaller"
pyinstaller --noconfirm --distpath build_mac/dist --workpath build_mac/work EditStudio.spec
APP="build_mac/dist/Edit Studio.app"

say "macOS tối thiểu (đo từ thư viện trong gói) + ký ad-hoc"
MIN=$(find "$APP" -type f \( -name "*.so" -o -name "*.dylib" -o -perm -u+x \) -print0 \
      | xargs -0 -n1 vtool -show-build 2>/dev/null \
      | awk '$1 == "cmd" {cmd = $2}
             $1 == "minos" {print $2}
             $1 == "version" && cmd == "LC_VERSION_MIN_MACOSX" {print $2}' | sort -V | tail -1)
[ -n "$MIN" ] || MIN=12.0
echo "macOS tối thiểu: $MIN"
/usr/libexec/PlistBuddy -c "Set :LSMinimumSystemVersion $MIN" "$APP/Contents/Info.plist"
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict "$APP"

say "Chạy thử --selftest (dữ liệu thử ở thư mục riêng, không đụng dữ liệu Edit Studio đang dùng)"
DATA="$HOME/Library/Application Support/Edit Studio"
BACKUP=""
if [ -d "$DATA" ]; then BACKUP="$DATA.truoc-build-$(date +%s)"; mv "$DATA" "$BACKUP"; fi
"$APP/Contents/MacOS/EditStudio" --selftest &
pid=$!
( sleep 240; kill "$pid" 2>/dev/null ) &
wait "$pid" || true
if [ -f "$DATA/logs/selftest.txt" ]; then cat "$DATA/logs/selftest.txt"; OK=1; else
  tail -60 "$DATA/logs/edit_studio.log" 2>/dev/null || true; OK=0; fi
mkdir -p build_mac
[ -d "$DATA" ] && mv "$DATA" "build_mac/selftest-data-$(date +%s)"
[ -n "$BACKUP" ] && mv "$BACKUP" "$DATA"
[ "$OK" = 1 ] || { echo "✖ App không chạy được (không có logs/selftest.txt) — xem log ở trên."; exit 1; }

say "Tạo DMG"
VER=$(python -c "from app.config.paths import APP_VERSION; print(APP_VERSION)")
NAME="EditStudio-$VER-macOS-arm64"
rm -rf build_mac/dmg && mkdir -p build_mac/dmg release/mac
ditto "$APP" "build_mac/dmg/Edit Studio.app"
ln -s /Applications build_mac/dmg/Applications
sed -e "s/__MACOS_MIN__/$MIN/" -e "s/__ARCH__/Apple Silicon/" tools/DOC_MAC.txt > "build_mac/dmg/DOC TRUOC.txt"
OUT="release/mac/$NAME.dmg"
[ -e "$OUT" ] && OUT="release/mac/$NAME-$(date +%Y%m%d-%H%M).dmg"     # không đè bản cũ
hdiutil create -volname "Edit Studio" -srcfolder build_mac/dmg -ov -format UDZO "$OUT"
(cd release/mac && shasum -a 256 "$(basename "$OUT")" >> SHA256-macos-arm64.txt)
say "Xong: $ROOT/$OUT"
ls -lh "$OUT"
