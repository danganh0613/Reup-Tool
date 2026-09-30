# Edit Studio

Công cụ tự động dựng video cho creator (Windows), viết lại từ đầu bằng Python + CustomTkinter + FFmpeg:
phụ đề tự động (Whisper / Gemini), dịch AI đa nhà cung cấp, lồng tiếng (Edge / ElevenLabs / Google / VieNeu),
che mờ, nhạc nền, logo/chữ/watermark, cắt ghép, màu sắc & hiệu ứng, chống trùng khi đăng lại video của bạn lên nhiều kênh, và **Dựng Truyện AI**
(kịch bản → giọng → ảnh/video AI → MP4 + SRT + metadata YouTube).

## Chạy

```bash
pip install -r requirements.txt
python main.py          # hoặc run.bat
```

- `bin/ffmpeg.exe` + `bin/ffprobe.exe` (bản gyan.dev full: có libass, NVENC/QSV/AMF). Không có thì chọn đường dẫn trong **Cài đặt**.
- API key (Gemini / Groq / DeepSeek / OpenAI / ElevenLabs / Google) nhập trong **Cài đặt** → lưu `settings.json` trên máy.
  Key Google AI Studio (`AIza…`) dùng chung: ô Gemini trống thì app lấy ô Google API key. Gói miễn phí của Gemini dịch và
  STT được, nhưng **không** tạo ảnh/video (429 quota) — Dựng Truyện sẽ ghép bằng ảnh tạm; muốn ảnh AI cần bật billing.
- Whisper tải model về `models/` lần đầu (`small` ≈ 460 MB). Có GPU NVIDIA thì cần thêm cuBLAS + cuDNN:
  bấm **«Cài CUDA runtime cho Whisper GPU»** trong module STT (pip, ~1.5 GB); chưa có sẽ tự chạy CPU (int8).
  Không có GPU: chọn STT «Gemini (cloud)».
- NVENC cần driver NVIDIA ≥ 610 với FFmpeg 9; không có sẽ tự chuyển QSV / AMF / CPU.
- Tuỳ chọn cài thêm trong app: **VieNeu** (TTS tiếng Việt local, clone giọng) và **Demucs** (tách lời thoại, cần torch).

### Chạy không giao diện (render hàng loạt)

```bash
python main.py --render a.mp4 b.mp4 --stt --out D:\xuat
```

`--stt` nhận diện phụ đề trước khi render, `--mp3` xuất MP3. Dùng cấu hình đã lưu (`output/last_config.json`);
nhật ký ở `logs/edit_studio.log`. Bản `.exe` cũng nhận các tham số này.

## Hướng dẫn trong app

Nút **Hướng dẫn** (header, `Ctrl+H`) hoặc nút «Hướng dẫn» trên tiêu đề mỗi module mở **bảng hướng dẫn nằm cạnh bảng tính năng**:
tự đổi theo module đang xem, 3 tab Bắt đầu · Mục này · Gặp lỗi, nút ⤢ mở cửa sổ đầy đủ có ô tìm. Nội dung ở
`app/ui/guide_content.py` (viết đơn giản, từng bước). Lần chạy đầu bảng tự mở; đóng một lần là thôi.

## Bố cục & phím tắt

Header: Cài đặt · Phím tắt · Thông báo · Thư mục xuất · Render MP3 · Render MP4 · Dừng.
Rail **Tính Năng** (F1–F11): Video · Phụ đề · STT · SRT · TTS · Che mờ · Nhạc · Logo · Cắt · Hiệu ứng · Dựng Truyện.
Xem trước: chip **Tỉ lệ · Độ phân giải · FPS · Vừa khung** ngay trên khung (21:9 · 16:9 · 4:3 · 1:1 · 3:4 · 4:5 · 9:16,
đồng bộ với module Video) · công cụ V (kéo) · T (khung phụ đề) · B (vẽ vùng che) · ẩn Sub/Blur · zoom · phát/tua · sóng âm ·
toàn màn hình. Chưa có video: kéo thả vào khung hoặc bấm vào khung để chọn file.
Thanh tiến trình 5 bước: Chuẩn bị → Tạo TTS → Ghép timeline → Bake hình ảnh → Ghép MP4.
Space phát/dừng · J/K/L · ←/→ · , . từng khung · I/O và [ ] điểm cắt · M tắt tiếng · Ctrl+Z/Y hoàn tác khung/vùng ·
Ctrl+S lưu · Ctrl+R render · Ctrl+E sửa SRT · Esc dừng.

## Cấu trúc

