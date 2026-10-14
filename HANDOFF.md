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
28. **Lượt cuối của báo cáo QA** (mục cỡ M + P1/P2 còn sót; test `audit_out/qa/test_qa_m.py`, 75 kiểm tra):
    - Bố cục: tab Lồng tiếng (công tắc + giọng đầu, «Khớp thời gian» / «Giọng đã tạo» thu gọn, Cao độ chỉ khi Edge) ·
      tab Video (hàng đợi → khung hình → âm thanh → cấu hình riêng; theo dõi / «Tên & nơi lưu file xuất» / «Nâng cao
      xuất file» thu gọn) · tab Truyện (Chủ đề + «Chạy tất cả» đầu trang) · công tắc «Duyệt trước» trên header
      (`MainWindow.set_review_mode`) · ‹ n/N › trên thanh phát · «Sửa ›» cuộn tới khối (`BaseModule.scroll_to_section`).
    - Tiến độ: `Pipeline.planned_steps` + `step_bands` (dải theo bước thật, không đi lùi); `ProgressPanel.set_detail`.
    - Giọng: `VideoItem.voice_locked` («Dùng giọng có sẵn» thật sự dùng khi xuất) · `Pipeline.voice_now` (nút Tạo giọng
      cùng đường Chuẩn bị) · `srt_sha` / `track_matches` (sửa SRT cùng giây) · `_prune_tracks` (giữ 3 track / video) ·
      câu «không đọc» (`<srt>.noread.json`, ⊘ trong Lời thoại, giữ âm gốc khi khử giọng `_keep_original_at_noread`).
    - Dịch: một ô «Ngôn ngữ video» (`SubtitleConfig.source_hint`) · Checkpoint theo câu nguồn · `retranslate(memory_key)`
      · `<bản dịch>.locks.json` (câu đã rút gọn / sửa tay) · `_SPLIT_AI_DOWN` · Gemini treo 2 lần → mất mạng.
    - Lỗi / dữ liệu: `NoSpeech` (video chỉ có nhạc vẫn xuất) · `ffmpeg_reason` · `friendly_error` OOM / mạng · Edge
      `_offline` · OCR thiếu → nghe giọng · Qwen hết VRAM → CPU, báo tải model lần đầu, căn mốc theo ngôn ngữ từng đoạn ·
      dòng video ở ổ chưa cắm giữ trong cấu hình (`_missing_rows`) · `last_config.json.bak` mỗi phiên · `_free_output` ·
      `attach_srt` · nhạc nền bỏ hệ số ẩn 20 % (`_fold_bgm_master`) · `BUILTIN_PRON` (tên hãng) · `_tk_error`.
29. **Chạy thử video thật bằng bản .exe** (`EditStudio.exe --render VIDEO --out DIR`; test `audit_out/qa/test_encoder.py`):
    - **Xuất bằng NVIDIA hỏng hết** với ffmpeg 9.0.1 kèm theo: ffmpeg 9 đổi `-spatial_aq` → `-spatial-aq` («Unrecognized
      option»). Nay `FFmpeg.encoder_options` đọc `-h encoder=…` để chọn đúng tên (`_option`), không có thì bỏ.
    - GPU vẫn lỗi (không phải Dừng / ổ đầy / nguồn hỏng — `cpu_may_help`) → `run_plan` xuất lại bằng `RenderPlan.cpu_cmd`,
      `mark_encoder_broken` để cả phiên dùng CPU; tab Truyện ghép lại các cảnh bằng CPU (`cpu_fallback`).
    - **OCR trong bản .exe chưa từng chạy được:** thiếu cấu hình pipeline PaddleX, metadata gói (PaddleX dò thư viện kèm
      theo bằng metadata) và DLL trong `paddle/libs` → `EditStudio.spec` đóng gói thêm. `ocr_hardsub.available()` kiểm cả
      `paddlex/configs/pipelines/OCR.yaml`; lỗi lúc khởi tạo PaddleOCR → `NoHardsub` (tự chuyển sang nghe giọng nói).
    - **Dịch lỗi cả video** khi Gemini Flash Lite bỏ sót dòng 3 lần ở một lô («kết quả thiếu dòng / sai dạng»): nay
      `translate_vl.translate_cues_vl` chia đôi lô dịch lại (`direct`), câu đơn vẫn lỗi thì giữ lời gốc + báo số câu (không
      ghi bộ nhớ dịch / tiến độ); tóm tắt + thuật ngữ lỗi thì dịch tiếp không có thuật ngữ (test
      `audit_out/qa/test_translate_split.py`).
    - AI hay viết dấu `"` trong câu dịch mà không thoát → JSON hỏng cả lô: `translate_router._load_json` thử thoát lại
      dấu `"` giữa giá trị từng dòng (`_escape_inner_quotes`) trước khi báo hỏng.
    - **OCR rồi Whisper trong cùng tiến trình .exe** → hộp «__kmpc_end_masked could not be located … ctranslate2.dll»:
      Paddle mang `libiomp5md.dll` bản 2019, ctranslate2 cần bản 2025, Windows chỉ nạp một bản theo tên (bản mã nguồn
      không lộ vì torch nạp sẵn bản mới). Spec thay `paddle/libs/libiomp5md.dll` bằng bản của ctranslate2; `main.py`
      `SetErrorMode` để thiếu DLL thì báo lỗi cho code (bỏ bước phụ) chứ không bật hộp chờ bấm OK (treo cả lượt xuất).
      Giả lập: `audit_out/qa/dll_frozen_sim.py A|B` (ẩn torch như bản .exe).
    - Dịch xong mà hết lượt AI ở bước rút gọn (`fit_texts`) → giữ bản dịch, bỏ rút gọn (trước: lỗi cả video).
      `split_long_sentences` báo «Tách N câu dài bằng AI…» (model có suy luận mất cả phút, không báo thì tưởng treo).
    - `main.py --render` thêm `--config FILE` (vd cấu hình của bản cài), không ghi lại cấu hình làm việc. Thử bản mã nguồn
      bằng cấu hình của bản cài:
      `python main.py --render VIDEO --config F:\Tool\EditStudio\output\last_config.json --out audit_out\src_test`.
