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
        ("h", "Làm theo 4 bước"),
        ("steps", [
            "Đưa video vào: kéo file video thả vào khung xem trước (bên phải). Hoặc mục Video bấm «＋ Nạp video».",
            "Chọn mẫu ở ô «Mẫu» trên cùng, vd «Review phim dọc 9:16» (dịch sang tiếng Việt + lồng tiếng + phụ đề TikTok). "
            "Không chọn mẫu thì video giữ nguyên tiếng gốc.",
            "Cần chỉnh thêm thì bấm từng mục ở thanh bên trái (Lời thoại, Lồng tiếng, Che mờ, Nhạc…) — khung xem trước "
            "đổi theo ngay.",
            "Bấm nút chính trên cùng bên phải: «Chuẩn bị» (máy làm phụ đề, dịch, giọng) → xem ở tab «Duyệt» → «Xuất». "
            "Thanh trạng thái dưới cùng cho biết đang làm bước nào và còn bao lâu. Xong bấm «Mở thư mục» ngay trên thanh "
            "đó để lấy video.",
        ]),
        ("warn", f"Video tiếng nước ngoài cần 2 thứ (cài một lần): bộ nghe lời thoại Qwen3-ASR (Cài đặt › AI trên máy, "
                 f"~{INSTALL_GB} GB) và API key Gemini "
                 "để dịch (Cài đặt › AI dùng API key). Thiếu thì máy báo ngay khi bấm chạy."),
        ("h", "Mấy nút trên khung xem trước"),
        ("kv", [
            ("Các ô nhỏ 16:9 · Gốc · Gốc · Cắt vừa khung", "Bấm vào để chọn khung video: 16:9 là video ngang (YouTube), 9:16 là video dọc (TikTok)."),
            ("V · T · B", "V: kéo hình đi. T: kéo ô chữ phụ đề. B: vẽ ô che mờ (hiện ở tab Lời thoại và Che mờ)."),
            ("Sub · Che mờ", "Tạm giấu chữ / ô che để nhìn hình cho rõ. Không ảnh hưởng video xuất ra."),
            ("‹ 2/5 ›", "Đổi sang video trước / sau trong hàng đợi ngay trên khung xem trước, ở tab nào cũng được."),
            ("Phát", "Xem thử giống video xuất: đủ khung hình, màu, che mờ, logo, chữ, phụ đề; tiếng gồm âm gốc + giọng lồng "
                     "+ nhạc nền; bỏ qua đoạn đã cắt, đúng tốc độ. Đang phát mà chỉnh gì thì hình/tiếng đổi theo ngay."),
            ("Xem hiệu ứng", "Tắt khi máy yếu, phát bị giật: lúc đó chỉ phát hình gốc, phụ đề vẽ đè (tiếng vẫn đủ)."),
            ("Xuất thử", "Xuất thật 15 giây từ chỗ đang xem rồi phát luôn — giống hệt file xuất. Chưa có giọng lồng thì "
                         "máy tự tạo giọng cho đoạn đó. Dùng để nghiệm thu trước khi xuất cả video."),
            ("Xoá · phím Delete", "Xoá ô che hoặc ô chữ đang chọn. Lỡ tay thì bấm Ctrl+Z để lấy lại."),
        ]),
        ("tip", "Cấu hình, hàng đợi và câu lời thoại đang sửa đều tự lưu — tắt máy mở lại vẫn còn. Sửa lời thoại xong, "
                "bấm icon loa trên danh sách câu để tạo lại giọng (hoặc để Chuẩn bị / Xuất tự tạo lại)."),
        ("h", "Mẫu: làm hàng loạt video giống nhau"),
        ("steps", [
            "Chọn mẫu có sẵn ở ô «Mẫu» trên cùng (Review phim dọc 9:16, Thuyết minh ngang 16:9, Reup nhanh không lời…), "
            "hoặc tự chỉnh một video cho ưng ý rồi bấm «Lưu mẫu» (chọn lưu những phần nào, kèm ảnh đại diện).",
            "Giọng lồng tiếng: mẫu nhớ giọng chính + danh sách giọng theo thứ tự người nói (Người 1 → giọng 1, Người 2 → "
            "giọng 2…). Video sau nhận ra bao nhiêu người thì gán lần lượt; dư người thì tự chọn thêm giọng Nam/Nữ. "
            "Muốn một người đọc hết thì tắt «Mỗi người nói một giọng» ở tab Lồng tiếng.",
            "Nạp nhiều video / cả thư mục vào hàng đợi — mọi video dùng chung mẫu đang chọn.",
            "Video nào cần khác (vd. chữ cứng nằm chỗ khác): chọn video đó, ở mục Video bật «Chỉnh riêng cho video này» "
            "hoặc «Áp mẫu riêng». Video đó có nhãn «✎ Riêng» trong hàng đợi, ô Mẫu trên cùng ghi mẫu của nó.",
            "Bấm «Chuẩn bị» rồi «Xuất»: mỗi video dựng đúng theo mẫu / cấu hình riêng của nó. Thứ tự = thứ tự hàng đợi "
            "(chuột phải vào video để đưa lên / xuống).",
            "Nút ≡ cạnh «Lưu mẫu» (Quản lý mẫu): xem tất cả mẫu, áp cho tất cả / chỉ video đang chọn, xuất file mẫu gửi "
            "máy khác, nhập mẫu.",
        ]),
        ("h", "Chuẩn bị → Duyệt → Xuất"),
        ("steps", [
            "Ô «Chạy đến» cạnh nút chính: «Tự động hết» (làm hết rồi chờ duyệt) · «Đến lời gốc» (dừng để bạn tự dịch bên "
            "ngoài) · «Đến bản dịch» (dừng để bạn soát bản dịch rồi mới tạo giọng). Áp cho mọi video.",
            "Bấm «Chuẩn bị (n)»: máy làm phụ đề, dịch, nhận diện người nói, tạo giọng lồng cho cả hàng đợi (chưa xuất). "
            "Xong tự mở tab «Duyệt» (thanh bên trái) — khung xem trước vẫn ở ngay cạnh.",
            "Video dừng «Chờ bản dịch» / «Chờ soát bản dịch»: làm ở tab Lời thoại (icon copy / dán, sửa câu, Nhờ AI sửa) "
            "rồi bấm «Tiếp tục (n)» — máy chạy tiếp từ chỗ dừng, phần đã xong không làm lại.",
            "Tab Duyệt: ‹ › hoặc ô chọn để đổi video, ▶ Phát để xem. Thấy câu sai: bấm «Sửa ›» ở dòng «Lời thoại» → sửa ở "
            "tab Lời thoại (tự lưu) → icon loa để tạo lại giọng (câu không đổi dùng lại, rất nhanh) → «‹ Về Duyệt».",
            "Đạt thì «Duyệt» (Ctrl+Enter, tự sang video chờ kế), không dùng thì «Bỏ qua».",
            "Bấm «Xuất (n)» trên cùng: chỉ xuất video đã duyệt, dùng lại phụ đề / giọng đã chuẩn bị. Sửa video đã xuất "
            "→ nó thành «Cần xuất lại», bấm «Duyệt» lại rồi «Xuất».",
        ]),
        ("h", "Tự động hoá thêm"),
        ("kv", [
            ("Tự dò chữ cứng", "Mục Che mờ → «Tự dò & che chữ cứng cho từng video»: máy tự tìm dải phụ đề in sẵn của mỗi video và "
                               "che mờ (bỏ qua logo / watermark đứng yên). Mẫu Review phim / Thuyết minh bật sẵn."),
            ("Thư mục theo dõi", "Mục Video → «Thư mục theo dõi»: chép video vào thư mục là tự vào hàng đợi, áp mẫu đã chọn, "
                                 "rồi tuỳ chọn tự chuẩn bị (chờ duyệt) hoặc tự xuất luôn."),
            ("Tên file xuất", "Mục Video → «Tên & nơi lưu file xuất»: thư mục xuất, «Tên file xuất» / «Thư mục con» với "
                              "mã {ten} {hauto} {mau} {ngay} {gio} {stt}, vd. {stt}_{ten} trong thư mục {mau}/{ngay}. "
                              "Lưu vào mẫu được."),
        ]),
    ]},
    {"key": "flow_dub", "title": "Video tiếng nước ngoài → tiếng Việt", "group": "Bắt đầu", "blocks": [
        ("p", "Bạn có video tiếng Trung, tiếng Anh… muốn có chữ Việt và giọng đọc tiếng Việt."),
        ("h", "Cách nhanh: dùng mẫu"),
        ("steps", [
            "Đưa video vào (kéo thả vào khung xem trước, hoặc tab Video › «＋ Nạp video»).",
            "Ô «Mẫu» trên cùng chọn «Review phim dọc 9:16» hoặc «Thuyết minh ngang 16:9» — mẫu bật sẵn: nghe lời thoại → "
            "dịch sang tiếng Việt → lồng tiếng → phụ đề kiểu TikTok → khử giọng gốc → che chữ cứng.",
            "Bấm «Chuẩn bị» trên cùng, xem từng video ở tab Duyệt rồi «Xuất». Muốn tự dịch bên ngoài, hoặc soát bản dịch "
            "trước khi tạo giọng: đổi ô «Chạy đến» cạnh nút chính.",
        ]),
        ("warn", f"Cần cài một lần: bộ nghe lời thoại (Cài đặt › AI trên máy, ~{INSTALL_GB} GB) và API key Gemini để dịch "
                 "(Cài đặt › AI dùng API key, miễn phí tại aistudio.google.com). Thiếu thì máy báo ngay khi bấm chạy."),
        ("h", "Tự chỉnh từng bước (không dùng mẫu)"),
        ("steps", [
            "Tab Nhận diện: «Ngôn ngữ video» để «Tự nhận diện», bấm «Tạo phụ đề cho video đang chọn».",
            "Tab Lời thoại: «Dịch sang» = Tiếng Việt, bấm «Dịch phụ đề». Sửa câu nào thì sửa ngay ở đầu tab.",
            "Tab Lồng tiếng: bật «Thêm lồng tiếng vào video», «Đọc từ» = Bản dịch, chọn giọng, bấm «Nghe thử giọng».",
            "Tab Lồng tiếng › Âm thanh: bật «Khử giọng nói gốc (AI)» (bỏ giọng Trung / Anh, giữ nhạc nền & tiếng động), «Âm gốc "
            "/ nền» khoảng 70, «Lồng tiếng» khoảng 150.",
            "Video có chữ nước ngoài in sẵn: tab Che mờ bật «Tự dò & che chữ cứng», hoặc bấm B vẽ một ô đè lên.",
            "Bấm «Xuất video».",
        ]),
        ("warn", "Câu tiếng Việt thường dài hơn câu gốc, nên giọng đọc phải nhanh hơn một chút. Máy tự chỉnh, câu vẫn "
                 "dài quá thì AI tự rút gọn. Muốn gọn hơn nữa: tab Lời thoại, ô «Nhờ AI sửa» gõ «ngắn gọn hơn»."),
    ]},
    {"key": "flow_short", "title": "Video ngang → video dọc TikTok", "group": "Bắt đầu", "blocks": [
        ("steps", [
            "Đưa video vào.",
            "Trên khung xem trước, bấm ô đầu tiên (đang ghi 16:9 hoặc Gốc) → chọn «9:16 (Dọc)».",
            "Bấm ô «Cắt vừa khung» → chọn «Nền mờ (blur)». Video sẽ nằm giữa, trên dưới là hình mờ. (Chọn «Cắt vừa khung» nếu muốn phóng to cắt hai bên.)",
            "Muốn cắt thành nhiều clip ngắn: mục Cắt → «＋ Thêm đoạn», gõ giây bắt đầu và kết thúc, chọn «Xuất mỗi đoạn thành 1 file».",
            "Bấm «Xuất video».",
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
            "Tỉ lệ (ngang 16:9, dọc 9:16…), kiểu khung (nền mờ trên-dưới, viền đen, cắt vừa khung), độ phân giải: chọn "
            "trên thanh của khung xem trước. Phần «Khung hình» ở đây: phóng to, «Canh dọc» / «Canh ngang» để dời video "
            "trong khung (vd nền mờ mà muốn video cao hơn chừa chỗ phụ đề), tốc độ.",
            "Âm thanh (âm gốc / nền, khử giọng gốc, to nhỏ lồng tiếng) nằm ở tab Lồng tiếng.",
        ]),
        ("kv", [
            ("Nâng cao xuất file › Tăng tốc GPU", "Để bật. Máy tự chọn card màn hình để làm nhanh; không có card thì tự dùng "
                                                 "CPU, chỉ chậm hơn thôi."),
            ("Chuẩn nén", "Để H.264 là an toàn nhất, mở được ở mọi nơi."),
            ("Độ nét (CRF)", "Số nhỏ = đẹp hơn nhưng file to hơn. 18–23 là vừa (mặc định đã hợp lý)."),
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
        ("p", "Video là trung tâm: khung xem trước bên phải luôn hiện video đang duyệt, bảng bên trái tóm tắt video đó "
              "sẽ ra sao."),
        ("steps", [
            "Đổi video bằng ‹ › hoặc ô chọn (● chờ duyệt, ✓ đã duyệt, – bỏ qua).",
            "Đọc «Video này sẽ ra sao»: khung hình, lời thoại (bao nhiêu câu, đã dịch chưa), phụ đề, mấy người nói & ai "
            "lồng tiếng, âm thanh gốc, che mờ, nhạc nền, logo, cắt, hiệu ứng. Dòng tô vàng ⚠ là chỗ nên xem lại. Dòng «Câu cần "
            "nghe lại» → «Sửa ›» mở tab Lời thoại chỉ với các câu đó.",
            "Bấm ▶ Phát xem video. Thấy chỗ chưa ổn → bấm «Sửa ›» ở dòng đó → mở đúng tab tính năng. Chỉnh gì ở đó cũng CHỈ "
            "áp cho video đang duyệt (dải trên cùng ghi «✎ Chỉnh riêng»); muốn dùng cho mọi video thì bấm «Áp cho tất cả».",
            "Chỉnh xong bấm ▶ Phát xem lại → «‹ Về Duyệt» → «Duyệt» (hoặc Ctrl+Enter ở bất kỳ tab nào) → tự sang video chờ kế.",
            "Duyệt hết thì bấm «Xuất (n)» trên cùng. Nút ⋯ cạnh nút Duyệt: chuẩn bị lại video này, duyệt hết video đang chờ.",
        ]),
        ("tip", "Sửa câu thoại: «Sửa ›» ở dòng Lời thoại → tab Lời thoại (bấm ô câu là video nhảy tới câu đó, ▶ phát đúng câu, "
                "ô «Nhờ AI sửa» chỉ sửa đúng những câu liên quan)."),
    ]},
    {"key": "stt", "title": "Nhận diện: nghe video và ghi ra chữ", "group": "Từng mục", "blocks": [
        ("h", "Để làm gì?"),
        ("p", "Máy nghe lời nói trong video rồi gõ ra thành chữ, có kèm giờ giấc. Kết quả là file .srt — dùng để hiện phụ "
              "đề, để dịch, để lồng tiếng."),
        ("h", "Làm thế nào?"),
        ("steps", [
            "AI nghe lời thoại ngay trên máy (có card NVIDIA thì nhanh hơn nhiều), không cần mạng, không tốn lượt AI. "
            f"Lần đầu bấm «Cài bộ nghe lời thoại» (~{INSTALL_GB} GB, một lần).",
            "Video có phụ đề in sẵn (Douyin…): máy tự đọc chữ trên hình để sửa chỗ nghe sai (tên riêng) — không cần chọn.",
            "«Ngôn ngữ video» để «Tự nhận diện» — máy tự nhận. Ô này dùng chung cho bước dịch (ngôn ngữ gốc).",
            "Bước «Chuẩn bị» tự nhận diện video chưa có lời gốc. Nhận diện lại riêng một video: tab Lời thoại › «Nguồn & "
            "dịch» › «Nhận diện lại video này». Xem / sửa chữ ở tab «Lời thoại».",
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
            "Đọc lại ở tab «Lời thoại» (cả bảng là danh sách câu, lăn chuột để cuộn). Sửa trực tiếp — tự lưu; bấm icon loa "
            "trên danh sách để tạo lại giọng (chỉ câu nào sửa mới đọc lại).",
        ]),
        ("h", "Chỉ nghe các câu cần xem"),
        ("steps", [
            "Máy tự soát từng câu và gắn dấu ⚠ kèm lý do: giọng đọc sai (máy nghe lại vẫn lệch), giọng dự phòng, đọc quá "
            "nhanh / tràn khung, còn chữ gốc / chưa dịch / trống, xưng hô khác «Hồ sơ kịch bản».",
            "Bấm nút ⚠ (kèm số câu) trên danh sách: chỉ còn các câu đó. Rê chuột vào ⚠ ở mỗi câu để xem lý do, ▶ để nghe.",
            "Bấm «Tự sửa» (cạnh nút ⚠): AI sửa các câu ⚠ đúng lý do — rút gọn câu dài cho vừa khung, dịch nốt câu chưa "
            "dịch, xưng hô theo hồ sơ. Chỉ một câu dài: bấm ✂ cạnh câu đó. Câu sửa tô vàng, ↶ để trả lại.",
            "Gõ tới đâu máy soát tới đó: câu vừa sửa mà dài quá khung hiện ngay «dài ~+0,6s» (ước lượng theo tốc độ đọc "
            "của video), chưa cần tạo giọng.",
            "Sửa xong câu nào thì dấu ⚠ của câu đó mất (câu vẫn ở danh sách để bạn nghe lại). Bấm icon loa tạo lại giọng, "
            "bấm lại nút ⚠ để hiện cả video.",
        ]),
        ("h", "Dịch chưa hay — nhờ AI sửa"),
        ("steps", [
            "Nhắn vào ô chat «Nhờ AI sửa» ở đáy tab «Lời thoại», ví dụ «xưng anh – em, văn nói», «sửa bạn mày thành chó nhà "
            "mày», «rà lại cả kịch bản cho thống nhất». AI đọc cả video, tự tìm mọi câu cần sửa (câu hỏi lẫn câu trả lời), "
            "câu khác giữ nguyên. Nhắn tiếp được như chat («còn câu 37 nữa») — AI nhớ các lượt trước và các câu vừa đổi.",
            "Chỉ một câu dở: bấm ↻ cạnh câu đó để AI dịch lại riêng câu ấy (vừa khung giọng của câu).",
            "Câu AI sửa được tô vàng (đã tự lưu), dòng nhỏ ghi câu cũ; bấm ↶ cạnh câu để trả lại, «Hoàn tác» trả lại hết; "
            "ưng thì bấm icon loa để tạo lại giọng.",
            "Dịch / soát bằng AI bên ngoài: icon copy (trên danh sách câu) chép lời gốc hoặc song ngữ kèm lời dặn → dán vào "
            "ChatGPT / Gemini web → copy phần SRT AI trả về → icon dán. Máy giữ mốc thời gian gốc nên không lệch video.",
            "Đang duyệt: từ tab Duyệt bấm «Sửa ›» ở dòng Lời thoại để tới đây.",
            "Bản sửa được nhớ lại: lần sau dịch video có câu giống vậy sẽ dùng bản mới.",
        ]),
        ("kv", [
            ("Tìm & thay", "Đổi một tên / chữ ở mọi câu một lần (nút cạnh «Nhờ AI sửa»)."),
            ("⊘ Không đọc câu này", "Lời bài hát, câu trích muốn giữ tiếng gốc: bấm ⊘ ở câu đó — câu không lồng tiếng, chỗ "
                                    "đó giữ âm gốc (kể cả khi khử giọng), phụ đề vẫn hiện."),
            ("Dịch bằng AI bên ngoài", "Tab «Dịch» › Tệp phụ đề: «Xuất SRT › Lời gốc» → gửi cho ChatGPT / Gemini web… "
                                       "(«Xuất SRT › Chép lời dặn» để kèm lời dặn) → chép SRT AI trả về → «Nhập bản dịch "
                                       "› Dán SRT…». Máy giữ mốc thời gian gốc nên không lệch video. Gõ tay từng câu: "
                                       "«Nhập bản dịch › Dịch tay từng câu»."),
            ("Dịch dở giữa chừng", "Mất mạng hay model hết lượt? Mỗi lô dịch đều được lưu — bấm «Thử lại» là dịch tiếp phần "
                                   "còn lại, không dịch lại từ đầu."),
            ("Từ điển tên riêng (trong Cài đặt)", "Gõ «tên gốc=tên Việt» mỗi dòng để AI dịch đúng tên riêng, ví dụ 松下=Panasonic."),
        ]),
    ]},
    {"key": "tts", "title": "Lồng tiếng: máy đọc chữ thành tiếng", "group": "Từng mục", "blocks": [
        ("h", "Để làm gì?"),
        ("p", "Máy đọc phụ đề thành giọng nói và ghép vào video, đúng lúc với từng câu."),
        ("h", "Làm thế nào?"),
        ("steps", [
            "Bật «Thêm lồng tiếng vào video».",
            "«Đọc từ»: chọn «Bản dịch» (đọc tiếng Việt) hoặc «Lời gốc».",
            "Chọn «Bộ đọc»: VieNeu (mặc định, khuyên dùng) — 25 giọng Việt 3 miền chạy ngay trên máy, miễn phí, không "
            "giới hạn lượt; máy đọc liền vài câu kề nhau cho tự nhiên rồi tách lại, câu đọc chưa đạt tự đọc lại. Edge TTS "
            "miễn phí (HoaiMy nữ, NamMinh nam + giọng 🌐 đa ngôn ngữ, cần mạng) — cũng đọc tạm câu VieNeu không đọc được.",
            "Chọn giọng, bấm «Nghe thử giọng». Chỉnh «Tốc độ đọc» nếu muốn.",
            "Giọng tự khớp với hình, nhịp đọc đều: câu giọng đọc tự nói nhanh bất thường được đọc chậm lại; các vế của một "
            "câu đọc nối liền; câu tràn thì AI rút gọn, câu đọc xong sớm (để trống) thì AI nói đầy đủ hơn — rồi đọc lại. "
            "Chỗ video gốc tự ngừng nói: nhạc / tiếng nền gốc dâng nhẹ lên lấp (âm gốc 0 % thì không).",
            "Muốn nghe trước: khối «Giọng của video đang chọn» bấm «Tạo giọng cho video này». Hoặc bấm «Xuất video» trên "
            "cùng. Giọng đã tạo được giữ lại, lần sau xuất lại rất nhanh.",
        ]),
        ("kv", [
            ("Giọng đã tạo", "Giữ nguyên giọng đã có (chỉ đổi nhạc / phụ đề / hiệu ứng): chọn một giọng đã tạo — xuất video "
                             "dùng thẳng file đó, không đọc lại."),
            ("Nén tối đa", "Máy được phép đọc nhanh tới mức này khi câu dài, tính cả «Tốc độ đọc» — tối đa 1,25 "
                           "để giọng không méo. Câu vẫn dài quá: máy nhờ AI rút gọn bản dịch rồi đọc lại."),
            ("Nghỉ giữa câu", "Khoảng lặng tối thiểu giữa hai câu thoại."),
        ]),
        ("h", "Video có nhiều người nói"),
        ("steps", [
            "Cần có phụ đề gốc trước (bấm «Chuẩn bị», hoặc «Nhận diện lại video này» ở Lời thoại › «Nguồn & dịch»).",
            "Tab Lồng tiếng bật «Mỗi người nói một giọng», bấm «Nhận diện người nói». Lần đầu máy tải model ~28 MB.",
            "Máy chia ra Người 1, Người 2… và tự gợi ý giọng (nam/nữ). Bấm «Nghe giọng gốc» để biết ai là ai, rồi chọn giọng "
            "cho từng người. Edge tiếng Việt chỉ có 2 giọng — người thứ ba dùng lại giọng nhưng đổi «Cao độ» cho khác đi.",
            "Máy nhận sai câu nào thì bấm «Gán từng câu…» sửa tay. Không nhận diện được thì cũng tự chia tay ở đây.",
            "Xuất hàng loạt: video nào chưa nhận diện người nói, máy tự nhận diện trước khi lồng tiếng.",
        ]),
    ]},
    {"key": "blur", "title": "Che mờ: giấu chữ, logo, mặt người", "group": "Từng mục", "blocks": [
        ("h", "Để làm gì?"),
        ("p", "Làm mờ hoặc che một vùng trên video, ví dụ dòng chữ tiếng Trung dính sẵn ở dưới, logo kênh khác, mặt người."),
        ("h", "Làm thế nào?"),
        ("steps", [
            "Trên khung xem trước bấm nút B.",
            "Đặt chuột vào góc trên bên trái chỗ cần che, giữ chuột kéo tới góc dưới bên phải, thả ra. Một ô màu cam xuất hiện.",
            "Kéo ô để dời, kéo mấy chấm ở cạnh để đổi to nhỏ.",
            "Chọn kiểu che: «Mờ (Gaussian)» tự nhiên nhất · «Ô vuông» kiểu pixel · «Che kín» màu đen. Kéo «Độ mờ» cho vừa mắt.",
            "Vẽ thêm ô khác nếu cần (mỗi ô một chỗ). Xoá ô: bấm ✕ đỏ ở góc ô, hoặc bấm phím Delete.",
        ]),
        ("kv", [
            ("Bắt đầu / Kết thúc (giây)", "Chỉ che trong khoảng này. Để 0 = che suốt video."),
            ("Mềm mép", "Viền ô mờ dần, nhìn tự nhiên hơn."),
            ("Nút «Vùng đáy»", "Tự đặt ô che sẵn ở dải phụ đề phía dưới."),
            ("Khung che (pixel)", "Gõ toạ độ chính xác — tính theo pixel của video gốc."),
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
            "Nhạc mới thêm phát từ đầu video. Muốn chỉ chạy một đoạn: tua tới chỗ cần rồi bấm «Đang xem» ở «Bắt đầu» / "
            "«Kết thúc». Để «Lặp» bật nếu nhạc ngắn hơn video.",
            "Để «Tự hạ nhạc khi có tiếng nói / giọng đọc» bật.",
        ]),
        ("tip", "Bỏ nhạc bạn hay dùng vào thư mục app\\assets\\music, rồi bấm «Thư viện nhạc» là thấy ngay."),
    ]},
    {"key": "brand", "title": "Logo, chữ, watermark", "group": "Từng mục", "blocks": [
        ("h", "Để làm gì?"),
        ("p", "Đóng dấu kênh của bạn lên video: hình logo, dòng chữ tên kênh, chữ mờ chống người khác lấy video, viền màu."),
        ("h", "Làm thế nào?"),
        ("steps", [
            "Thẻ «Logo»: bật, bấm «Chọn ảnh logo…» (PNG nền trong suốt là đẹp nhất). Chọn Vị trí (góc nào), «Cỡ logo», «Độ đậm».",
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
            "Cách nhanh: xem video trên khung xem trước tới chỗ muốn bắt đầu, bấm phím I. Xem tới chỗ muốn kết thúc, bấm phím O.",
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
            "Chọn «Bộ lọc màu» hoặc chỉnh ô bất kỳ — công tắc «Bật chỉnh màu / hiệu ứng» tự bật.",
            "«Bộ lọc màu»: Ấm áp, Điện ảnh, Tươi sáng… Nhìn khung xem trước thấy đổi ngay. Kéo Sáng / Tương phản nếu muốn chỉnh thêm.",
            "Video bị nằm ngang / ngược? Chọn «Xoay» 90° hoặc 180°.",
            "Cảnh tĩnh, ít chuyển động? Chọn «Máy quay chuyển động» = Zoom vào để hình từ từ phóng to cho đỡ chán.",
            "Đăng lại video của chính bạn lên kênh khác: bấm «Bật nhanh chống trùng» ở trên cùng (video khác bản gốc một "
            "chút — không đảm bảo qua được bộ dò trùng của nền tảng).",
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
            "Cần API key Gemini (Cài đặt › AI dùng API key; model viết / ảnh chọn ở tab Truyện). Viết truyện thì miễn phí; vẽ ảnh AI cần tài khoản có bật thanh toán — chưa có thì máy dùng ảnh tạm (chữ trên nền màu).",
            "Gõ ý tưởng vào ô «Chủ đề» rồi bấm «Chạy tất cả»: AI viết kịch bản → đọc giọng → vẽ ảnh → ghép video. Độ dài, giọng văn, khung hình, giọng kể: mở các khối «Kịch bản», «Hình ảnh», «Giọng kể» bên dưới.",
            "Muốn sửa trước khi làm tiếp: mở khối «Từng bước», bấm «1 · Viết kịch bản» → «Trình soạn kịch bản» để đọc lại từng cảnh, sửa lời kể, chọn giọng cho nhân vật.",
            "Rồi bấm lần lượt «2 · Tạo giọng đọc» → «3 · Tạo ảnh» (nếu có quota) → «5 · Ghép thành video».",
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
            ("Edge TTS báo «No audio was received»", "Dịch vụ đang bận. Máy tự thử lại; câu nào vẫn lỗi sẽ ghi trong log — bấm «Thử lại» là được."),
            ("Không biết máy thiếu gì", "Cài đặt → nút «Kiểm tra máy»: liệt kê GPU, CUDA, model, giọng, key — mục nào thiếu có ghi bấm đâu để tải."),
            ("Gemini báo hết lượt / quá tải", "Máy dừng và báo (không tự đổi model). Bấm «Chọn model…» để chọn model khác, hoặc "
                                              "đợi hạn mức đặt lại (≈ 14–15 giờ chiều). Đã dịch được lô nào thì «Thử lại» dịch "
                                              "tiếp phần còn lại. Tạo ảnh/video AI cần tài khoản trả tiền."),
            ("Video bị xoay sai", "Mục Hiệu ứng → Xoay."),
            ("Giọng gốc (Trung, Anh…) vẫn nghe thấy", "Tab Lồng tiếng › Âm thanh: bật «Khử giọng nói gốc (AI)». AI bỏ giọng nói, giữ nhạc nền & tiếng động (lần đầu tải model ~64 MB, mỗi video khử một lần ~1 phút/phút video)."),
        ]),
    ]},
]

GROUPS = ["Bắt đầu", "Từng mục", "Khác"]


def topic(key: str) -> dict | None:
    return next((t for t in TOPICS if t["key"] == key), None)