```
main.py                     điểm vào
app/config/                 đường dẫn, theme: 5 bộ màu trọn gói (Studio · Midnight · Graphite · Orchid · OLED), đổi ngay không cần mở lại
app/ui/icons.py             icon đơn sắc từ Segoe Fluent Icons (Win11) / MDL2 (Win10) → CTkImage; bảng emoji→icon
app/core/state.py           cấu hình mọi module + autosave last_config.json + preset
app/services/
  ffmpeg.py                 bọc ffmpeg/ffprobe: probe, chạy có tiến độ, trích khung, PCM
  render_pipeline.py        dựng filter graph (khung hình, tốc độ, cắt, audio mix, encoder GPU/CPU, MP3)
  stages.py                 stage của module: che mờ, logo/chữ/watermark/viền, màu/preset/deshake/grain/Ken Burns/texture,
                            chống trùng (dịch khung, nhiễu, lệch màu, phóng & trượt, lệch nhịp âm, GOP), nhạc nền
  job_runner.py             pipeline 1 video: STT → dịch → TTS → render; hàng đợi & tác vụ lẻ chạy nền
  whisper_stt.py / stt_gemini.py      nhận diện giọng nói local / cloud
  translate_router.py       dịch đa nhà cung cấp, xoay key, glossary, bộ nhớ dịch, checkpoint
  subtitle_style.py         SRT → ASS: khung phụ đề, hiệu ứng chữ, nền, karaoke
  tts/                      Edge, ElevenLabs (xoay key), Google (API key / service account), VieNeu (local), generator khớp thời gian
  player.py                 phát xem trước (PyAV + winsound), sóng âm
  story/                    Dựng Truyện: dự án, prompt, Gemini (kịch bản/ảnh/Veo), engine, assembler
app/ui/                     cửa sổ chính, xem trước (canvas), tiến trình, 11 module, cài đặt, hộp thoại
output/                     videos · subs · voice · jobs · projects
presets/  cache/  models/  logs/  voices/(giọng clone)
```

## Đóng gói

```bash
build.bat        # PyInstaller → dist/EditStudio/EditStudio.exe (kèm bin/ffmpeg, app/assets, presets)
```

- Thư mục `dist/EditStudio` (~690 MB) copy đi máy khác chạy được; mỗi lần build lại PyInstaller **xoá và tạo mới**
  thư mục này, nên `output/`, `models/`, `settings.json` trong đó sẽ mất — giữ bản gốc ở nơi khác.
- Bản .exe **không có VieNeu và Demucs** (cần torch ~2 GB) và không tự `pip install` được; muốn dùng thì chạy bản
  mã nguồn (`run.bat`).
- `build.bat` gọi `tools/bundle_cuda.py`: nếu máy build đã cài `nvidia-cublas-cu12` + `nvidia-cudnn-cu12` thì chép
  cuBLAS/cuDNN/NVRTC (~1.3 GB) vào `dist/EditStudio/cuda/` để máy nhận có GPU NVIDIA chạy Whisper trên GPU.
  Gửi cho máy không có NVIDIA thì xoá thư mục `cuda/` cho nhẹ.

### Chạy trên máy khác — tự nhận phần cứng

Không cần cài gì thêm ngoài driver card màn hình. Mỗi lần render app **thử thật** từng bộ mã hóa bằng ffmpeg:
NVENC (NVIDIA) → QSV (Intel, kể cả iGPU onboard) → AMF (AMD, kể cả APU) → CPU (libx264/x265). Chọn cụ thể một
backend mà máy đó không có thì vẫn tự dò tiếp. Module Video hiện dòng «"auto" sẽ dùng …» sau khi dò xong.
Whisper: có NVIDIA + cuBLAS/cuDNN → GPU (float16), không thì CPU (int8) — tự chuyển, không cần chỉnh.
NVENC cần driver NVIDIA ≥ 610 (FFmpeg 9); driver cũ hơn sẽ tự sang QSV/AMF/CPU.

## Đóng gói bộ cài (gửi máy khác)

```bash
build.bat
python tools\make_installer.py
```

Ra `release\EditStudio-<ver>-Setup.exe` (đủ, kèm CUDA ~1 GB) và `-Lite.exe` (~200 MB, không CUDA) — file tự giải nén
WinRAR SFX: hộp thoại chọn thư mục (mặc định `%LocalAppData%\Programs\Edit Studio`), tự tạo lối tắt Desktop/Start Menu,
kèm `DOC_TRUOC.txt`. Máy nhận không cần cài gì thêm. `settings.json`/output/logs không bao giờ được đóng vào gói.