30. **Nhận diện tự động: Qwen3-ASR + chữ in sẵn** (người dùng không chọn — bỏ ô «Lấy lời từ»; test
    `audit_out/qa/test_ocr_group.py`, 35 kiểm tra). Người dùng chấm: bản Qwen (câu trọn, đúng nhịp nói) > OCR > OCR .exe;
    Qwen nghe sai tên riêng (钳嘴鹤 → «钱嘴冠», 䴙䴘 → «PT»), OCR đúng chữ nhưng câu vụn (creator cắt câu thành 2–3 dòng).
    - `Pipeline.transcribe`: có Qwen → nghe giọng nói, video có phụ đề in sẵn thì `hardsub_cues` (OCR, CPU) chạy song song
      lúc Qwen chạy (GPU) → `ocr_hardsub.correct_with_ocr`: giữ câu + mốc của Qwen, thay chỗ nghe sai bằng chữ trên hình —
      dò toàn bộ lời với toàn bộ chữ (SequenceMatcher theo chữ Hán / từ Latin), chỉ thay đoạn ≤ 6 chữ nằm giữa hai đoạn
      khớp, dài tương đương, chữ hiện trên hình trong ± 2 s quanh câu, và **đọc giống** (pypinyin: nghe sai ra chữ đồng âm
      钱/钳; OCR đọc nhầm ra chữ trông giống mà đọc khác 白/自 → giữ chữ nghe được). Cụm cách một chữ xét chung («钱嘴冠»).
    - Qwen nghe được < ¼ số chữ trên hình (`SPEECH_MIN_SHARE` — video chỉ nhạc + phụ đề) → lấy lời từ chữ in sẵn
      (`ocr_hardsub.finish`: mốc SenseVoice + `translate_vl.group_fragments` AI gộp mẩu chữ thành câu). Phụ đề khác thứ
      tiếng thì số chữ tương đương → vẫn theo lời nghe được. Chưa cài Qwen / video không tiếng → chỉ chữ in sẵn; đã nhớ
      «không có lời» vẫn thử chữ in sẵn. Nghe nhiều video một lượt (`_listen_first`) đọc chữ song song rồi `with_hardsub`.
    - Dịch: AI hay viết `"` không thoát → Gemini (chế độ JSON) cắt chuỗi ngay đó, JSON vẫn hợp lệ («Biệt danh "tăng áp"»
      → «Biệt danh »). `QUOTE_RULE` trong mọi lời nhắc + `cut_at_quote` (còn dấu cách cuối = cụt): dịch sát cụt → hỏi lại;
      dịch thoát cụt / thiếu / ngắn chưa tới nửa bản dịch sát → dùng bản dịch sát cho câu đó (`_free_broken`, không bỏ cả
      lô); dịch lại / rút gọn cụt → giữ câu cũ.
    - Chạy trước (`_preflight`): chưa cài Qwen mà máy đọc được chữ → không chặn. `pypinyin` thêm vào requirements + spec.

31. **Dịch: cả video một lượt + phóng tác cho người xem Việt** (test `audit_out/qa/test_whole.py` 16,
    `test_transcreate.py` 18; thử câu tiếng lóng `audit_out/qa/try_transcreate.py`):
    - `translate_cues_vl`: mỗi phần ≤ 25 câu / 1500 ký tự (`WHOLE_LINES`, `_windows`) gửi MỘT lượt (`whole_prompt`):
      AI đọc hết câu chuyện rồi trả `terms` + mỗi câu `direct` (dịch sát, đã đoán lại lời nghe / đọc sai) và `free`
      (phóng tác, ≤ `[max N syllables]`). Video 2 phút = 1 lượt gọi (trước ~10: tóm tắt + 2 lượt / lô 10 câu). Phần trả
      thiếu câu / JSON hỏng 3 lần → phần đó dịch từng lô như cũ (tóm tắt lười, `faith_prompt` → `express_prompt`).
    - `source_note`: lời gốc lấy tự động có thể sai (đồng âm, đọc nhầm nét, ngắt dòng) → đoán theo ngữ cảnh rồi mới dịch.
      `transcreation_section`: video ngắn lồng tiếng, không dịch từng chữ biệt danh / meme, cô đọng. Giọng văn riêng (xưng
      hô mày–tao, tiếng lóng Việt, ví dụ «đít gắn động cơ») nằm trong lời dặn của mẫu (`templates.REVIEW_STYLE`, mẫu
      «Review phim dọc») — tab Lời thoại ô «Lời dặn cho AI dịch» nhiều dòng (CTkTextbox) + nút «Gợi ý» chèn câu dặn.
    - `syllable_range`: mỗi câu «[min-max syllables]» — tối đa = đọc kịp khung (`syllable_budget`, `_slot`), tối thiểu =
      `MIN_FILL` 70 % thời gian người gốc nói. Chỉ có tối đa thì AI viết ngắn dần về cuối lượt dài (đo: 39 câu một lượt →
      nửa sau giọng lấp 54 % khung, «Nuôi được hông?», khoảng lặng) → phần ≤ 25 câu, phần sau thấy thuật ngữ + 3 câu đã
      dịch ngay trước; lời nhắc «không bỏ ý, câu lặp dịch đủ, câu cuối kỹ như câu đầu». `fit_texts`: câu quá dài / quá
      ngắn sửa CHUNG một lượt (`adjust_prompt`), lỗi thì rút gọn từng câu dài như cũ (`_trim_each`). Câu dịch kết thúc
      «,» (câu gốc Qwen ngắt ở dấu phẩy) → «.». Video mẫu: lấp khung 76 % / 77 %, 5 câu < 60 % (trước 22), 5 lượt gọi.
    - Câu dịch thoát cụt (`_free_broken`: còn dấu cách cuối = cắt ở `"`, hoặc kết thúc lửng bằng là / của / và / để…;
      KHÔNG so độ dài — câu cô đọng đủ ý vẫn dùng) → dịch sát, không ghi bộ nhớ (`weak`). `STYLE_TAG` đổi khi đổi lời
      nhắc (bộ nhớ dịch kiểu cũ không bị dùng lại). Mỗi lượt gọi Gemini ghi log «Gọi Gemini/model (N ký tự)».
    - Flash Lite vẫn hay: dùng sai tiếng lóng («gỡ lịch»), tên loài tiếng Việt sai (钳嘴鹤 «cò mỏ nhện»), không theo hết
      ví dụ trong lời dặn — giới hạn của model nhỏ.

32. **Model dịch DeepSeek** (test `audit_out/qa/test_deepseek.py` 19, máy chủ giả):
    - `Settings.deepseek_api_key`, `uses_deepseek()` / `translate_provider()` / `translate_keys()` (key của model dịch
      đang chọn). Model «deepseek-…» → `Router` gọi `_call_deepseek` (api.deepseek.com/chat/completions, chuẩn OpenAI):
      `thinking: {type: disabled}` (lời nhắc đã dịch sát → phóng tác, suy nghĩ ngầm chỉ tốn token + chậm), JSON mode,
      temperature 1.3 (DeepSeek khuyên cho dịch), log token trúng cache. Model hiện có (api-docs.deepseek.com 10/2026):
      `deepseek-flash`, `deepseek-v4-pro`; thinking mặc định BẬT nên phải tắt rõ.
    - Cache của DeepSeek tính theo phần đầu lời nhắc giống nhau → `whole_prompt` để luật cố định đầu, số câu dời xuống
      «## Lines (N lines)».
    - Lỗi: 402 → «hết số dư», 401 → «key không đúng» (nút «Nhập key…»); 429 = giới hạn tốc độ (không phải hết lượt ngày).
      `check_deepseek_key` hỏi /user/balance (không tốn tiền) khi lưu Cài đặt → báo số dư.
    - Cài đặt › AI dùng API key: thẻ «Model dịch» riêng (Gemini + DeepSeek), thẻ Gemini (key), thẻ DeepSeek (key che,
      Hiện, Lấy key ↗). Kiểm key trước khi chạy / Kiểm tra máy theo nhà cung cấp của model dịch.
    - Khoá bộ nhớ dịch kèm model (`memory_style(..., model)`): đổi sang model khác → dịch mới. `main.py --render --model M`
      thử model mà không ghi Cài đặt. Đo video mẫu (39 câu, 5 lượt): deepseek-flash ≈ $0.0027 / video (768 token
      trúng cache ở phần 2), lấp khung 75 % / 79 %, tự nhiên + tiếng lóng tốt nhất; v4-pro ≈ $0.011, giọng hành chính
      («Cấp quốc gia nhóm II»), lấp 71 % / 73 %. Tên loài hiếm (钳嘴鹤, 大鸨) model nào cũng hay sai → từ điển tên riêng.

