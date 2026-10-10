# Ghi chú bàn giao — phiên làm việc trên cloud (Claude Code)

Đọc file này trước khi làm tiếp ở máy local. Viết ngày 2026-10-05.

## Trạng thái

- **Nhánh làm việc:** `claude/macos-apple-silicon`, mở PR #2 (`danganh0613/Reup-Tool#2`), **chưa merge**.
- **Commit mới nhất:** `31d0e81`. CI xanh: Windows ZIP, macOS Apple Silicon DMG. (Bản macOS Intel đã ngừng từ
  2026-10-10 — PyTorch / PaddlePaddle không còn bản cho Mac Intel.)
- **Máy local:** `F:\Project\Edit Studio` (Windows, PowerShell). Chạy app bằng `python main.py`.
- **Bảo mật:** không đưa API key vào chat hay code. Key chỉ nhập trong app: ☰ Cài đặt.

## Stack

- Python 3.12 với CustomTkinter, code chạy được trên cả bản 5.2.2 lẫn 6.0. Giao diện tiếng Việt.
- FFmpeg dùng để dựng video. PyInstaller đóng gói; GitHub Actions build bằng `EditStudio.spec` và `build.bat`.
- Model AI chạy trên máy qua onnxruntime + numpy (không cần torch).
- **Quy ước code:**
  - comment tiếng Việt, ngắn;
  - nút tạo bằng `app/ui/widgets/ui.py: button(...)`;
  - màu lấy từ token trong `app/config/theme.py` (`T.ACCENT`, `T.DANGER`…).

## Luồng chính của app

**Nạp video → Mẫu → Chuẩn bị → Duyệt → Xuất**

- **Chuẩn bị** (`Pipeline.prepare` trong `app/services/job_runner.py`) làm lần lượt:
  - nhận diện lời (STT);
  - dịch;
  - nhận diện người nói;
  - tạo giọng lồng;
  - khử giọng gốc.
- **Duyệt** là tab trung tâm (`app/ui/widgets/review_window.py`, class `ReviewPanel`):
  - video ở giữa màn hình;
  - bảng «Video này sẽ ra sao» có 10 dòng (`app/ui/widgets/video_summary.py`);
  - mỗi dòng có nút «Sửa ›» mở đúng tab tính năng.
- **Sửa khi đang duyệt chỉ áp cho video đó** («Chỉnh riêng», xem `AppState.review_scope` / `_scope_edit` trong `app/core/state.py`):
  - dải vàng đầu bảng có nút «Áp cho tất cả» và «‹ Về Duyệt»;
  - **máy tự điền cấu hình** (gợi ý giọng…) thì phải gọi `state.config_changed(section, auto=True)` để không bị tính là người dùng sửa.

## Những gì đã làm trên cloud (cũ → mới)

1. **Hỗ trợ macOS** Apple Silicon (DMG) và CI build Windows + macOS.
2. **Nhiều giọng:**
   - nhận diện ai nói câu nào (`app/services/diarize.py`);
   - mỗi người nói một giọng (`app/ui/widgets/speaker_panel.py`);
   - thêm giọng Gemini TTS, FPT.AI, thư viện cộng đồng ElevenLabs, Edge đa ngôn ngữ.
3. **Xem trước giống bản render:** hiệu ứng, phụ đề thật, giọng lồng, nhạc, cắt, tốc độ; có «Render thử» 15 giây.
4. **Mẫu dựng** trên thanh trên cùng (`app/services/templates.py`, `app/ui/widgets/template_dialogs.py`):
   - cấu hình riêng từng video;
   - 3 mẫu dựng sẵn.
5. **Luồng Chuẩn bị → Duyệt → Xuất.** Thêm tự dò và che phụ đề cứng, thư mục theo dõi, mẫu tên file xuất.
6. **Phụ đề:**
   - khung phụ đề tự co theo chữ và bám đúng chỗ chữ cứng;
   - AI dịch lại (`translate_router.retranslate`).
7. **Hàng chờ việc:** bấm khi đang bận thì được hỏi xếp hàng, xong việc trước tự chạy (`_offer_wait` / `_run_waiting`).
8. **Khử giọng gốc bằng AI** (`app/services/vocal_remove.py`):
   - model UVR MDX-Net `Kim_Vocal_2.onnx` (~64 MB), tự tải về `models/uvr`;
   - giữ nhạc nền và tiếng động; xử lý theo đoạn 6 giây, RAM ~1,3 GB;
   - kết quả lưu ở `cache/novocal`;
   - đóng app thì `vocal_remove.shutdown()` dừng gọn;
   - luồng đang chờ cùng video thì báo tiếp tiến độ của lượt đang chạy (`_PROG`).
9. **Cắt khoảng lặng của TTS** (`generator.trim_silence`, `PULL_EARLY`) để các câu thoại liền mạch, không khựng.
10. **Thử lại video lỗi** ngay trong hàng đợi. Gemini quá tải thì tự lùi thời gian chờ và đổi sang model khác.
11. **Ô «Nhờ AI sửa»:** chỉ sửa những câu liên quan tới yêu cầu (`translate_router.ai_edit`), tách riêng khỏi ô phong cách dịch.
12. **Thanh tính năng bên trái** gọn còn 72px, bỏ chữ «Tính Năng».
13. **Thiết kế lại tab Duyệt lấy video làm trung tâm:**
    - trình sửa từng câu (`app/ui/widgets/cue_editor.py`) chuyển sang tab «Lời thoại» (SRT);
    - mỗi màn hình chỉ một nút chính.
