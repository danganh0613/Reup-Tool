"""Nội dung Hướng dẫn sử dụng — viết đơn giản, câu ngắn, ai đọc cũng làm theo được.

Mỗi chủ đề: ``key`` (trùng key module để mở đúng trang), ``title``, ``group``, ``blocks``:
    ("p", "đoạn văn")                 ("h", "tiêu đề nhỏ")
    ("steps", ["bước 1", "bước 2"])   ("tip", "mẹo")   ("warn", "lưu ý")
    ("kv", [("Tên nút", "nó làm gì"), …])   ("shortcuts", None)
"""

from __future__ import annotations

from app.services.qwen_asr import INSTALL_GB

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
            "Mục STT: bấm «Tạo phụ đề cho video đang chọn». Máy nghe video rồi ghi ra chữ (tiếng gốc), chạy ngay trên máy. "
            f"Lần đầu cần cài bộ nghe lời thoại (Cài đặt › AI trên máy › «Cài», ~{INSTALL_GB} GB, một lần).",
            "Mục Lời thoại: chọn «Dịch sang» = Tiếng Việt, bấm «Dịch phụ đề». Cần có API key Gemini (miễn phí, lấy tại "
            "aistudio.google.com, dán vào Cài đặt › AI dùng API key). Muốn sửa câu nào thì sửa ngay ở đầu tab.",
            "Phụ đề tự hiện chữ Việt kiểu TikTok. Muốn dời / đổi cỡ: dùng 2 nút phụ đề trên khung video.",
            "Mục TTS: bật «Thêm lồng tiếng vào video», chọn giọng, bấm «Nghe thử giọng» xem có thích không.",
            "Mục Che mờ (nếu video có chữ tiếng nước ngoài dính sẵn trên hình): bấm B rồi vẽ một ô đè lên dòng chữ đó.",
            "Mục Video › Âm thanh: bật «Khử giọng nói gốc (AI)» (bỏ giọng Trung/Anh, giữ nhạc nền & tiếng động), «Âm gốc / nền» "
            "khoảng 70, «Lồng tiếng» khoảng 150. Rồi bấm «Render MP4».",
        ]),
        ("warn", "Câu tiếng Việt thường dài hơn câu gốc, nên giọng đọc phải nhanh hơn một chút. Máy tự chỉnh, câu vẫn "
                 "dài quá thì AI tự rút gọn. Muốn gọn hơn nữa: tab Lời thoại, ô «Nhờ AI sửa» gõ «ngắn gọn hơn»."),
    ]},
    {"key": "flow_short", "title": "Video ngang → video dọc TikTok", "group": "Bắt đầu", "blocks": [
        ("steps", [
            "Đưa video vào.",
            "Trên màn hình giữa, bấm ô đầu tiên (đang ghi 16:9 hoặc Gốc) → chọn «9:16 (Dọc)».",
            "Bấm ô «Cắt vừa khung» → chọn «Nền mờ (blur)». Video sẽ nằm giữa, trên dưới là hình mờ. (Chọn «Cắt vừa khung» nếu muốn phóng to cắt hai bên.)",
            "Muốn cắt thành nhiều clip ngắn: mục Cắt → «＋ Thêm đoạn», gõ giây bắt đầu và kết thúc, chọn «Xuất mỗi đoạn thành 1 file».",
            "Bấm «Render MP4».",
        ]),
        ("tip", "Mục Logo → thẻ «Watermark» bật «Bật watermark»; thẻ «Chuyển động» chọn «Vùng chạy» = TikTok/Reels để chữ không bị nút like/comment của TikTok che mất."),
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
        ("p", "Mặc định đã giống phụ đề TikTok: chữ đậm trắng viền đen, tối đa 2 dòng, nằm trên vùng nút của TikTok."),
        ("steps", [
            "Đổi chỗ / cỡ chữ: dùng 2 nút trên khung video — «Dưới · Giữa · Trên · Đè chữ cũ» và «Nhỏ · Vừa · Lớn».",
            "Video gốc có chữ in sẵn: chọn «Đè chữ cũ» — máy tự dò, che mờ chữ cũ rồi đặt phụ đề mới vào giữa đúng chỗ đó.",
            "Đổi kiểu (trắng, vàng, hộp nền…): mục «Lời thoại» › tab «Phụ đề» › «Kiểu». Kiểu «Nổi chữ đang đọc»: "
            "chữ đang đọc đổi màu và to lên theo đúng nhịp giọng, giống CapCut — cài «Căn chữ nổi» (Cài đặt › AI "
            "trên máy) để khớp từng tiếng.",
            "Muốn chỉnh tay (phông, màu, viền, nền…): các khối «Chữ», «Nền & chữ nổi», «Khung» ngay dưới — bấm tiêu đề "
            "để mở.",
            "Kéo tay: ở mục «Lời thoại» bấm vào khung xanh trên video rồi kéo; «Căn giữa» / «Rộng hết» trên thanh để căn "
            "nhanh.",
        ]),
        ("tip", "Chữ hiện sớm / muộn hơn tiếng: tab «Phụ đề» › «Thời gian» › «Trễ phụ đề»."),
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
            "«Lấy lời từ» = «Giọng nói trong video»: AI nghe lời thoại ngay trên máy (có card NVIDIA thì nhanh hơn nhiều), "
            f"không cần mạng, không tốn lượt AI. Lần đầu bấm «Cài bộ nghe lời thoại» (~{INSTALL_GB} GB, một lần).",
            "Video Douyin có phụ đề in sẵn: chọn «Chữ in sẵn trên video» — đọc đúng từng chữ creator gõ (kể cả tên riêng), "
            "mốc thời gian lấy theo tiếng nói.",
            "Ngôn ngữ để «Tự nhận diện» — máy tự nhận.",
            "Bấm «Tạo phụ đề cho video đang chọn». Xong sẽ thấy dòng «Phụ đề: … câu». Bấm «Xem / sửa lời thoại» để xem chữ.",
            "Tên riêng hiếm có thể sai: mở tab «Lời thoại» sửa ngay (bấm ô câu là video nhảy tới câu đó) hoặc gõ vào ô "
            "«Nhờ AI sửa».",
        ]),
    ]},
    {"key": "srt", "title": "Lời thoại: xem, sửa, dịch câu thoại của video", "group": "Từng mục", "blocks": [
        ("h", "Để làm gì?"),
        ("p", "Dịch file chữ (.srt) sang tiếng Việt hoặc tiếng khác bằng AI. Sửa lại câu nào chưa hay."),
        ("h", "Làm thế nào?"),
        ("steps", [
            "Cần API key một lần: mở Cài đặt (nút trên cùng), trang «AI dùng API key», dán key Gemini vào, bấm «Lưu tất cả». "
            "Key lấy miễn phí ở aistudio.google.com → «Get API key».",
            "Chọn «Dịch sang» (Tiếng Việt).",
            "Ô «Phong cách» (không bắt buộc): chọn gợi ý hoặc gõ ý bạn muốn, ví dụ «xưng tôi – các bạn, ngắn gọn».",
            "Bấm «Dịch phụ đề». AI dịch 2 bước: dịch sát nghĩa rồi tự đọc lại, viết cho tự nhiên; có lồng tiếng thì rút gọn "
            "câu đọc không kịp. Khoảng 10–15 lượt gọi AI mỗi video ngắn.",
            "Đọc lại ngay ở mục «Lời thoại của video đang chọn» đầu tab. Sửa trực tiếp, bấm «Lưu & tạo lại giọng» — chỉ câu nào sửa mới đọc lại giọng.",
        ]),
        ("h", "Dịch chưa hay — nhờ AI sửa"),
        ("steps", [
            "Gõ yêu cầu vào ô «Nhờ AI sửa», ví dụ «xưng anh – em, văn nói», «ngắn gọn hơn», «sửa tên Lý Tiểu Long». AI tự tìm "
            "đúng các câu cần sửa trong cả video, câu khác giữ nguyên.",
            "Chỉ một câu dở: bấm ↻ cạnh câu đó để AI dịch lại riêng câu ấy.",
            "Câu AI sửa được tô vàng, dòng nhỏ ghi câu cũ; bấm ↶ cạnh câu để trả lại, «Hoàn tác» trả lại hết, ưng thì "
            "«Lưu & tạo lại giọng».",
            "Đang duyệt: từ tab Duyệt bấm «Sửa ›» ở dòng Lời thoại để tới đây.",
            "Bản sửa được nhớ lại: lần sau dịch video có câu giống vậy sẽ dùng bản mới.",
        ]),
        ("kv", [
            ("Dán bản dịch ngoài", "Bảng từng câu để bạn tự gõ / dán bản dịch, không cần AI."),
            ("Dịch dở giữa chừng", "Mất mạng hay model hết lượt? Mỗi lô dịch đều được lưu — bấm «Thử lại» là dịch tiếp phần "
                                   "còn lại, không dịch lại từ đầu."),
            ("Từ điển tên riêng (trong Cài đặt)", "Gõ «tên gốc=tên Việt» mỗi dòng để AI dịch đúng tên riêng, ví dụ 松下=Panasonic."),
        ]),
    ]},
    {"key": "tts", "title": "TTS: máy đọc chữ thành tiếng (lồng tiếng)", "group": "Từng mục", "blocks": [
        ("h", "Để làm gì?"),
        ("p", "Máy đọc phụ đề thành giọng nói và ghép vào video, đúng lúc với từng câu."),
        ("h", "Làm thế nào?"),
        ("steps", [
            "Bật «Thêm lồng tiếng vào video».",
            "«Đọc từ»: chọn «Sub đã dịch» (đọc tiếng Việt) hoặc «Sub gốc».",
            "Chọn «Giọng đọc»: VieNeu (mặc định, khuyên dùng) — 25 giọng Việt 3 miền chạy ngay trên máy, miễn phí, không "
            "giới hạn lượt; máy đọc liền vài câu kề nhau cho tự nhiên rồi tách lại, câu đọc lỗi tự đọc lại. Edge TTS miễn phí "
            "(HoaiMy nữ, NamMinh nam + giọng 🌐 đa ngôn ngữ, cần mạng) — cũng đọc tạm câu VieNeu đọc hỏng.",
            "Chọn giọng, bấm «Nghe thử giọng». Chỉnh «Tốc độ đọc» nếu muốn.",
            "Giọng luôn khớp mềm với hình: câu dài đọc nhanh dần, câu bị trễ đọc nhanh hơn để đuổi kịp, câu vẫn tràn thì AI "
            "rút gọn bản dịch rồi đọc lại.",
            "Bấm «Render MP4» ở trên. Giọng đã tạo được giữ lại, lần sau render lại rất nhanh.",
        ]),
        ("kv", [
            ("Thư viện voice", "Giọng đã tạo trước đây. Chọn để dùng lại, khỏi tạo mới."),
            ("Nén tối đa", "Máy được phép đọc nhanh tới mức này khi câu dài, tính cả «Tốc độ đọc» — tối đa 1,25 "
                           "để giọng không méo. Câu vẫn dài quá: máy nhờ AI rút gọn bản dịch rồi đọc lại."),
            ("Nghỉ giữa câu", "Khoảng lặng tối thiểu giữa hai câu thoại."),
        ]),
        ("h", "Video có nhiều người nói"),
        ("steps", [
            "Cần có phụ đề gốc trước (mục STT bấm «Tạo phụ đề cho video đang chọn», hoặc chọn file SRT).",
            "Mục TTS bật «Mỗi người nói một giọng», bấm «Nhận diện người nói». Lần đầu máy tải model ~28 MB.",
            "Máy chia ra Người 1, Người 2… và tự gợi ý giọng (nam/nữ). Bấm «Nghe giọng gốc» để biết ai là ai, rồi chọn giọng "
            "cho từng người. Edge tiếng Việt chỉ có 2 giọng — người thứ ba dùng lại giọng nhưng đổi «Cao độ» cho khác đi.",
            "Máy nhận sai câu nào thì bấm «Gán từng câu…» sửa tay. Không nhận diện được thì cũng tự chia tay ở đây.",
            "Render hàng loạt: video nào chưa nhận diện, máy tự nhận diện trước khi lồng tiếng.",
        ]),
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
            ("Nút «Vùng đáy»", "Tự đặt ô che sẵn ở dải phụ đề phía dưới."),
        ]),
        ("tip", "Ô che bám theo video gốc. Sau đó bạn đổi sang video dọc 9:16 hay phóng to thì ô vẫn che đúng chỗ, không phải vẽ lại."),
    ]},
    {"key": "bgm", "title": "Nhạc nền", "group": "Từng mục", "blocks": [
        ("h", "Để làm gì?"),
        ("p", "Thêm nhạc chạy nền dưới video. Máy tự vặn nhỏ nhạc khi có người nói."),
        ("h", "Làm thế nào?"),
        ("steps", [
            "Bấm «Thư viện nhạc» chọn bài có sẵn, hoặc «+ Thêm từ máy…» chọn file mp3/wav. Hoặc kéo thả file nhạc vào cửa sổ.",
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
            "Thẻ «Logo»: bật, bấm «Chọn ảnh logo…» (PNG nền trong suốt là đẹp nhất). Chọn Vị trí (góc nào), «Cỡ logo», «Opacity».",
            "Thẻ «Chữ»: bật, gõ tên kênh, chọn font, màu, vị trí.",
            "Thẻ «Watermark»: bật, gõ @tên kênh. Chữ này mờ mờ, hiện suốt video.",
            "Muốn logo / chữ trôi, nảy, nhấp nháy: chọn ô «Chuyển động» ngay trong thẻ đó. Không thì để «Đứng yên».",
        ]),
        ("kv", [
            ("Thẻ «Chuyển động» › «Vùng chạy»", "Chọn TikTok/Reels khi làm video dọc: logo / chữ chạy né chỗ nút like và dòng mô tả."),
            ("Khung viền", "Vẽ viền màu quanh video."),
        ]),
    ]},
    {"key": "trim", "title": "Cắt: bỏ đầu, bỏ đuôi, cắt thành nhiều đoạn", "group": "Từng mục", "blocks": [
        ("h", "Để làm gì?"),
        ("p", "Bỏ phần thừa ở đầu hoặc cuối video. Hoặc cắt video dài thành nhiều clip ngắn."),
        ("h", "Làm thế nào?"),
        ("steps", [
            "Cách nhanh: xem video trên màn hình giữa tới chỗ muốn bắt đầu, bấm phím I. Xem tới chỗ muốn kết thúc, bấm phím O.",
            "Cách gõ số: bật «Bật cắt đầu / cuối», gõ «Bỏ đầu» và «Bỏ cuối» (giây, hoặc kiểu 1:30 = 1 phút 30 giây).",
            "Cắt nhiều đoạn: bấm «＋ Thêm đoạn», gõ bắt đầu – kết thúc cho từng đoạn.",
            "Chọn «Ghép các đoạn thành 1 video» hoặc «Xuất mỗi đoạn thành 1 file» (ra nhiều file _p1, _p2…).",
        ]),
        ("tip", "Phụ đề, giọng đọc, ô che đều tự đi theo đoạn đã cắt, không phải chỉnh lại."),
    ]},
    {"key": "color", "title": "Hiệu ứng: màu sắc, chuyển động, chống trùng", "group": "Từng mục", "blocks": [
        ("h", "Để làm gì?"),
        ("p", "Làm video đẹp hơn (màu ấm, điện ảnh…), xoay video, thêm chuyển động cho cảnh tĩnh. Và làm video của bạn khác đi một chút khi đăng lên nhiều kênh."),
        ("h", "Làm thế nào?"),
        ("steps", [
            "Bấm một nút màu sẵn hoặc chỉnh ô bất kỳ — công tắc «Bật chỉnh màu / hiệu ứng» tự bật.",
            "Bấm một nút màu sẵn: Ấm áp, Điện ảnh, Tươi sáng… Nhìn màn hình giữa thấy đổi ngay. Kéo Sáng / Tương phản nếu muốn chỉnh thêm.",
            "Video bị nằm ngang / ngược? Chọn «Xoay» 90° hoặc 180°.",
            "Cảnh tĩnh, ít chuyển động? Chọn «Chuyển động Ken Burns» = Zoom vào để hình từ từ phóng to cho đỡ chán.",
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
            "Bấm «Trình soạn kịch bản» để đọc lại từng cảnh. Sửa lời kể, thêm bớt cảnh, chọn giọng cho nhân vật.",
            "Bấm «Tạo giọng» → «Tạo ảnh» (nếu có quota) → «Ghép video».",
            "Xong sẽ có: video MP4, file phụ đề, tiêu đề + mô tả + tag để đăng YouTube, ảnh bìa.",
        ]),
    ]},

    # ══ KHÁC ═════════════════════════════════════════════════════════════
    {"key": "settings", "title": "Cài đặt & API key", "group": "Khác", "blocks": [
        ("p", "Mở bằng nút «Cài đặt» ở góc trên bên trái."),
        ("kv", [
            ("🖥 AI trên máy", "Nghe lời thoại (Qwen3-ASR), giọng đọc Việt (VieNeu), kiểm lỗi giọng đọc (Whisper), căn chữ "
                              "nổi, CUDA cho card NVIDIA: xem đã cài chưa, bấm «Cài» / «Tải» một lần. Miễn phí, không giới "
                              "hạn lượt."),
            ("☁ AI dùng API key", "Key Gemini (mỗi dòng một key) + model, và từ điển tên riêng. Gemini dùng để dịch, «Nhờ AI "
                                  "sửa», rút gọn câu đọc không kịp, dựng truyện. Model hết lượt thì máy dừng và báo — bấm "
                                  "«Chọn model…» để đổi."),
            ("Các trang khác", "«Xuất video»: thư mục xuất, độ to âm thanh · «Giao diện»: bấm một bộ màu là đổi ngay · "
                               "«Bộ nhớ & nâng cao»: dọn dẹp file đang làm, đường dẫn ffmpeg. Từ điển phát âm ở «AI trên máy»."),
        ]),
        ("steps", ["Lấy key Gemini: vào aistudio.google.com → đăng nhập Google → «Get API key» → «Create API key» → copy.",
                   "Dán vào ô «API key» ở trang «AI dùng API key» → bấm «Lưu tất cả»."]),
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
            ("Báo «Chưa cài bộ nghe lời thoại»", f"Bấm «Cài…» trên thanh trạng thái (hoặc Cài đặt › AI trên máy) — cài một lần ~{INSTALL_GB} GB."),
            ("Video bị giữ khung cuối thêm vài giây", "Giọng dài hơn video nên máy giữ khung cuối cho đọc hết (không cắt giọng). Máy đã tự nhờ AI rút gọn câu tràn; muốn gọn hơn nữa: tab Lời thoại, ô «Nhờ AI sửa» gõ «ngắn gọn hơn»; hoặc mục Video bật «Lặp video cho đủ thời lượng giọng đọc»."),
            ("Edge TTS báo «No audio was received»", "Dịch vụ đang bận. Máy tự thử lại; câu nào vẫn lỗi sẽ ghi trong log — bấm Render lại là được."),
            ("Không biết máy thiếu gì", "Cài đặt → nút «Kiểm tra máy»: liệt kê GPU, CUDA, model, giọng, key — mục nào thiếu có ghi bấm đâu để tải."),
            ("Gemini báo hết lượt / quá tải", "Máy dừng và báo (không tự đổi model). Bấm «Chọn model…» để chọn model khác, hoặc "
                                              "đợi hạn mức đặt lại (≈ 14–15 giờ chiều). Đã dịch được lô nào thì «Thử lại» dịch "
                                              "tiếp phần còn lại. Tạo ảnh/video AI cần tài khoản trả tiền."),
            ("Video bị xoay sai", "Mục Hiệu ứng → Xoay."),
            ("Giọng gốc (Trung, Anh…) vẫn nghe thấy", "Tab Video › Âm thanh: bật «Khử giọng nói gốc (AI)». AI bỏ giọng nói, giữ nhạc nền & tiếng động (lần đầu tải model ~64 MB, mỗi video khử một lần ~1 phút/phút video)."),
        ]),
    ]},
]

GROUPS = ["Bắt đầu", "Từng mục", "Khác"]


def topic(key: str) -> dict | None:
    return next((t for t in TOPICS if t["key"] == key), None)