33. **Chọn AI dịch + đủ ý** (test `test_deepseek.py` 26, `test_whole.py` 19; đo `audit_out/qa/exp_windows.py`):
    - `Settings.translate_provider` ("Gemini" | "DeepSeek", nút chọn «Dùng để dịch» trong Cài đặt), `translate_model` =
      model Gemini, `deepseek_model` (mặc định deepseek-flash), `active_model()`. Cài đặt cũ có «deepseek-…» trong ô model
      → tự tách sang DeepSeek; nhà cung cấp đã gỡ (Groq…) → Gemini. `--model` dòng lệnh chọn nhà cung cấp theo tên.
    - Đo chia đoạn (39 câu, không bước khớp): DeepSeek flash 25 câu/lượt → âm tiết / tối thiểu 1,34 · 1,43, 0 câu ngắn;
      cả video 1 lượt → 1,06 · 1,03, 4 câu ngắn. Gemini Lite 25 câu → 1,08 · 1,17, 3 câu; 1 lượt → 1,11 · 1,08, 5 câu ⇒
      giữ 25 câu / lượt cho mọi model.
    - `fit_texts` thêm lý do «bỏ ý»: câu phóng tác < `MIN_MEANING` 60 % số âm tiết bản dịch sát (`directs` lưu từ lượt
      dịch) mà còn chỗ trong khung → vào lượt sửa chung, `adjust_prompt` có «FAITHFUL (all the information)»; nhận bản
      sửa chỉ khi đúng chiều (dài → ngắn đi, ngắn / bỏ ý → dài thêm) và ≤ tối đa.

34. **Lượt dùng AI hôm nay** (test `audit_out/qa/test_usage.py` 53, ảnh `audit_out/qa/shot_usage.py`):
    - `app/services/ai_usage.py`: Google không có API hỏi số lượt còn lại → máy tự đếm mỗi lượt gọi THÀNH CÔNG
      (`_call_gemini`, `_call_deepseek` kèm token, `GeminiClient._post` của Dựng truyện) theo model, lưu
      `cache/ai_usage.json` (không lưu key; app và dòng lệnh dùng chung, giao diện đọc lại khi file đổi). Router gặp 429
      hết lượt ngày → `mark_full` (thanh 100 %). Ngày Gemini theo giờ Mỹ Pacific (tự tính giờ mùa hè, không cần tzdata)
      → đặt lại 14:00 (hè) / 15:00 (đông) giờ VN; DeepSeek theo giờ máy. Chưa có file → ước tính từ dòng «Gọi Gemini/…»
      trong nhật ký từ đầu ngày. Hạn mức gói miễn phí: `FREE_PER_DAY` (flash-lite 500, flash 20, 2.5-flash-lite 20, Gemma
      14 400; Pro / model mới = chưa rõ → chỉ đếm). Mọi lỗi đọc / ghi bộ đếm chỉ ghi log.
    - `app/ui/widgets/usage_bar.py` `UsageBar(master, target)`: «37/500 · 7%» + thanh (≥ 80 % vàng, hết lượt đỏ) + «còn N
      lượt · đặt lại lúc 14:00»; DeepSeek: «N lượt · 52K token» + số dư (tự hỏi `/user/balance` ở luồng nền sau khi dùng
      thêm / cũ hơn 10 phút, tối đa 1 lần / phút). Đọc lại bộ đếm mỗi 1,5 s. Ở tab Lời thoại (trên nút «Dịch phụ đề»,
      theo AI đang chọn) và thẻ Gemini (theo model đang chọn ở ô) / DeepSeek trong Cài đặt.
    - Test ẩn: `audit_out/headless/sitecustomize.py` chuyển bộ đếm sang `audit_out/_ai_usage_test/` — test dùng AI giả
      (giả hết lượt ngày, số dư giả) không ghi vào bộ đếm thật.

35. **Giọng ưu tiên Nam / Nữ + đoán giới tính** (test `audit_out/qa/test_voice_priority.py` 39, ảnh `shot_prio.py`; đo
    `audit_out/speaker_gender/`):
    - Đoán giới: `diarize.gender_of(f0, df)` — cao độ < 165 Hz → Nam (không bao giờ lật); ΔF (khoảng cách formant, LPC
      numpy `_dispersion`, ~ độ dài khoang họng) < 1100 Hz → Nam kể cả khi cao độ ≥ 210 Hz (nam nói giọng cao: video
      «tải xuống (2)» 239 Hz / ΔF 1038 trước bị đoán Nữ); ≥ 210 Hz → Nữ; vùng giữa theo ΔF (> 1250 → Nữ) hoặc «chưa rõ».
      Trên 150 giọng mẫu Edge TTS: chưa giọng nữ nào ΔF < 1100. Đã thử và BỎ: phân loại trên vân giọng CAM++ học từ giọng
      Edge TTS (đúng 94 % trên giọng máy nhưng sai ngược trên video thật).
    - `TTSConfig.male_voices` / `female_voices` (lưu theo mẫu, tối đa 4 mỗi giới, khung «Giọng ưu tiên» trong tab Lồng
      tiếng, `app/ui/widgets/voice_priority.py`). `assign_voices`: giọng chọn riêng của video (`speakers.json` › `voices`)
      trước, rồi người nói nhiều nhất nhận giọng 1 của giới mình…; thiếu → `suggest_voices` chọn giọng cùng giới chưa ai
      dùng (`video_voices`, dùng chung lúc dựng và trên thẻ). Thay cho cách cũ «người 1 → giọng 1» không xét giới
      (`resolve_voices` đã bỏ); cấu hình / mẫu cũ (`speaker_voices`) tự chuyển sang ưu tiên theo giới của giọng.
    - Thẻ người nói: nút Nam / Nữ (sửa tay, lưu vào speakers.json), dòng nguồn giọng («Theo giọng ưu tiên «Nam 1»» / «chọn
      riêng cho video này» / «Máy tự chọn»), «Theo ưu tiên» bỏ giọng riêng. Chữ ký giọng gồm giọng ưu tiên + mtime
      speakers.json → đổi là lần chuẩn bị sau đọc lại.