14. **Sửa lỗi:** chỉ mở tab TTS mà video tự bị đánh dấu «Chỉnh riêng» (do bảng người nói tự điền giọng).
15. **Thanh tiến trình → thanh trạng thái 1 dòng** (`app/ui/progress_panel.py`):
    - hiện bước thật của từng video, %, thời gian còn lại;
    - nút Thử lại / Mở thư mục;
    - câu lỗi dễ hiểu (`friendly_error`).
16. **Header trở lại như cũ** theo yêu cầu: Cài đặt · Hướng dẫn · Phím tắt · Thông báo | Thư mục xuất · Render MP3 · nút chính · Dừng (luôn hiện, mờ khi rảnh). Không còn menu ☰.
17. **Màu nút khi bị khoá đồng bộ** (patch `CTkButton` trong `app/ui/widgets/ui.py`):
    - nút chính → nền accent dịu + chữ accent;
    - nút đỏ → nền đỏ dịu + chữ trắng;
    - nút xám → icon mờ cùng chữ.
18. **Nhiều giọng bật sẵn; mẫu nhớ giọng theo THỨ TỰ người nói:**
    - `diarize.resolve_voices`: người 1 → giọng 1, người 2 → giọng 2…;
    - dư người thì tự gợi ý thêm giọng Nam/Nữ (`Pipeline.voice_config`);
    - hộp «Lưu mẫu» hiện rõ giọng sẽ lưu.
19. **Sửa sau QA audit (10/2026)** — chi tiết + phát hiện còn lại: `audit_out/qa/BAO_CAO_QA.md`:
    - Dừng tất cả / **Bỏ qua 1 video** (✕ trên dòng đang chạy, nút «Bỏ qua video này» ở thanh trạng thái): mỗi video một cờ huỷ (`JobRunner.skip`);
    - chờ hạn mức Gemini, tách câu dài, tạo giọng đều dừng được ngay; mất mạng / model bị gỡ / chưa có key có câu báo + nút riêng;
    - sửa sau khi xuất: trạng thái «Cần xuất lại», ↻ «Xuất lại video này», Render bỏ qua video đã Xong;
    - video trùng tên khác thư mục không đè phụ đề / video xuất (`Pipeline.file_stem`); xuất ra file `.dang-xuat` rồi mới đổi tên;
    - Lời thoại tự nạp lại khi file phụ đề đổi; nạp lại video cũ → hỏi dùng lại phụ đề đã làm.

## Còn tồn / để ý khi làm tiếp

- **Dải «Đang duyệt … chỉ áp cho video này»** hiện cả trên tab Video khi video chưa chuẩn bị. Có thể chỉ nên hiện khi video đã «Chờ duyệt».
- **Ghi cấu hình lặng lẽ:**
  - `speaker_panel._entry` thêm mục giọng vào `tts.speaker_voices` khi chỉ xem;
  - `SaveTemplateDialog._order_voices` sắp lại danh sách giọng ngay khi mở hộp.
  - Cả hai không đổi kết quả nhưng có ghi vào cấu hình.
- **Ước lượng thời gian còn lại** (ETA) tính thô theo trọng số các bước, có thể nhảy lúc đầu (chỉ hiện sau 8 giây và khi đã ≥ 5%).
- **Cảnh báo pyflakes cũ:** biến `cv` không dùng ở `app/ui/preview_panel.py` (~dòng 734).
- **Tốc độ khử giọng:** chạy CPU khá chậm với video dài; có GPU (CUDA/DirectML/CoreML) thì onnxruntime tự dùng.
- **Merge PR:** PR #2 chưa merge — quyết định merge vào nhánh chính khi đã test ổn.

## Cách test (local)

- **Không có test tự động trong repo.** Trên cloud mình test bằng script tạm (Xvfb + chụp màn hình), nên không mang theo được.
- **Kiểm tra tay nên làm sau mỗi thay đổi lớn:**
  1. Nạp 2–3 video, chọn mẫu «Review phim dọc 9:16», bấm **Chuẩn bị**. Theo dõi thanh trạng thái dưới cùng.
  2. Ở tab **Duyệt**:
     - xem bảng tổng quan;
     - bấm «Sửa ›» ở một dòng, sửa thử, rồi «‹ Về Duyệt»;
     - bấm **Duyệt** (Ctrl+Enter).
  3. Bấm **Xuất** và kiểm tra file trong Thư mục xuất.
  4. Thử **Dừng** giữa chừng, nạp một file hỏng (sẽ hiện ✖ + Thử lại), và đổi theme trong Cài đặt.
  5. Chuẩn bị 3 video, bấm **Bỏ qua video này** ở thanh trạng thái (hoặc ✕ trên dòng đang chạy) → video sau chạy tiếp.
- **Lint nhanh:** `python -m pyflakes app`.
- **Bài test tự động (local, trong `audit_out/`, không commit):** `audit_out/qa/test_qa_fixes.py`, `audit_out/b_only/smoke_b_ui.py`,
  `audit_out/ux_sub/smoke_sub_ui.py`, `audit_out/review/smoke_retry_model.py` — thêm tham số `ctk52` để chạy với CustomTkinter 5.2.
