"""Nội dung Hướng dẫn sử dụng — viết đơn giản, câu ngắn, ai đọc cũng làm theo được.

Mỗi chủ đề: ``key`` (trùng key module để mở đúng trang), ``title``, ``group``, ``blocks``:
    ("p", "đoạn văn")                 ("h", "tiêu đề nhỏ")
    ("steps", ["bước 1", "bước 2"])   ("tip", "mẹo")   ("warn", "lưu ý")
    ("kv", [("Tên nút", "nó làm gì"), …])   ("shortcuts", None)
"""

from __future__ import annotations

TOPICS: list[dict] = [
    # ══ BẮT ĐẦU ══════════════════════════════════════════════════════════
    {"key": "start", "title": "Bắt đầu: làm video đầu tiên", "group": "Bắt đầu", "blocks": [
        ("p", "Phần mềm này giúp bạn làm video mà không cần biết dựng phim. Bạn chỉ cần đưa video vào, bật thứ mình muốn, "
              "rồi bấm một nút. Máy tự làm phần còn lại."),
        ("h", "Làm theo 3 bước"),
        ("steps", [
            "Đưa video vào: kéo file video thả vào ô đen to ở giữa màn hình. Hoặc bấm nút xanh «＋ Nạp video» ở góc trên bên trái.",
            "Chọn thứ bạn muốn làm ở cột bên trái (Phụ đề, Lồng tiếng, Che mờ, Nhạc…). Bật cái nào thì màn hình giữa hiện ngay cái đó.",
            "Bấm nút xanh «Render MP4» ở góc trên bên phải. Thanh trạng thái dưới cùng cho biết đang làm bước nào, bao nhiêu % và còn bao lâu. Xong thì bấm «Mở thư mục» ngay trên thanh đó để lấy video.",
        ]),
        ("h", "Mấy nút trên màn hình giữa"),
        ("kv", [
            ("Các ô nhỏ 16:9 · Gốc · Gốc · Cắt vừa khung", "Bấm vào để chọn khung video: 16:9 là video ngang (YouTube), 9:16 là video dọc (TikTok)."),
            ("V · T · B", "V: kéo hình đi. T: kéo ô chữ phụ đề. B: vẽ ô che mờ."),
            ("Sub · Blur", "Tạm giấu chữ / ô che để nhìn hình cho rõ. Không ảnh hưởng video xuất ra."),
            ("Phát", "Xem thử giống video xuất: đủ khung hình, màu, che mờ, logo, chữ, phụ đề; tiếng gồm âm gốc + giọng lồng "
                     "+ nhạc nền; bỏ qua đoạn đã cắt, đúng tốc độ. Đang phát mà chỉnh gì thì hình/tiếng đổi theo ngay."),
            ("Hiệu ứng", "Tắt khi máy yếu, phát bị giật: lúc đó chỉ phát hình gốc, phụ đề vẽ đè (tiếng vẫn đủ)."),
            ("Render thử", "Render thật 15 giây từ chỗ đang xem rồi phát luôn — giống hệt file xuất. Chưa có giọng lồng thì "
                           "máy tự tạo giọng cho đoạn đó. Dùng để nghiệm thu trước khi render cả video."),
            ("Xóa · phím Delete", "Xoá ô che hoặc ô chữ đang chọn. Lỡ tay thì bấm Ctrl+Z để lấy lại."),
        ]),
        ("tip", "Bạn không cần bấm lưu. Mọi thứ tự lưu, tắt máy mở lại vẫn còn."),
        ("h", "Mẫu: làm hàng loạt video giống nhau"),
        ("steps", [
            "Chọn mẫu có sẵn ở ô «Mẫu» trên cùng (Review phim dọc 9:16, Thuyết minh ngang 16:9, Reup nhanh không lời…), "
            "hoặc tự chỉnh một video cho ưng ý rồi bấm «Lưu mẫu» (chọn lưu những phần nào, kèm ảnh đại diện).",
            "Giọng lồng tiếng: mẫu nhớ giọng chính + danh sách giọng theo thứ tự người nói (Người 1 → giọng 1, Người 2 → "
            "giọng 2…). Video sau nhận ra bao nhiêu người thì gán lần lượt; dư người thì tự chọn thêm giọng Nam/Nữ. "
            "Muốn một người đọc hết thì tắt «Mỗi người nói một giọng» ở tab TTS.",
            "Nạp nhiều video / cả thư mục vào hàng đợi — mọi video dùng chung mẫu đang chọn.",
            "Video nào cần khác (vd. chữ cứng nằm chỗ khác): chọn video đó, ở mục Video bật «Chỉnh riêng cho video này» "
            "hoặc «Áp mẫu riêng». Video đó có nhãn «✎ Riêng» trong hàng đợi.",
            "Bấm «Render MP4»: mỗi video dựng đúng theo mẫu / cấu hình riêng của nó.",
            "Nút ☰ cạnh «Lưu mẫu»: xem tất cả mẫu, áp cho tất cả / chỉ video đang chọn, xuất file mẫu gửi máy khác, nhập mẫu.",
        ]),
        ("h", "Duyệt trước khi xuất (Chuẩn bị → Duyệt → Xuất)"),
        ("steps", [
            "Mục Video bật «Duyệt trước khi xuất». Nút Render trên cùng đổi thành «Chuẩn bị (n)».",
            "Bấm «Chuẩn bị»: máy làm phụ đề, dịch, nhận diện người nói, tạo giọng lồng cho cả hàng đợi (chưa render). "
            "Xong tự mở tab «Duyệt» (thanh bên trái) — khung xem trước vẫn ở ngay cạnh.",
            "Tab Duyệt: ‹ › hoặc ô chọn để đổi video. Bấm vào ô câu nào thì khung xem trước nhảy tới câu đó; ▶ phát đúng "
            "câu đó có giọng lồng; đang phát thì câu đang đọc sáng lên. Sửa câu sai → «Lưu & tạo lại giọng» (câu không đổi dùng lại, rất nhanh).",
            "Đạt thì «Duyệt» (Ctrl+Enter, tự sang video chờ kế), không dùng thì «Bỏ qua».",
            "Bấm «Xuất (n)» trên cùng: chỉ render video đã duyệt, dùng lại phụ đề / giọng đã chuẩn bị.",
        ]),
        ("h", "Tự động hoá thêm"),
        ("kv", [
            ("Tự dò chữ cứng", "Mục Che mờ → «Tự dò & che chữ cứng cho từng video»: máy tự tìm dải phụ đề in sẵn của mỗi video và "
                               "che mờ (bỏ qua logo / watermark đứng yên). Mẫu Review phim / Thuyết minh bật sẵn."),
            ("Thư mục theo dõi", "Mục Video → «Thư mục theo dõi»: chép video vào thư mục là tự vào hàng đợi, áp mẫu đã chọn, "
                                 "rồi tuỳ chọn tự chuẩn bị (chờ duyệt) hoặc tự xuất luôn."),
            ("Tên file xuất", "Mục Video → «Tên file xuất» / «Thư mục con» với mã {ten} {hauto} {mau} {ngay} {gio} {stt}, "
                              "vd. {stt}_{ten} trong thư mục {mau}/{ngay}. Lưu vào mẫu được."),
        ]),
    ]},
    {"key": "flow_dub", "title": "Video tiếng nước ngoài → tiếng Việt", "group": "Bắt đầu", "blocks": [
        ("p", "Bạn có video tiếng Trung, tiếng Anh… muốn có chữ Việt và giọng đọc tiếng Việt. Làm theo thứ tự này, "
              "mỗi bước bấm ở một mục bên cột trái."),
        ("steps", [
            "Mục Video: đưa video vào.",
            "Mục STT: bấm «Tạo SRT từ Video trong hàng đợi». Máy nghe video rồi ghi ra chữ (tiếng gốc). Chờ vài chục giây. "
            "Máy nào có card NVIDIA thì chọn Model = large-v3-turbo cho chữ đúng nhất.",
            "Mục SRT: bật «Dịch AI», chọn «Tiếng Việt», bấm «Dịch». Cần có API key Gemini (miễn phí, lấy tại aistudio.google.com, "
            "dán vào Cài đặt). Muốn sửa câu nào thì mở tab «Lời thoại».",
            "Mục Phụ đề: chọn «Sub đã dịch». Chữ Việt hiện trên video. Muốn dời chữ thì bấm T rồi kéo ô chữ.",
            "Mục TTS: bật «Thêm lồng tiếng vào video», chọn giọng, bấm «Nghe thử giọng» xem có thích không. Để «Khớp mềm» bật.",
            "Mục Che mờ (nếu video có chữ tiếng nước ngoài dính sẵn trên hình): bấm B rồi vẽ một ô đè lên dòng chữ đó.",
            "Mục Video › Âm thanh: bật «Khử giọng nói gốc (AI)» (bỏ giọng Trung/Anh, giữ nhạc nền & tiếng động), «Âm gốc / nền» "
            "khoảng 70, «Lồng tiếng» khoảng 150. Rồi bấm «Render MP4».",
        ]),
        ("warn", "Câu tiếng Việt thường dài hơn câu gốc, nên giọng đọc phải nhanh hơn một chút. Máy tự chỉnh. "
                 "Nếu cuối video bị mất tiếng: tab Lời thoại, ô «Phong cách dịch» chọn «Ngắn gọn hơn», bấm «Dịch lại»."),
    ]},
    {"key": "flow_short", "title": "Video ngang → video dọc TikTok", "group": "Bắt đầu", "blocks": [
        ("steps", [
            "Đưa video vào.",
            "Trên màn hình giữa, bấm ô đầu tiên (đang ghi 16:9 hoặc Gốc) → chọn «9:16 (Dọc)».",
            "Bấm ô «Cắt vừa khung» → chọn «Nền mờ (blur)». Video sẽ nằm giữa, trên dưới là hình mờ. (Chọn «Cắt vừa khung» nếu muốn phóng to cắt hai bên.)",
            "Muốn cắt thành nhiều clip ngắn: mục Cắt → «＋ Thêm đoạn», gõ giây bắt đầu và kết thúc, chọn «Xuất mỗi đoạn thành 1 file».",
            "Bấm «Render MP4».",
        ]),
        ("tip", "Mục Logo → bật «Watermark» và «Vùng an toàn TikTok» để chữ không bị nút like/comment của TikTok che mất."),
    ]},

    # ══ TỪNG MỤC ═════════════════════════════════════════════════════════
    {"key": "video", "title": "Video: đưa file vào và chọn khung", "group": "Từng mục", "blocks": [
        ("h", "Để làm gì?"),
        ("p", "Đây là nơi bạn đưa video vào và chọn video xuất ra to nhỏ, ngang dọc thế nào, tiếng to nhỏ ra sao."),
        ("h", "Làm thế nào?"),
        ("steps", [
            "Bấm «＋ Nạp video» rồi chọn file (mp4, mkv, mov…). Đưa nhiều file cũng được — máy làm lần lượt từng cái.",
            "Trong ô Hàng đợi, bấm vào tên video để chọn video đang chỉnh. Bấm ✕ để bỏ video khỏi danh sách.",
            "Phần «Khung hình & tốc độ»: chọn Tỉ lệ (ngang 16:9, dọc 9:16…), Độ phân giải (1080p là đẹp), FPS để «Giữ nguyên».",
            "Phần «Âm thanh»: «Âm gốc / nền» là tiếng có sẵn trong video, «Lồng tiếng» là giọng máy đọc. Có lồng tiếng thì bật "
            "«Khử giọng nói gốc (AI)» — chỉ còn nhạc nền & tiếng động, để âm nền 60–90. Không bật thì để âm gốc 10–20.",
        ]),
        ("kv", [
            ("Tăng tốc GPU", "Để bật. Máy tự chọn card màn hình để làm nhanh; không có card thì tự dùng CPU, chỉ chậm hơn thôi."),
            ("Chuẩn nén", "Để H.264 là an toàn nhất, mở được ở mọi nơi."),
            ("Chất lượng (CRF)", "Số nhỏ = đẹp hơn nhưng file to hơn. 20–23 là vừa."),
            ("Lặp video cho đủ giọng", "Bật khi giọng đọc dài hơn video: máy sẽ chiếu lại video cho đủ."),
        ]),
    ]},
    {"key": "subtitle", "title": "Phụ đề: chữ hiện trên video", "group": "Từng mục", "blocks": [
        ("h", "Để làm gì?"),
        ("p", "Chọn chữ nào hiện lên video (chữ gốc hay chữ đã dịch), chữ to nhỏ, màu gì, nằm ở đâu."),
        ("h", "Làm thế nào?"),
        ("steps", [
            "Chưa có file chữ (.srt)? Để «Tự nhận diện»: khi bấm Render, máy tự nghe và tạo chữ. Có sẵn file .srt thì chọn «Chọn file SRT» hoặc kéo thả file .srt vào cửa sổ.",
            "Chọn kiểu hiện: «Sub gốc» (tiếng gốc) · «Sub đã dịch» (tiếng Việt) · «Song ngữ» (Việt ở trên, gốc ở dưới).",
            "Dời chữ: bấm nút T trên màn hình giữa, rồi kéo cái ô viền xanh. Ô luôn ôm vừa chữ (chữ bám đáy ô): kéo cạnh "
            "trên/dưới = chữ to/nhỏ, kéo cạnh trái/phải = ô rộng/hẹp (ít/nhiều dòng), kéo giữa ô = dời chỗ.",
            "Video gốc có phụ đề in sẵn? Bật «Đặt lên chỗ chữ cứng đã che»: mỗi video máy tự dò chỗ chữ cũ, che mờ rồi đặt "
            "phụ đề mới đè đúng lên đó — áp mẫu hàng loạt không bị lệch mỗi video một kiểu. Kéo tay ô = tắt chế độ này.",
            "Chỉnh chữ: chọn Font, Cỡ chữ, Màu, Viền, Nền. Muốn nổi bật thì bật «Nền» kiểu «Hộp mờ bo góc».",
            "«Karaoke»: chữ đổi màu theo lời đọc, giống karaoke.",
        ]),
        ("tip", "Chữ hiện sớm hay muộn hơn tiếng? Chỉnh ô «Trễ phụ đề» (số dương = chữ hiện muộn hơn)."),
    ]},
    {"key": "review", "title": "Duyệt: xem từng video, chỗ nào chưa ổn thì sửa", "group": "Từng mục", "blocks": [
        ("p", "Tab này chỉ hiện khi mục Video bật «Duyệt trước khi xuất». Video là trung tâm: khung xem trước bên phải luôn "
              "hiện video đang duyệt, bảng bên trái tóm tắt video đó sẽ ra sao."),
        ("steps", [
            "Đổi video bằng ‹ › hoặc ô chọn (● chờ duyệt, ✓ đã duyệt, – bỏ qua).",
            "Đọc «Video này sẽ ra sao»: khung hình, lời thoại (bao nhiêu câu, đã dịch chưa), phụ đề, mấy người nói & ai "
            "lồng tiếng, âm thanh gốc, che mờ, nhạc nền, logo, cắt, hiệu ứng. Dòng tô vàng ⚠ là chỗ nên xem lại.",
            "Bấm ▶ Phát xem video. Thấy chỗ chưa ổn → bấm «Sửa ›» ở dòng đó → mở đúng tab tính năng. Chỉnh gì ở đó cũng CHỈ "
            "áp cho video đang duyệt (dải trên cùng ghi «✎ Chỉnh riêng»); muốn dùng cho mọi video thì bấm «Áp cho tất cả».",
            "Chỉnh xong bấm ▶ Phát xem lại → «‹ Về Duyệt» → «Duyệt» (hoặc Ctrl+Enter ở bất kỳ tab nào) → tự sang video chờ kế.",
            "Duyệt hết thì bấm «Xuất (n)» trên cùng. Menu ☰ cạnh nút Duyệt: chuẩn bị lại video này, duyệt hết video đang chờ.",
        ]),
        ("tip", "Sửa câu thoại: «Sửa ›» ở dòng Lời thoại → tab Lời thoại (bấm ô câu là video nhảy tới câu đó, ▶ phát đúng câu, "
                "ô «Nhờ AI sửa» chỉ sửa đúng những câu liên quan)."),
    ]},
    {"key": "stt", "title": "STT: nghe video và ghi ra chữ", "group": "Từng mục", "blocks": [
        ("h", "Để làm gì?"),
        ("p", "STT nghĩa là máy nghe lời nói trong video rồi gõ ra thành chữ, có kèm giờ giấc. Kết quả là file .srt — "
              "dùng để hiện phụ đề, để dịch, để lồng tiếng."),
        ("h", "Làm thế nào?"),
        ("steps", [
            "Chọn «Nhận dạng» = Whisper (chạy ngay trên máy, không cần mạng sau lần đầu) hoặc Gemini (qua mạng, cần API key, không cần card màn hình).",
            "Video tiếng Trung: chọn «SenseVoice» — đúng chữ và đúng nhịp nói hơn Whisper nhiều (lần đầu tải model ~0,9 GB). "
            "Video Douyin có phụ đề in sẵn: chọn «OCR phụ đề in sẵn» — đọc đúng từng chữ creator gõ (kể cả tên riêng), "
            "mốc thời gian lấy theo tiếng nói.",
            "Với Whisper, chọn Model: máy có card NVIDIA → «large-v3-turbo» (đúng nhất). Máy thường → «small». Lần đầu máy sẽ tải model, chờ vài phút.",
            "Ngôn ngữ để «auto» — máy tự nhận.",
            "Bấm «Tạo SRT từ Video trong hàng đợi». Xong sẽ thấy dòng «SRT: … cue». Bấm «Mở SRT» để xem chữ.",
            "Chữ sai chỗ nào, mở tab «Lời thoại» sửa ngay (bấm ô câu là video nhảy tới câu đó).",
        ]),
        ("kv", [
            ("Thiết bị = auto", "Máy tự thử card NVIDIA trước, không được thì dùng CPU."),
            ("Nút «Tải / Cài CUDA runtime»", "Chỉ hiện khi máy có card NVIDIA nhưng còn thiếu phần mềm đi kèm. Bấm một lần, chờ tải (~1.3 GB, cần Internet), từ đó nghe nhanh gấp 5–10 lần. Bản Setup đủ đã có sẵn, không cần bấm."),
            ("Nút «Tải trước model Whisper»", "Tải model đang chọn về máy ngay (lần nhận dạng đầu khỏi chờ, dùng được khi không có mạng)."),
            ("PC yếu", "Bật nếu máy chậm, hay bị treo."),
        ]),
    ]},
    {"key": "srt", "title": "Lời thoại: xem, sửa, dịch câu thoại của video", "group": "Từng mục", "blocks": [
        ("h", "Để làm gì?"),
        ("p", "Dịch file chữ (.srt) sang tiếng Việt hoặc tiếng khác bằng AI. Sửa lại câu nào chưa hay."),
        ("h", "Làm thế nào?"),
        ("steps", [
            "Cần API key một lần: mở Cài đặt (nút trên cùng), cột giữa «API keys Gemini», dán key vào, bấm «Lưu tất cả». "
            "Key lấy miễn phí ở aistudio.google.com → «Get API key».",
            "Bật «Dịch AI», chọn ngôn ngữ đích (Tiếng Việt).",
            "Ô «Phong cách dịch» (không bắt buộc): chọn gợi ý hoặc gõ ý bạn muốn, ví dụ «xưng tôi – các bạn, ngắn gọn».",
            "Bấm «Dịch». Chờ khoảng 30 giây cho video 5 phút. Xong thấy «Dịch xong».",
            "Đọc lại ngay ở mục «Lời thoại của video đang chọn» đầu tab. Sửa trực tiếp, bấm «Lưu & tạo lại giọng» — chỉ câu nào sửa mới đọc lại giọng.",
        ]),
        ("h", "Dịch chưa hay — bảo AI dịch lại"),
        ("steps", [
            "Bấm «Dịch phụ đề» lần nữa KHÔNG ra bản mới (máy nhớ bản cũ). Muốn bản khác: ghi yêu cầu vào ô «Phong cách dịch», "
            "ví dụ «xưng anh–em, văn nói», «ngắn gọn hơn», rồi bấm «Dịch lại (bản mới)». AI xem bản cũ để biết bạn chưa ưng chỗ nào.",
            "Chỉ vài câu dở: bấm ↻ cạnh câu đó, hoặc gõ yêu cầu vào ô «Nhờ AI sửa» — AI chỉ sửa đúng những câu liên quan.",
            "Đang duyệt: từ tab Duyệt bấm «Sửa ›» ở dòng Lời thoại để tới đây; nút ↻ cạnh từng câu, hoặc ô «Nhờ AI sửa» — gõ yêu cầu, AI chỉ sửa đúng những câu liên quan "
            "(tô vàng, xem trước được) — ưng thì bấm «Lưu & tạo lại giọng».",
            "Bản dịch mới được nhớ lại: lần sau dịch video có câu giống vậy sẽ dùng bản mới.",
        ]),
        ("kv", [
            ("Dịch thủ công", "Bảng từng câu để bạn tự gõ bản dịch, không cần AI."),
            ("Dự án dịch dang dở", "Đang dịch mà mất mạng hay tắt app? Vào đây bấm tiếp tục, không phải dịch lại từ đầu."),
            ("Từ điển thuật ngữ (trong Cài đặt)", "Gõ «tên gốc=tên Việt» mỗi dòng để AI dịch đúng tên riêng, ví dụ 松下=Panasonic."),
        ]),
    ]},
    {"key": "tts", "title": "TTS: máy đọc chữ thành tiếng (lồng tiếng)", "group": "Từng mục", "blocks": [
        ("h", "Để làm gì?"),
        ("p", "Máy đọc phụ đề thành giọng nói và ghép vào video, đúng lúc với từng câu."),
        ("h", "Làm thế nào?"),
        ("steps", [
            "Bật «Thêm lồng tiếng vào video».",
            "«Đọc từ»: chọn «Sub đã dịch» (đọc tiếng Việt) hoặc «Sub gốc».",
            "Chọn «Nhà cung cấp»: Edge TTS miễn phí (HoaiMy nữ, NamMinh nam + giọng 🌐 đa ngôn ngữ). "
            "Gemini TTS: 30 giọng rất tự nhiên, chọn «Phong cách» (review phim, kể chuyện, giọng miền Nam/Bắc…), dùng key Gemini; "
            "máy tự gộp ~25 câu vào một lượt gọi rồi tách lại (né giới hạn lượt/phút của gói miễn phí). "
            "VieNeu: 25 giọng Việt 3 miền chạy trên máy. FPT.AI: giọng Việt 3 miền. ElevenLabs: có thư viện giọng Việt "
            "🌐 cộng đồng chia sẻ. Google Cloud: 30 giọng Chirp3-HD.",
            "Chọn giọng, bấm «Nghe thử giọng». Chỉnh «Tốc độ đọc» nếu muốn.",
            "Để «Khớp mềm» bật: giọng đọc tự nhiên, máy tự dồn cho vừa video.",
            "Bấm «Render MP4» ở trên. Giọng đã tạo được giữ lại, lần sau render lại rất nhanh.",
        ]),
        ("kv", [
            ("Thư viện voice", "Giọng đã tạo trước đây. Chọn để dùng lại, khỏi tạo mới."),
            ("Nén tối đa (tổng)", "Máy được phép đọc nhanh tới mức này khi câu dài, tính cả «Tốc độ đọc» — tối đa 1,25 "
                                  "để giọng không méo. Câu vẫn dài quá: máy nhờ AI rút gọn bản dịch rồi đọc lại."),
            ("Đồng bộ tự nhiên", "Không ép giọng nhanh lên; chữ và giọng đi cùng nhau, video có thể cần bật «Lặp video cho đủ giọng»."),
        ]),
        ("h", "Video có nhiều người nói"),
        ("steps", [
            "Cần có phụ đề gốc trước (mục STT bấm «Nhận diện ngay», hoặc chọn file SRT).",
            "Mục TTS bật «Mỗi người nói một giọng», bấm «Nhận diện người nói». Lần đầu máy tải model ~28 MB.",
            "Máy chia ra Người 1, Người 2… và tự gợi ý giọng (nam/nữ). Bấm «Nghe giọng gốc» để biết ai là ai, rồi chọn giọng "
            "cho từng người. Edge tiếng Việt chỉ có 2 giọng — người thứ ba dùng lại giọng nhưng đổi «Cao độ» cho khác đi.",
            "Máy nhận sai câu nào thì bấm «Gán từng câu…» sửa tay. Không nhận diện được thì cũng tự chia tay ở đây.",
            "Render hàng loạt: video nào chưa nhận diện, máy tự nhận diện trước khi lồng tiếng.",
        ]),
        ("h", "Giọng tự nhiên & cảm xúc từng câu (Đạo diễn âm thanh)"),
        ("p", "Mặc định cả video đọc MỘT giọng xuyên suốt theo «Phong cách» ở mục TTS (vd «Tự nhiên», «Kể chuyện trầm "
              "ấm») — nghe tự nhiên nhất và ít lượt gọi nhất (~25 câu một lượt). Chỉ nhấn cảm xúc ở vài câu cần thiết:"),
        ("steps", [
            "Tab «Lời thoại»: bấm «＋ cảm xúc» cạnh câu (hoặc nút «Cảm xúc») → bảng «Cảm xúc từng câu»: bánh xe để kéo kim "
            "(góc = cảm xúc, xa tâm = bậc), «Nghe thử câu» rồi «Áp dụng».",
            "Đổi nhiều câu một lần: ô «Áp cho câu» gõ «3-8, 12», hoặc «Cả video», rồi «Áp dụng».",
            "«Tạo lại giọng video» — chỉ câu đổi cảm xúc mới gọi API, câu khác dùng lại.",
            "Muốn AI tự gán cảm xúc cho MỌI câu: bật «AI tự đặt cảm xúc cho từng câu» ở mục TTS trước khi dịch (thêm 1–2 lượt "
            "gọi; giọng đổi theo từng câu nên có thể kém tự nhiên hơn).",
        ]),
        ("p", "Gemini TTS đọc đủ cảm xúc; Edge TTS chỉ đổi nhịp / cao độ. Câu có cảm xúc mà chữ nhiều, khung ngắn: máy dặn "
              "«nói nhanh hơn một chút» ngay lượt đầu."),
    ]},
    {"key": "blur", "title": "Che mờ: giấu chữ, logo, mặt người", "group": "Từng mục", "blocks": [
        ("h", "Để làm gì?"),
        ("p", "Làm mờ hoặc che một vùng trên video, ví dụ dòng chữ tiếng Trung dính sẵn ở dưới, logo kênh khác, mặt người."),
        ("h", "Làm thế nào?"),
        ("steps", [
            "Trên màn hình giữa bấm nút B.",
            "Đặt chuột vào góc trên bên trái chỗ cần che, giữ chuột kéo tới góc dưới bên phải, thả ra. Một ô màu cam xuất hiện.",
            "Kéo ô để dời, kéo mấy chấm ở cạnh để đổi to nhỏ.",
            "Chọn kiểu che: «Mờ (Gaussian)» tự nhiên nhất · «Ô vuông» kiểu pixel · «Che kín» màu đen. Kéo «Độ mờ» cho vừa mắt.",
            "Vẽ thêm ô khác nếu cần (mỗi ô một chỗ). Xoá ô: bấm ✕ đỏ ở góc ô, hoặc bấm phím Delete.",
        ]),
        ("kv", [
            ("Bắt đầu / Kết thúc (giây)", "Chỉ che trong khoảng này. Để 0 = che suốt video."),
            ("Mềm mép", "Viền ô mờ dần, nhìn tự nhiên hơn."),
            ("Nút «Dải dưới»", "Tự đặt ô che sẵn ở dải phụ đề phía dưới."),
        ]),
        ("tip", "Ô che bám theo video gốc. Sau đó bạn đổi sang video dọc 9:16 hay phóng to thì ô vẫn che đúng chỗ, không phải vẽ lại."),
    ]},
    {"key": "bgm", "title": "Nhạc nền", "group": "Từng mục", "blocks": [
        ("h", "Để làm gì?"),
        ("p", "Thêm nhạc chạy nền dưới video. Máy tự vặn nhỏ nhạc khi có người nói."),
        ("h", "Làm thế nào?"),
        ("steps", [
            "Bấm «＋ Thêm nhạc» chọn file mp3/wav. Hoặc kéo thả file nhạc vào cửa sổ.",
            "Chỉnh «Âm lượng» khoảng 15–25 để nhạc không át tiếng nói.",
            "Muốn nhạc chỉ chạy một đoạn: gõ giây «Bắt đầu» và «Kết thúc». Để «Lặp» bật nếu nhạc ngắn hơn video.",
            "Để «Tự hạ nhạc khi có giọng» bật.",
        ]),
        ("tip", "Bỏ nhạc bạn hay dùng vào thư mục app\\assets\\music, rồi bấm «Thư viện nhạc» là thấy ngay."),
    ]},
    {"key": "brand", "title": "Logo, chữ, watermark", "group": "Từng mục", "blocks": [
        ("h", "Để làm gì?"),
        ("p", "Đóng dấu kênh của bạn lên video: hình logo, dòng chữ tên kênh, chữ mờ chống người khác lấy video, viền màu."),
        ("h", "Làm thế nào?"),
        ("steps", [
            "Thẻ «Logo»: bật, bấm «…» chọn file ảnh PNG (nền trong suốt là đẹp nhất). Chọn Vị trí (góc nào), Kích thước, Độ mờ.",
            "Thẻ «Chữ»: bật, gõ tên kênh, chọn font, màu, vị trí.",
            "Thẻ «Watermark»: bật, gõ @tên kênh. Chữ này mờ mờ, hiện suốt video.",
            "«Chuyển động»: muốn logo trôi, nảy, nhấp nháy thì chọn ở đây. Không thì để «Đứng yên».",
        ]),
        ("kv", [
            ("Vùng an toàn TikTok", "Bật khi làm video dọc: logo/chữ né chỗ nút like và dòng mô tả của TikTok."),
            ("Khung viền", "Vẽ viền màu quanh video."),
        ]),
    ]},
    {"key": "trim", "title": "Cắt: bỏ đầu, bỏ đuôi, cắt thành nhiều đoạn", "group": "Từng mục", "blocks": [
        ("h", "Để làm gì?"),
        ("p", "Bỏ phần thừa ở đầu hoặc cuối video. Hoặc cắt video dài thành nhiều clip ngắn."),
        ("h", "Làm thế nào?"),
        ("steps", [
            "Cách nhanh: xem video trên màn hình giữa tới chỗ muốn bắt đầu, bấm phím I. Xem tới chỗ muốn kết thúc, bấm phím O.",
            "Cách gõ số: bật «Cắt», gõ «Bỏ đầu» và «Bỏ đuôi» (giây, hoặc kiểu 1:30 = 1 phút 30 giây).",
            "Cắt nhiều đoạn: bấm «＋ Thêm đoạn», gõ bắt đầu – kết thúc cho từng đoạn.",
            "Chọn «Ghép các đoạn thành 1 video» hoặc «Xuất mỗi đoạn thành 1 file» (ra nhiều file _p1, _p2…).",
        ]),
        ("tip", "Phụ đề, giọng đọc, ô che đều tự đi theo đoạn đã cắt, không phải chỉnh lại."),
    ]},
    {"key": "color", "title": "Hiệu ứng: màu sắc, chuyển động, chống trùng", "group": "Từng mục", "blocks": [
        ("h", "Để làm gì?"),
        ("p", "Làm video đẹp hơn (màu ấm, điện ảnh…), xoay video, thêm chuyển động cho ảnh tĩnh. Và làm video của bạn khác đi một chút khi đăng lên nhiều kênh."),
        ("h", "Làm thế nào?"),
        ("steps", [
            "Bật «Bật chỉnh màu / hiệu ứng».",
            "Bấm một nút màu sẵn: Ấm áp, Điện ảnh, Tươi sáng… Nhìn màn hình giữa thấy đổi ngay. Kéo Sáng / Tương phản nếu muốn chỉnh thêm.",
            "Video bị nằm ngang / ngược? Chọn «Xoay» 90° hoặc 180°.",
            "Video là ảnh đứng yên? Chọn «Ken Burns» = Zoom vào để hình từ từ phóng to cho đỡ chán.",
            "Đăng lại video của chính bạn lên kênh khác: bấm «Bật nhanh chống trùng» ở trên cùng.",
        ]),
        ("kv", [
            ("Khử rung", "Video quay tay bị rung thì bật."),
            ("Hạt phim / Tối góc", "Kiểu phim cũ, nhìn nghệ hơn."),
            ("Lớp phủ texture", "Phủ thêm hiệu ứng ánh sáng, bụi (file trong app\\assets\\textures)."),
        ]),
    ]},
    {"key": "story", "title": "Dựng Truyện: AI viết truyện và làm thành video", "group": "Từng mục", "blocks": [
        ("h", "Để làm gì?"),
        ("p", "Bạn gõ một ý tưởng, AI viết thành câu chuyện nhiều cảnh, đọc thành giọng, vẽ ảnh cho từng cảnh, rồi ghép thành video có phụ đề."),
        ("h", "Làm thế nào?"),
        ("steps", [
            "Cần API key Gemini trong Cài đặt (như mục SRT). Viết truyện thì miễn phí; vẽ ảnh AI cần tài khoản có bật thanh toán — chưa có thì máy dùng ảnh tạm (chữ trên nền màu).",
            "Gõ ý tưởng vào ô «Chủ đề», chọn độ dài, giọng văn, khung video (ngang/dọc). Bấm «Viết kịch bản». Chờ 1–2 phút.",
            "Bấm «Trình soạn cảnh» để đọc lại từng cảnh. Sửa lời kể, thêm bớt cảnh, chọn giọng cho nhân vật.",
            "Bấm «Tạo giọng» → «Tạo ảnh» (nếu có quota) → «Ghép video».",
            "Xong sẽ có: video MP4, file phụ đề, tiêu đề + mô tả + tag để đăng YouTube, ảnh bìa.",
        ]),
    ]},

    # ══ KHÁC ═════════════════════════════════════════════════════════════
    {"key": "settings", "title": "Cài đặt & API key", "group": "Khác", "blocks": [
        ("p", "Mở bằng nút «Cài đặt» ở góc trên bên trái."),
        ("kv", [
            ("Cột trái", "Card màn hình, chuẩn nén, Giao diện (bấm một thẻ màu là đổi ngay: Studio, Midnight, Graphite, Orchid, OLED), dọn file tạm."),
            ("Cột giữa — API keys", "Dán key Gemini vào đây để dịch AI, nghe cloud, dựng truyện. Mọi ô key (Gemini, Groq, DeepSeek, "
                                    "OpenAI, ElevenLabs, Google) đều nhận nhiều key: mỗi dòng một key, hết hạn mức hay key hỏng máy tự đổi key kế tiếp."),
            ("Cột phải", "Key giọng đọc trả tiền (ElevenLabs, Google). «Preset 1-click»: lưu toàn bộ cài đặt hiện tại để lần sau bấm một cái là xong. Chọn thư mục xuất video."),
        ]),
        ("steps", ["Lấy key Gemini: vào aistudio.google.com → đăng nhập Google → «Get API key» → «Create API key» → copy.",
                   "Dán vào ô «API keys Gemini» → bấm «Lưu tất cả»."]),
        ("warn", "Key nằm trong file settings.json cùng thư mục app. Đừng gửi file này cho người khác."),
    ]},
    {"key": "shortcuts", "title": "Phím tắt", "group": "Khác", "blocks": [("shortcuts", None)]},
    {"key": "faq", "title": "Gặp lỗi thì làm gì?", "group": "Khác", "blocks": [
        ("kv", [
            ("Chờ mãi không xong", "Nhìn thanh trạng thái dưới cùng (bước đang làm, thời gian còn lại) và bấm biểu tượng log ở góc phải. Bấm «Dừng» trên cùng nếu muốn huỷ."),
            ("Đang chạy việc này mà bấm việc khác", "Máy hỏi có xếp vào hàng chờ không — đồng ý thì việc đó tự chạy ngay khi "
             "việc trước xong (dòng trên cùng ghi «chờ: …»). Trong lúc chờ cứ chuyển tab, chọn video khác, chỉnh tiếp thoải "
             "mái — việc đang chạy giữ nguyên cấu hình lúc bấm. Bấm «Dừng» = huỷ cả hàng chờ."),
            ("Báo NVENC không dùng được", "Không sao, máy tự chuyển sang cách khác. Muốn nhanh hơn thì cập nhật driver NVIDIA mới nhất."),
            ("Nghe video (STT) chậm dù có card NVIDIA", "Mục STT → bấm «Tải CUDA runtime» một lần (hoặc cài bản Setup đủ)."),
            ("Video bị giữ khung cuối thêm vài giây", "Giọng dài hơn video nên máy giữ khung cuối cho đọc hết (không cắt giọng). Câu dịch quá dài: Cài đặt → «Câu đọc tràn khung → AI tự rút gọn bản dịch» (bật sẵn), hoặc tab Lời thoại: «Phong cách dịch» chọn «Ngắn gọn hơn» rồi «Dịch lại (bản mới)»; hoặc mục Video bật «Lặp video cho đủ thời lượng giọng đọc»."),
            ("Edge TTS báo «No audio was received»", "Dịch vụ đang bận. Máy tự thử lại; câu nào vẫn lỗi sẽ ghi trong log — bấm Render lại là được."),
            ("Không biết máy thiếu gì", "Cài đặt → nút «Kiểm tra máy»: liệt kê GPU, CUDA, model, giọng, key — mục nào thiếu có ghi bấm đâu để tải."),
            ("Gemini báo 429 / hết hạn mức", "Đợi 1 phút rồi thử lại, hoặc dán thêm key khác. Tạo ảnh/video AI cần tài khoản trả tiền."),
            ("Video bị xoay sai", "Mục Hiệu ứng → Xoay."),
            ("Giọng gốc (Trung, Anh…) vẫn nghe thấy", "Tab Video › Âm thanh: bật «Khử giọng nói gốc (AI)». AI bỏ giọng nói, giữ nhạc nền & tiếng động (lần đầu tải model ~64 MB, mỗi video khử một lần ~1 phút/phút video)."),
        ]),
    ]},
]

GROUPS = ["Bắt đầu", "Từng mục", "Khác"]


def topic(key: str) -> dict | None:
    return next((t for t in TOPICS if t["key"] == key), None)