36. **Nhờ AI sửa + câu AI lồng object** (test `audit_out/qa/test_ai_edit.py` 18):
    - Lời dặn: đọc HẾT thoại, sửa mọi câu liên quan (câu hỏi lẫn câu trả lời), rà lại cho nhất quán; chỉ đổi phần được
      yêu cầu, không rút gọn / bỏ ý. Yêu cầu «sửa X thành Y» (`replace_request`): AI trả xong còn câu chứa X → hỏi lại AI
      riêng các câu đó (kèm ví dụ đã sửa); vẫn sót → `replace_phrase` thay trực tiếp (đúng cụm từ, giữ hoa đầu câu).
      Thử thật (flash-lite, video «tải xuống (2)»): trước sửa 11 câu hỏi, sót 11 câu trả lời → nay đủ 11/11 trong 1 lượt.
    - `as_text`: AI đôi khi trả `"text": {"id": 6, "text": "…"}` hoặc viết object thành chữ → bóc lấy câu (ở `_parse`,
      `_parse_items`, `ai_edit`, dịch cả lượt / từng lô, lưới cuối của `translate_cues_vl`). Trước đó câu 36 / 44 của
      video trên bị ghi «{'id': 6, 'text': …}» vào phụ đề và giọng đọc đọc luôn.

37. **Dịch / rà soát bằng AI bên ngoài** (test `audit_out/qa/test_external_ai.py` 22; `app/services/external_ai.py`):
    - Tab Lời thoại › Dịch › «AI bên ngoài»: «Xuất» → SRT lời gốc (để dịch) hoặc song ngữ gốc + bản dịch (để rà soát),
      UTF-8 có BOM (Notepad / web đọc đúng chữ Hán) + lời dặn (giữ số câu + mốc, phong cách của mẫu, từ điển tên riêng)
      chép vào clipboard và lưu `.loi-dan.txt`. «Nhập bản dịch» → từ file hoặc dán SRT từ khung chat.
    - Nhập luôn dùng MỐC THỜI GIAN GỐC: đủ câu → theo thứ tự; thiếu câu mà số thứ tự khớp mốc → theo số; AI gộp / đánh
      số lại → theo mốc thời gian. Bỏ khối ```, bỏ dòng chữ Hán khi AI trả song ngữ, nhận cả danh sách «1. câu». Câu
      thiếu giữ bản dịch cũ (chưa có thì lời gốc) + báo số câu; < 80 % khớp thì hỏi trước khi nhập; bản cũ → `.srt.bak`.
    - Nhờ AI sửa thêm luật: rà lỗi thì chỉ sửa lỗi thật — tiếng lóng / biệt danh đúng nghĩa («ngáo đá», «chó gù») không
      phải lỗi (thử thật: trước flash-lite đổi «chó gù» → «chó ta» sai nghĩa, nay giữ).

38. **Đóng gói**: Windows — `tools/make_installer.py --dist <build> --out <thư mục>` (không đè bộ cài cũ). macOS chip M
    — không build được trên Windows; `build_mac.command` / `tools/build_mac.sh` chạy trên máy Mac (làm như job macos
    của CI: venv riêng, FFmpeg arm64, PyInstaller, LSMinimumSystemVersion, ký ad-hoc, selftest với dữ liệu tạm, DMG →
    `release/mac/`). CI GitHub vẫn bị chặn vì billing (repo riêng tư).

39. **Nhờ AI sửa biết suy luận + nhắn tiếp kiểu chat; nút «Lưu phụ đề»** (test `test_ai_edit.py` 22,
    `test_ai_edit_ui.py` 11; thử thật `audit_out/qa/try_ai_reason.py`):
    - Lời nhắc bắt AI viết `analysis` TRƯỚC `edits` (ghi log «AI phân tích»), luật nhất quán: câu cùng SOURCE đọc giống
      nhau, một người nghe một đại từ (không lẫn bạn / mày), câu hỏi đổi sang X thì câu trả lời theo X.
    - `edit_context(history, changes)`: editor gửi kèm các lượt trước (yêu cầu + trả lời của AI + ví dụ câu đã đổi,
      `CueEditor._ai_history`, quên khi đổi video) và các câu đã đổi chưa lưu — «by the AI» / «by the user by hand»
      (`_ai_after`: chữ AI đặt; người dùng gõ đè → tính là tự sửa). Thử thật trên video «tải xuống (2)»: có ngữ cảnh →
      đồng bộ cả 22 câu hỏi / đáp sang «chó nhà mày»; không ngữ cảnh → còn 11 câu «Bạn mày».
    - «sửa X thành Y» với X là MỘT chữ («bạn»): không thay máy móc nữa (đã ra «Mày mày» từ «Bạn mày» = bạn bè); hỏi lại
      AI chỉ đổi chỗ cùng nghĩa, còn lại báo số câu. Cụm ≥ 2 chữ vẫn thay trực tiếp như cũ.
    - Khung sửa lời thoại: hàng nút riêng [Lưu phụ đề] [Lưu & tạo lại giọng] chia đều (bảng hẹp vẫn đủ chỗ). «Lưu phụ đề»
      = `save(regen=False)`, báo giọng chưa tạo lại.

40. **Tab Lời thoại kiểu chatbot + 3 tab + chống ghi đè lời gốc** (test `test_cue_list.py` 12, `test_external_ai.py` 27,
    `test_ai_edit_ui.py` 11, `smoke_sub_ui.py` 24; ảnh `audit_out/qa/shot_3tab.py`; đo `perf_cue_list.py`):
    - Mục Lời thoại: [Lời thoại | Dịch | Phụ đề]. «Lời thoại» = trang cao đúng khung nhìn (`_fit_text_page`, đo lại khi
      `<Map>` / đổi cỡ) chỉ có `CueEditor`: danh sách TẤT CẢ câu (không chia trang), [Lưu phụ đề][Lưu & tạo lại giọng],
      dòng trạng thái, ô chat «Nhờ AI sửa» neo đáy có lịch sử «Bạn › / AI ›» (CTkTextbox `chat`, quên khi đổi video).
    - Danh sách ẢO: `POOL` = 18 dòng widget + CTkScrollbar, `_first` = câu đầu khung; lăn chuột `WHEEL` = 2 câu
      (bind trên từng dòng, trả "break" để bảng không cuộn theo), `_see_row` cuộn tới câu khuất (đang phát / ↑↓ / AI
      sửa), `_set` chỉ configure phần đổi. Đo: 300 câu hiện 0,14 s, mỗi nấc cuộn ~25 ms (dựng hết 300 dòng CTk: 14 s).
    - Tab «Dịch»: thẻ Dịch (bỏ hàng «AI bên ngoài») + «Tệp phụ đề» mở sẵn: [Xuất SRT ▾: bản dịch / lời gốc / song ngữ /
      chép lời dặn dịch | rà soát] [Nhập bản dịch ▾: từ file / dán SRT từ chat / dịch tay từng câu], [Nạp SRT lời gốc…]
      [Xem / sửa SRT…], [Mở bằng app khác] [Xoá…]. Xuất chỉ ghi SRT (không còn tự chép lời dặn + .txt — người dùng nhầm).
    - Sự cố thật 12/10 13:52: lời gốc tiếng Trung của «tải xuống (2)» bị ghi đè bằng bản tiếng Việt (dán bản dịch vào hộp
      «Xem / Sửa SRT» — mặc định lưu vào «Gốc», không .bak). Nay: `import_translation_text` dùng chung; `looks_translated`
      (lời gốc ≥ 50 % câu chữ Hán, nội dung mới ≤ 10 %) → «Nạp SRT lời gốc» và hộp Xem / sửa SRT hỏi «nhập làm bản dịch
      (giữ mốc gốc) / vẫn thay lời gốc»; đè lời gốc luôn giữ `.srt.bak`. Bản dựng lại lời gốc của video đó:
      `audit_out/khoi_phuc/tải xuống (2).loi-goc-tieng-trung.srt`.

41. **Lời thoại tự lưu + thanh icon** (test `test_ai_edit_ui.py` 23, `test_qa_m.py` cập nhật):
    - Bỏ hàng [Lưu phụ đề][Lưu & tạo lại giọng]. Chữ TỰ LƯU: ngừng gõ `AUTOSAVE_MS` = 1 s, rời ô (250 ms), AI sửa /
      ↻ dịch lại / ↶ / Hoàn tác / Tìm & thay (300 ms), đổi video / đóng app (`_flush_autosave`) — `save(regen=False,
      quiet=True)` giữ tô vàng + ↶ của câu AI sửa tới khi đổi video. Đóng app không còn hỏi «lưu trước khi thoát?».
      Ngữ cảnh Nhờ AI sửa tính từ `_base_texts` (lúc mở video), không từ lần lưu cuối.
    - Thanh icon cạnh «Bản dịch · N câu»: copy (menu: lời gốc + lời dặn dịch / song ngữ + lời dặn rà soát / chỉ SRT
      đang sửa — lời dặn `inline=True` + khối ```srt, dán thẳng vào chat AI), dán (`on_import` → `import_translation_text`,
      giữ mốc gốc), tìm & thay, loa (`regen_voice`: tạo lại giọng; sáng màu khi giọng chưa khớp chữ — `_voice_state`).
    - Dòng trạng thái tự ẩn khi trống (`_AutoHideStatus`). Icon «copy / paste / search»: Segoe E8C8 / E77F / E721; font
      đi kèm macOS chưa có → mượn icon tài liệu / bảng / kính lúp.

