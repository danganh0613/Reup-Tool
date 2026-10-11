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
20. **Nút «Dọn dẹp» thay «Render MP3» trên header** (`app/services/cleanup.py`, `MainWindow.clean_workspace`): hỏi trước,
    xoá phụ đề / bản dịch / giọng lồng / job / cache xem trước-TTS-khử giọng và làm trống hàng đợi; giữ video đã xuất,
    Dựng truyện, mẫu, cài đặt, model, bộ nhớ dịch. Cài đặt › Bộ nhớ dùng chung hàm này. Không còn nút xuất MP3.
21. **Kiểm tra trước khi chạy** (`MainWindow._preflight`, `Pipeline.needs`): cần dịch mà chưa có key Gemini / cần nghe
    lời thoại mà chưa cài Qwen3-ASR → báo ngay + «Mở Cài đặt?»; lưu key xong nút thành «Chạy tiếp». Mục Dịch nhắc chưa có key.
22. **Qwen3-ASR báo rõ khi chậm** (`qwen_asr_worker.py`): card còn trống < 5 GB (game / app khác chiếm) → dòng «⚠ GPU chỉ
    còn …»; `phase()` + luồng nhịp tim: thanh tiến độ nhích trong từng bước, 30 s im lặng thì log «… vẫn đang …»; log thời
    gian nạp model. Dò chữ cứng có khoá theo video (`hardsub.ensure_region`) — xem trước + Chuẩn bị không dò hai lần.
    Test: `audit_out/qa/test_worker_heartbeat.py`.
23. **Nghe lời thoại cả lượt bằng một tiến trình** (`qwen_asr.transcribe_batch`, `JobRunner._listen_first`, worker
    `--jobs`): ≥ 2 video cần Qwen3-ASR → nạp Qwen một lần nghe hết, giải phóng, nạp bộ căn mốc một lần căn hết, rồi mới
    dịch / lồng tiếng từng video (Qwen không giữ GPU lúc lồng tiếng). Bỏ qua video đang nghe → dừng, chạy lại phần còn
    lại (kết quả nghe lưu `<out>.pieces.json`); video chưa tới lượt → file `<out>.skip`. Lỗi → video đó nghe riêng như
    cũ. Test: `audit_out/qa/test_batch_listen.py` (worker giả `fake_qwen_worker.py`).
24. **Các mục còn lại của báo cáo QA** (test `audit_out/qa/test_qa_round2.py`): lưu «Xong» + file xuất qua lần mở app ·
    `remap_cues_for_trim` bỏ câu bị cắt gần hết, meta «cut» không rút gọn, retranslate chặn ghi khi bản dịch lệch số câu ·
    `srt_utils.decode_subtitle` (GBK / Big5 / Shift-JIS / EUC-KR, UTF-16 chỉ khi có BOM) + báo SRT 0 câu ·
    `platform.keep_awake` trong `set_running` · `qwen_asr.install` dừng pip ngay + lỗi có lý do, `local_ai._must` ·
    Hiệu ứng / Che chữ tự bật công tắc · `translate_vl.same_language` bỏ dịch Việt → Việt, báo câu sót chữ Hán.
25. **Gọi thử key Gemini khi lưu Cài đặt** (`gemini_models.check_key` — models.get, không tốn lượt dịch;
    `MainWindow.check_gemini_keys`): chỉ khi key / model vừa đổi. Key sai / bị chặn → thanh trạng thái báo key thứ mấy +
    «Nhập key…»; model không có → «Chọn model…»; mất mạng → chỉ ghi log. Test: `audit_out/qa/test_key_check.py`.
26. **Đoán bảng mã phụ đề bằng chấm điểm** (`srt_utils.decode_subtitle`, bỏ charset_normalizer): UTF-8 / UTF-16 có BOM
    → đọc thử cp1258, cp1252, EUC-KR, GB18030, Big5, Shift-JIS: chữ có dấu ghép thành âm tiết tiếng Việt (≥ 80 %) → cp1258
    (trả NFC); không thì chọn bảng mã CJK ra nhiều chữ thông dụng nhất (GB2312 cấp 1 / Big5 thường dụng, Hangul; hoà
    điểm → Hàn); còn lại cp1252. Test: `audit_out/qa/test_encoding.py` (8 loại × 200 file).
27. **Lượt P3 của báo cáo QA** (danh sách đầy đủ + mục để lại: `audit_out/qa/BAO_CAO_QA.md`, test `test_qa_p3.py`):
    settings.json ghi tạm + .bak + khôi phục · khoá một cửa sổ (`main._single_instance`) · `qwen_asr.kill_all` khi đóng
    app · bỏ cửa sổ không tiếng + lọc câu bịa (`_made_up`) · `-map 0:a:0` mọi chỗ trích âm · OCR → ASR khi không có chữ ·
    `friendly_error` regex + đường dẫn · `error_line` cắt ở dấu cách · Esc ×2, Delete chỉ khi có vùng chọn · ✕ hướng dẫn
    (`close_help`) · tìm không dấu · `parse_time` (5s / 1p30) · `Pipeline.needs` chặn video cần Gemini khi model hết
    lượt · Duyệt khoá video chưa sẵn sàng, báo giọng cũ / câu bỏ trống / tràn khung (`_voice_issues`) · thông báo chưa đọc.

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