42. **«Không dịch — tự dịch bên ngoài»** (đã thay bằng «Chạy đến: Đến lời gốc» — mục 45):
    - `Settings.external_translate` (Cài đặt › AI dùng API key › «Cách dịch»: AI tự dịch / Không dịch). Bật: Chuẩn bị /
      Xuất nhận diện lời gốc (+ người nói) rồi raise `WaitTranslation` → trạng thái hàng đợi «Chờ bản dịch» (màu vàng,
      không tính lỗi, có trong câu tổng kết). `needs()` trả «external» (không đòi key dịch), `planned_steps` bỏ bước dịch.
      «Dịch phụ đề» bấm tay vẫn được (hỏi lại). Nhờ AI sửa vẫn dùng AI đang chọn.
    - Trước đây cần bản dịch mà tắt «Tự dịch» → «dùng phụ đề gốc»: giọng Việt đọc chữ Trung. Chế độ mới dừng thay vì đọc sai.
    - Tab Lời thoại: tiêu đề «Lời gốc · N câu — chưa dịch: icon copy → dịch ngoài → icon dán»; tab Dịch nhắc cách làm.

43. **Hồ sơ kịch bản** (test `audit_out/qa/test_brief.py` 11, `test_whole.py` mục 8):
    - Người nói TRƯỚC khi dịch: `Pipeline.translate` gọi `ensure_speakers` khi bật lồng tiếng nhiều giọng; `speaker_labels`
      gửi nhãn «Người 1 · Nam» từng câu → dòng lời nhắc `n. [lo-hi syllables] [Người 1 · Nam] …`.
    - `make_brief` (thêm 1 lượt AI / video, chỉ khi chưa có hồ sơ): JSON nội dung, giọng điệu, người nói + xưng hô, tên
      riêng, bản chốt cho câu lặp → `render_brief` thành chữ đọc được, lưu `SUBS_DIR/{stem}_{hash}.brief.txt`
      (`on_brief` → `save_brief`). Hồ sơ đi vào mọi phần dịch (`whole_prompt` «## Script brief»), khớp độ dài / dịch sát
      (`style_b`), ↻ dịch lại (`retranslate` nối vào style) và Nhờ AI sửa (`_edit_prompt(brief=)`).
    - `unify_repeats`: câu gốc lặp (so `_norm_src`) dùng đúng một bản dịch — bản hồ sơ chốt, không có thì bản dùng nhiều
      nhất — trước bước khớp độ dài. `parse_brief` đọc lại hồ sơ người dùng sửa (tên riêng + câu lặp).
    - Tab Dịch › «Hồ sơ kịch bản»: xem / sửa (rời ô là lưu), xoá trắng = lần dịch sau AI soạn lại. Nhận diện lại / nạp SRT
      lời gốc khác / «Xoá phụ đề & giọng» → bỏ hồ sơ. `STYLE_TAG` = `|vl-hoso1` (bộ nhớ dịch cũ không dùng lại).
    - Câu lặp sau bước khớp độ dài: `keep_repeats` — chỉ số ít chỗ bị rút / nới (khung bất thường) → trả về bản chung
      (giọng đọc nhanh / chậm hơn ở chỗ đó); đa số chỗ bị rút → mọi chỗ dùng bản ngắn nhất. Khung số âm tiết của câu lặp
      trong lời nhắc hồ sơ = khung giữa của nhóm (không lấy khung ngắn nhất).
    - Video «tải xuống»: câu 27 bị cắt còn 0,6 s vì bộ căn mốc để 2 s trống giả rồi dồn 8 chữ 0,04 s/chữ — ngưỡng «căn mốc
      sụp» `COLLAPSED` 0,03 → 0,05 s/chữ (bộ căn mốc chia khung 0,08 s; quét các kết quả nhận diện có sẵn: chỉ bắt đúng
      chỗ này). Có hiệu lực khi nhận diện lại.

44. **Căn mốc 2 lượt** (`qwen_asr_worker.py`; test `test_brief.py` 23; thí nghiệm `audit_out/repeat_fit/`):
    - Lượt 1 căn cả cửa sổ (≤ 180 s) như cũ → `chunk_spans` chia đoạn ≤ `CHUNK_SECONDS` = 20 s, chỉ cắt ở cuối câu chỗ
      lượt 1 tin được (hai chữ quanh không dồn, nghỉ < 1 s), tại khung 20 ms lặng nhất → `realign` căn lại từng đoạn;
      đoạn lỗi / ra khác chữ / dồn nhiều hơn lượt 1 → giữ lượt 1. `repair_collapsed` vẫn chạy sau cùng làm lưới an toàn.
      JSON có thêm `chunks` / `realigned`, log «Căn mốc lượt 2: k/n đoạn».
    - Đo «tải xuống» (16 câu hỏi lặp, mốc thật từ giọng tách): 1 lượt cũ lệch TB 0,24 s / lớn nhất 1,44 s; 1 lượt + ngưỡng
      mới 0,16 / 0,52 s; 2 lượt 0,08 / 0,19 s, không còn vùng dồn. «tải xuống (2)»: lượt 1 có 3 vùng dồn, 2 lượt 0; số câu
      giữ nguyên. Tốn thêm ~0,5 s / 100 s âm thanh. Video đã nhận diện phải bấm Nhận diện lời thoại lại.

45. **«Chạy đến» + luôn duyệt** (test `test_external_mode.py` 29, `test_qa_m` / `test_qa_p3` cập nhật; ảnh
    `audit_out/qa/shot_until.py`):
    - Bỏ công tắc «Duyệt trước» (trên cùng + tab Video): luôn Chuẩn bị → Duyệt → Xuất; `ui["review_mode"]` cũ bỏ qua,
      tab Duyệt luôn có. `set_review_mode` / `review_mode` / `layout_review` đã gỡ.
    - Ô «Chạy đến» (`Select`, cạnh nút chính) = `Settings.prepare_until` (nhớ chung, settings.json): `all` Tự động hết ·
      `source` Đến lời gốc (thay radio «Cách dịch» trong Cài đặt — `external_translate` cũ tự chuyển) · `translation`
      Đến bản dịch: AI dịch xong `WaitReview` → «Chờ soát bản dịch» (chưa tạo giọng). `PrepareStop` (gốc của
      `WaitTranslation` / `WaitReview`) mang `status`; `WAITING` = hai trạng thái chờ.
    - Bấm lại chạy tiếp từ chỗ dừng (bước đã có kết quả bỏ qua — dừng ở bản dịch chỉ khi lượt đó vừa dịch). Nút chính
      «Tiếp tục (n)» khi mọi video còn lại đang chờ bạn; kết quả cuối lượt có nút «Mở Lời thoại» (`open_text_of`).
      `planned_steps` không tính bước giọng / khử giọng khi sẽ dừng; xuất (render) không dừng ở bản dịch; nút tạo lại
      giọng ở Lời thoại gọi `prepare(stops=False)`.
    - Tab Duyệt: video đang chờ hiện «⏸ Chờ … · làm tiếp ở tab Lời thoại rồi bấm Tiếp tục», bộ đếm «Chờ bạn làm tiếp n».
      Ngưỡng thu gọn ô Mẫu 1250 → 1320 (ô «Chạy đến» rộng hơn công tắc cũ ~40 px).

46. **Câu cần nghe lại (B1 — nền cho agent sau này)** (`app/services/line_qa.py`; test `test_line_qa.py` 29; ảnh
    `audit_out/qa/shot_qa.py`):
    - `line_qa.review_flags` đọc file có sẵn, không gọi AI: báo cáo từng câu của track giọng (`.cues.csv` cạnh WAV:
      `loi_qc` sau khi đọc lại, `du_phong`, `tempo`, `tran`) — chỉ khi chữ `chu` khớp chữ đang thấy; bản dịch còn chữ
      Hán / Kana / Hangul, y nguyên lời gốc, trống; xưng hô lệch hồ sơ (`brief_pronouns` đọc «xưng «…», gọi người nghe
      «…»» theo người nói, nhãn người nói từng câu; «bạn mày», «bạn bè», «cậu ấy» là danh từ). Đọc nhanh khi ×≥ max(1,2,
      tốc độ đặt × 1,08); tràn > 0,15 s.
    - `Pipeline.review_flags(item, texts=None)`; cuối Chuẩn bị ghi «n câu cần nghe lại: …» vào nhật ký.
    - Tab Lời thoại: dấu ⚠ + lý do ngắn trên dòng câu (tooltip lý do đủ), nút ⚠ n lọc (`_view`: danh sách ảo theo chỉ số
      câu đang hiện — `_range` / `_row_index` / `_pos` / `_move` / cuộn theo `_view`). Lưu / tạo lại giọng / sửa hồ sơ
      → soát lại; đang lọc thì câu vừa sửa xong vẫn ở danh sách (hết ⚠). Đổi video → bỏ lọc.
    - Tab Duyệt: dòng «Câu cần nghe lại» (`QA_TITLE`, WARN, liệt kê số câu); «Sửa ›» → `edit_in` bật sẵn lọc. Dòng
      giọng không còn nhắc trùng «câu đọc chưa đạt / tràn khung».

47. **Gọn giao diện (đợt 1 audit `audit_out/audit_gon/BAO_CAO.md`)** (test `test_gon.py` 24; ảnh `shot_gon.py`):
    - Thanh bên: Video · Duyệt · Lời thoại · Lồng tiếng · Cắt · Nhạc · Logo · Hiệu ứng · «Thêm» (`MORE` = Che mờ, Truyện —
      menu `Popover`, nút sáng khi đang mở mục đó; vẫn mở bằng F8 / F9). `PREWARM` bỏ mục trong `MORE`.
    - Tab «Nhận diện» gộp vào Lời thoại › «Nguồn & dịch» (`widgets/source_panel.py`: ngôn ngữ video, bộ nghe Qwen, nhận
      diện lại, từ file khác — `show_module("stt")` chuyển tới đây). `module_stt.py` không còn dùng (chờ người dùng đồng ý
      mới xoá).
    - Bỏ công tắc «Tự nhận diện khi chuẩn bị» / «Tự dịch khi chuẩn bị»: Chuẩn bị luôn dịch khi cần (`translate_enable` bỏ
      qua; dừng ở đâu do «Chạy đến»); mẫu «Chọn file SRT» → `sidecar_srt` (không có SRT cạnh video thì tự nhận diện);
      giọng có sẵn (khoá) thì lồng tiếng không cần bản dịch. «Đến lời gốc» không nhờ AI rút gọn câu tràn.
    - Tab Video: bỏ Tỉ lệ / Cách vừa khung / Độ phân giải / Số hình (đã có trên thanh khung xem trước); khối «Khung hình»
      = Phóng to, **Canh ngang / Canh dọc** (`VideoConfig.offset_x/offset_y` −50…50: nền mờ / viền đen dời nội dung, cắt
      vừa khung chọn phần giữ, phóng to chọn vùng — `render_pipeline._offsets/_place/_zoom_origin`, đo bằng ffmpeg thật),
      Tốc độ, Lật. Khối «Âm thanh» chuyển sang tab Lồng tiếng (`widgets/audio_section.py`, ghi cấu hình `video`).
    - Tab Phụ đề: công tắc «Che chữ cứng có sẵn (tự dò)» (`blur.enable` + `auto_detect`).

48. **Nhẹ hơn khi cài (đợt 2)**: Whisper (kiểm giọng đọc, tách đoạn đọc gộp) dùng chung cuBLAS 12 / cuDNN 9 của bộ nghe
    Qwen (`whisper_stt._share_qwen_cuda`: nạp từng DLL theo đường dẫn đầy đủ, KHÔNG thêm `torch\lib` vào đường tìm DLL —
    trong đó có `libiomp5md.dll` của torch → xung đột OpenMP với Paddle). Thử thật (`audit_out/repeat_fit/
    cuda_share_probe.py`): giấu CUDA khác, large-v3-turbo chạy GPU 10 s / 0,76 s, PaddleOCR cùng tiến trình ổn.
    `make_installer.py` mặc định chỉ bản Lite (`--full` / `--both` khi cần). OCR thưa hơn (4 / 3 khung/giây) đo được
    nhanh hơn nhưng sửa sai tên riêng (猞→舍) → giữ 10 khung/giây (`audit_out/repeat_fit/ocr_fps.json`).

49. **CLI + MCP + agent DeepSeek (đợt 3)** (`app/agent/`; test `test_agent.py` 24):
    - App đang mở là chủ duy nhất của hàng đợi: `MainWindow._start_control` mở `ControlServer` (127.0.0.1, cổng ngẫu
      nhiên, mã bí mật, ghi `output/control.json`; biến `EDIT_STUDIO_CONTROL_FILE` để test trỏ chỗ khác — sitecustomize
      của test ẩn đã đặt). `control.call` gửi lệnh; app chưa mở → tự mở (`launch_app`) rồi chờ; app bản cũ đang mở
      (khoá «chỉ một cửa sổ» có mà không có cổng) → báo khởi động lại, không mở thêm.
    - 5 lệnh thô (`ops.py`, chạy trong app qua `GuiHost.call` = luồng Tk): `videos(add, preset, limit)` · `run(ids,
      action=prepare|render|wait, until, wait_s)` · `review(id, all, limit, offset)` (chỉ câu ⚠ + `people` = cặp xưng
      hô của hồ sơ) · `fix(id, text{n: câu}, request, voice, wait_s)` (request → `ai_edit` kèm hồ sơ; voice → prepare
      stops=False) · `settings(template, ratio, fit, offset_x/y, zoom, target, style, until)`. Id = sha1(đường dẫn)[:6].
      Lỗi trả `{"error"}` một dòng; không đường dẫn / log / cả SRT.
    - `_run_queue(quiet=True)` + `preflight_problem`: không bật hộp thoại (thiếu key / Qwen, ổ đầy, đang bận) — trả lý do.
    - Mô tả lệnh `schema.py` (~650 token, tiếng Anh); MCP stdio tự viết (`python -m app.agent mcp`: initialize,
      tools/list, tools/call, ping); CLI `python -m app.agent <lệnh> '<json>'`; `tools/agent_deepseek.py` vòng lặp
      function-calling (key: DEEPSEEK_API_KEY / .env / key trong app, không in ra).
    - Bản .exe là app cửa sổ (không stdout) → MCP / CLI chạy bằng python mã nguồn; muốn dùng với bản cài cần thêm một exe
      console riêng (chưa làm).

50. **Duyệt / xuất video đang dừng mà đã đủ lời thoại** (test `test_resume_export.py` 9; lỗi người dùng 12/10 22:24:
    «Đến lời gốc» → dán bản dịch → lượt Chuẩn bị bị Dừng → «Đã huỷ» → tạo giọng ở tab Lồng tiếng → không Duyệt / Xuất được):
    - `Pipeline.ready_for_review`: không còn thiếu lời gốc / bản dịch (`needs` không có qwen / gemini / external) — giọng,
      khử giọng gốc thiếu thì bước xuất tự làm. Tab Duyệt `_ready` dùng nó cho video «Đã huỷ» / dừng chờ bạn (đang chạy,
      Lỗi vẫn khoá) + dòng «lời thoại đã đủ — Duyệt được»; agent `run(render)` cũng vậy.
    - Nút «Tạo giọng cho video này» (tab Lồng tiếng): xong mà đủ lời thoại → «Chờ duyệt» (trước: giữ trạng thái cũ).
    - Nút chính: video «Chờ bản dịch» chưa dán bản dịch (`_needs_paste`) không che «Xuất (n)»; bấm Xuất thì xuất video đã
      duyệt, không hỏi.

51. **Dung lượng file xuất** (test `test_size.py` 17; người dùng: video vào 52 MB, xuất ra 180 MB):
    - Nguyên nhân: NVENC `-rc vbr -cq 23 -b:v 0` không trần → ~11,6 Mbps (video tải về ~3 Mbps). Đo VMAF
      (`audit_out/bitrate/probe_bitrate.py`): trần H.264 6 Mbps → ~95 MB, VMAF 98,5 so với bản không trần; HEVC 3 Mbps → ~49 MB,
      VMAF 95,3.
    - `VideoConfig.size_mode` (`SIZE_MODES`): `social` (mặc định) = giữ CQ nhưng `-maxrate/-bufsize` theo cạnh ngắn khung ra
      (720p 3,5 · 1080p 6 · 1440p 10 · 4K 20 Mbps; > 31 khung/giây ×1,5; H.265 ×½, AV1 ×0,45) + tiếng 128k; `small` = luôn
      H.265, trần ½; `max` = như cũ (không trần, tiếng 192k). `render_pipeline.bitrate_cap` / `audio_bitrate`.
    - `pick_encoder(cap=)`: NVENC / CPU thêm trần; QSV (ICQ) và AMF (CQP) không nhận trần → có trần thì chạy VBR
      (`-b:v` 0,8 × trần); VideoToolbox có trần → `-b:v`. Nhật ký xuất ghi «Dung lượng file: trần hình … · tiếng …».
    - Tab Video: khối «Dung lượng file xuất» (sau Khung hình) — «Xuất để» + ước lượng MB mỗi phút; agent `settings(size)`.
    - Trần theo cả video GỐC (`bitrate_cap(source=)`): gốc nhỏ hơn khung ra (576p phóng lên 1080p) thì tính theo cạnh
      ngắn của gốc — đo video chó 576×1024 → 1080×1920 (`audit_out/bitrate/probe_lowres.py`): 3,5 Mbps VMAF 95,8 so với
      bản 6 Mbps, 40 so với 71 MB (2,5 Mbps: VMAF 90,8 — thấp quá). Nhật ký ghi «— theo video gốc 576p».

52. **Tự sửa câu lỗi (A–D)** (test `test_autofix.py` 34; người dùng: «tự chỉnh / phụ đề lỗi thì tool có tự fix không»):
    - `line_qa.FitModel` / `fit_model`: khung từng câu như lúc tạo giọng (`khung` trong `.cues.csv`, chưa có thì tới câu
      sau trừ `min_gap`), tốc độ đọc đo từ track giọng của chính video (trung vị ký tự / giây ở ×1; câu đã đọc mà chữ chưa
      đổi dùng tốc độ riêng của câu — câu nhiều dấu phẩy đọc chậm hơn ~20 %), «sàn» câu ngắn, `too_short` (khung 0,35 s
      một chữ cũng không kịp). `TEMPO_CAP` = `generator.MAX_TOTAL_TEMPO` (không nạp generator: kéo numpy ~1 s).
    - C: câu chưa đọc theo chữ đang thấy (vừa sửa) → ⚠ «dài ~+0,6s» / «sẽ đọc nhanh» (ước lượng); tab Lời thoại soát lại
      sau mỗi lần ngừng gõ 0,3 s bằng `Pipeline.qa_context` đã đọc sẵn (không đọc file).
    - A: ✂ trên dòng câu dài rút gọn được → `Pipeline.auto_fix(only=[i], shorten_only=True)`. D: nút «Tự sửa» (cạnh ⚠) →
      `auto_fix`: `line_qa.fix_tasks` (rút gọn có giới hạn ký tự · dịch nốt · xưng hô theo hồ sơ · đổi cách nói câu giọng
      đọc sai CHỮ; lỗi âm thanh như «vào đột ngột» bỏ qua; khung quá ngắn báo riêng) → `translate_router.fix_lines` (một
      lời nhắc cho mọi câu, mỗi câu đúng việc, giữ chữ người dùng; bản «rút gọn» không ngắn hơn / cắt cụt → bỏ). Câu sửa tô
      vàng, ↶ trả lại, tự lưu. Agent: `fix(id, auto=true)` (trả `too_short`).
    - B: ↻ gửi giới hạn ký tự theo khung giọng thật (`Pipeline.line_budgets`) thay vì thời lượng phụ đề.
    - Sửa lỗi cũ: ↶ một câu không trả lại được (kiểm «đã đổi» đọc ô chưa vẽ lại → ghi đè chữ AI lên).
    - Thử thật (video chó, Gemini): lời nhắc đầu AI đổi tiếng lóng người dùng tự viết («xả xì trét của ông tướng này» →
      «xả stress của nó») và viết sai «kiểng cợ» → lời nhắc giữ nguyên tiếng lóng / cách viết vui, rút bằng cách bỏ chữ
      khác, mọi chữ đúng chính tả (thử lại: giữ «max ping», «õng ẹo», «xì trét»).
    - Agent: lệnh tới lúc app vừa mở (Tk chưa vào vòng chạy, «main thread is not in main loop») → `GuiHost.call` đợi
      tới 30 s thay vì báo lỗi.

53. **Lồng tiếng tự nhiên — ngầm, không công tắc** (test `test_pacing.py` 23; người dùng: câu quá nhanh / câu bình thường
    khó chịu, video bị khoảng trống). Đo video chó (`audit_out/export_3/pacing_probe.py`): 26/31 câu cùng hệ số ×1,1 nhưng
    tốc độ nói thật 4,7–6,9 âm tiết / giây (VieNeu tự đọc câu cảm thán / câu ngắn nhanh hơn tới 23 %); lặng 0,6–1,1 s ngay
    sau dấu phẩy; lặng dài sau câu = giọng xong sớm (câu 11, 14) HOẶC video gốc tự ngừng nói (câu 22, 24, 26 — khoe chó);
    độ to các câu đều (lệch 0,4 dB) — không cần sửa.
    - Ghép giọng (`generator`): nhịp đều `EVEN_BAND` — câu nói nhanh hơn trung vị của video > 7 % đọc chậm lại (≤ 12 %),
      câu chậm giữ nguyên (chậm thường do dấu phẩy); vế sau của cùng một câu (`_ENDS` không khớp câu trước) vào sớm tới
      `PULL_JOIN` 0,8 s — đọc liền; chỗ của câu (`room_for`) tính từ lúc thật sự bắt đầu (thay `slot_for` + đuổi kịp
      `CATCH_UP_LATE`); báo cáo thêm `phu_de_het`, `khung` = chỗ thật. Chữ ký track `v3` → ghép lại (câu đã đọc dùng lại).
      Đo lại: câu nhanh bất thường 2 → 0, lặng giữa câu dài nhất 1,10 → 0,54 s, tràn 1 → 0.
    - Chuẩn bị: một lượt AI chỉnh độ dài (`fix_lines`, thay `retranslate(SHORTEN_REQUEST)`) — rút gọn câu tràn + nói đầy
      đủ hơn câu xong sớm (`line_qa.loose_lines`: xong trước khi người nói gốc hết câu ≥ 0,6 s, chừa 0,45 s nghỉ, ≤ +40 %);
      bỏ câu người dùng sửa / đã chỉnh (`locks`), «Đến lời gốc». ⚠ «xong sớm» + «Tự sửa» (việc `long`) cho câu bị khoá.
    - Âm gốc dâng ở chỗ không có giọng ≥ `DUCK_HOLD` (`render_pipeline.orig_swell`: ×3 mức đặt, tối đa 25 %; 0 % / tắt tự
      hạ nền thì không) — đường bao riêng của âm gốc (`duck_envelope(swell=)`: hệ số ≤ 1, âm gốc đặt to ×swell), nhạc nền
      giữ đường bao thường. Bản render + xem trước (`live_preview`).

## Còn tồn / để ý khi làm tiếp

- **Phông Việt cũ VNI / TCVN3** trong file SRT chưa tự chuyển sang Unicode (cần Unikey Toolkit).
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
- **Bài test tự động (local, trong `audit_out/`, không commit):** `audit_out/qa/test_qa_*.py`, `audit_out/b_only/smoke_b_ui.py`,
  `audit_out/ux_sub/smoke_sub_ui.py`, `audit_out/review/smoke_retry_model.py` — thêm tham số `ctk52` để chạy với CustomTkinter 5.2.
- **Chạy test ẨN** (không bật cửa sổ, không giành chuột / bàn phím): đặt `PYTHONPATH=audit_out\headless` (file
  `sitecustomize.py` làm mọi cửa sổ Tk trong suốt, ngoài màn hình, không lên thanh tác vụ). Bài test trỏ cấu hình làm việc
  sang `_test_last_config.json` — không ghi `output/last_config.json` thật.
