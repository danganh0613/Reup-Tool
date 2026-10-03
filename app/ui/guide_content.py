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
            "Bấm nút xanh «Render MP4» ở góc trên bên phải. Chờ thanh tiến trình ở dưới chạy hết 5 ô. Xong thì bấm «Thư mục xuất» để lấy video.",
        ]),
        ("h", "Mấy nút trên màn hình giữa"),
        ("kv", [
            ("Các ô nhỏ 16:9 · Gốc · Gốc · Cắt vừa khung", "Bấm vào để chọn khung video: 16:9 là video ngang (YouTube), 9:16 là video dọc (TikTok)."),
            ("V · T · B", "V: kéo hình đi. T: kéo ô chữ phụ đề. B: vẽ ô che mờ."),
            ("Sub · Blur", "Tạm giấu chữ / ô che để nhìn hình cho rõ. Không ảnh hưởng video xuất ra."),
            ("Phát", "Xem thử video có tiếng."),
            ("Xóa · phím Delete", "Xoá ô che hoặc ô chữ đang chọn. Lỡ tay thì bấm Ctrl+Z để lấy lại."),
        ]),
        ("tip", "Bạn không cần bấm lưu. Mọi thứ tự lưu, tắt máy mở lại vẫn còn."),
    ]},
    {"key": "flow_dub", "title": "Video tiếng nước ngoài → tiếng Việt", "group": "Bắt đầu", "blocks": [
        ("p", "Bạn có video tiếng Trung, tiếng Anh… muốn có chữ Việt và giọng đọc tiếng Việt. Làm theo thứ tự này, "
              "mỗi bước bấm ở một mục bên cột trái."),
        ("steps", [
            "Mục Video: đưa video vào.",
            "Mục STT: bấm «Tạo SRT từ Video trong hàng đợi». Máy nghe video rồi ghi ra chữ (tiếng gốc). Chờ vài chục giây. "
            "Máy nào có card NVIDIA thì chọn Model = large-v3-turbo cho chữ đúng nhất.",
            "Mục SRT: bật «Dịch AI», chọn «Tiếng Việt», bấm «Dịch». Cần có API key Gemini (miễn phí, lấy tại aistudio.google.com, "
            "dán vào Cài đặt). Muốn sửa câu nào thì bấm «Xem / Sửa SRT».",
            "Mục Phụ đề: chọn «Sub đã dịch». Chữ Việt hiện trên video. Muốn dời chữ thì bấm T rồi kéo ô chữ.",
            "Mục TTS: bật «Thêm lồng tiếng vào video», chọn giọng, bấm «Nghe thử giọng» xem có thích không. Để «Khớp mềm» bật.",
            "Mục Che mờ (nếu video có chữ tiếng nước ngoài dính sẵn trên hình): bấm B rồi vẽ một ô đè lên dòng chữ đó.",
            "Mục Video: kéo «Âm gốc» xuống khoảng 20–30 (để tiếng gốc nhỏ), «Lồng tiếng» khoảng 150. Rồi bấm «Render MP4».",
        ]),
        ("warn", "Câu tiếng Việt thường dài hơn câu gốc, nên giọng đọc phải nhanh hơn một chút. Máy tự chỉnh. "
                 "Nếu cuối video bị mất tiếng: vào Cài đặt, ô «Phong cách dịch» gõ «dịch thật ngắn gọn», dịch lại."),
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
            "Phần «Âm thanh»: «Âm gốc» là tiếng có sẵn trong video, «Lồng tiếng» là giọng máy đọc. Có lồng tiếng thì để âm gốc 20–30.",
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
            "Dời chữ: bấm nút T trên màn hình giữa, rồi kéo cái ô viền xanh. Kéo góc ô để đổi to nhỏ.",
            "Chỉnh chữ: chọn Font, Cỡ chữ, Màu, Viền, Nền. Muốn nổi bật thì bật «Nền» kiểu «Hộp mờ bo góc».",
            "«Karaoke»: chữ đổi màu theo lời đọc, giống karaoke.",
        ]),
        ("tip", "Chữ hiện sớm hay muộn hơn tiếng? Chỉnh ô «Trễ phụ đề» (số dương = chữ hiện muộn hơn)."),
    ]},
    {"key": "stt", "title": "STT: nghe video và ghi ra chữ", "group": "Từng mục", "blocks": [
        ("h", "Để làm gì?"),
        ("p", "STT nghĩa là máy nghe lời nói trong video rồi gõ ra thành chữ, có kèm giờ giấc. Kết quả là file .srt — "
              "dùng để hiện phụ đề, để dịch, để lồng tiếng."),
        ("h", "Làm thế nào?"),
        ("steps", [
            "Chọn «Nhận dạng» = Whisper (chạy ngay trên máy, không cần mạng sau lần đầu) hoặc Gemini (qua mạng, cần API key, không cần card màn hình).",
            "Với Whisper, chọn Model: máy có card NVIDIA → «large-v3-turbo» (đúng nhất). Máy thường → «small». Lần đầu máy sẽ tải model, chờ vài phút.",
            "Ngôn ngữ để «auto» — máy tự nhận.",
            "Bấm «Tạo SRT từ Video trong hàng đợi». Xong sẽ thấy dòng «SRT: … cue». Bấm «Mở SRT» để xem chữ.",
            "Chữ sai chỗ nào, vào mục SRT → «Xem / Sửa SRT» để sửa tay.",
        ]),
        ("kv", [
            ("Thiết bị = auto", "Máy tự thử card NVIDIA trước, không được thì dùng CPU."),
            ("Nút «Tải / Cài CUDA runtime»", "Chỉ hiện khi máy có card NVIDIA nhưng còn thiếu phần mềm đi kèm. Bấm một lần, chờ tải (~1.3 GB, cần Internet), từ đó nghe nhanh gấp 5–10 lần. Bản Setup đủ đã có sẵn, không cần bấm."),
            ("Nút «Tải trước model Whisper»", "Tải model đang chọn về máy ngay (lần nhận dạng đầu khỏi chờ, dùng được khi không có mạng)."),
            ("PC yếu", "Bật nếu máy chậm, hay bị treo."),
        ]),
    ]},
    {"key": "srt", "title": "SRT: dịch chữ sang tiếng khác, sửa chữ", "group": "Từng mục", "blocks": [
        ("h", "Để làm gì?"),
        ("p", "Dịch file chữ (.srt) sang tiếng Việt hoặc tiếng khác bằng AI. Sửa lại câu nào chưa hay."),
        ("h", "Làm thế nào?"),
        ("steps", [
            "Cần API key một lần: mở Cài đặt (nút trên cùng), cột giữa «API keys Gemini», dán key vào, bấm «Lưu tất cả». "
            "Key lấy miễn phí ở aistudio.google.com → «Get API key».",
            "Bật «Dịch AI», chọn ngôn ngữ đích (Tiếng Việt).",
            "Ô «Phong cách dịch» (không bắt buộc): gõ ý bạn muốn, ví dụ «xưng tôi – các bạn, ngắn gọn».",
            "Bấm «Dịch». Chờ khoảng 30 giây cho video 5 phút. Xong thấy «Dịch xong».",
            "Bấm «Xem / Sửa SRT» để đọc lại. Sửa trực tiếp, bấm «Lưu». Chỉ câu nào sửa thì lần sau mới đọc lại giọng câu đó.",
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
            "Chọn «Nhà cung cấp»: Edge TTS (miễn phí, cần mạng, có giọng HoaiMy nữ và NamMinh nam) là đủ tốt. "
            "VieNeu là giọng Việt chạy trên máy. ElevenLabs/Google hay hơn nhưng cần key trả tiền.",
            "Chọn giọng, bấm «Nghe thử giọng». Chỉnh «Tốc độ đọc» nếu muốn.",
            "Để «Khớp mềm» bật: giọng đọc tự nhiên, máy tự dồn cho vừa video.",
            "Bấm «Render MP4» ở trên. Giọng đã tạo được giữ lại, lần sau render lại rất nhanh.",
        ]),
        ("kv", [
            ("Thư viện voice", "Giọng đã tạo trước đây. Chọn để dùng lại, khỏi tạo mới."),
            ("Tốc độ tối đa", "Máy được phép đọc nhanh tới mức này khi câu dài. 1.3–1.5 là vẫn dễ nghe."),
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
            ("Chờ mãi không xong", "Nhìn thanh tiến trình dưới cùng và bấm «Xem log». Bấm «Dừng» nếu muốn huỷ."),
            ("Báo NVENC không dùng được", "Không sao, máy tự chuyển sang cách khác. Muốn nhanh hơn thì cập nhật driver NVIDIA mới nhất."),
            ("Nghe video (STT) chậm dù có card NVIDIA", "Mục STT → bấm «Tải CUDA runtime» một lần (hoặc cài bản Setup đủ)."),
            ("Giọng đọc bị mất ở cuối video", "Câu dịch quá dài. Vào Cài đặt gõ «dịch thật ngắn gọn» vào Phong cách dịch rồi dịch lại; hoặc mục TTS tăng «Tốc độ tối đa»; hoặc mục Video bật «Lặp video cho đủ giọng»."),
            ("Edge TTS báo «No audio was received»", "Dịch vụ đang bận. Máy tự thử lại; câu nào vẫn lỗi sẽ ghi trong log — bấm Render lại là được."),
            ("Không biết máy thiếu gì", "Cài đặt → nút «Kiểm tra máy»: liệt kê GPU, CUDA, model, giọng, key — mục nào thiếu có ghi bấm đâu để tải."),
            ("Gemini báo 429 / hết hạn mức", "Đợi 1 phút rồi thử lại, hoặc dán thêm key khác. Tạo ảnh/video AI cần tài khoản trả tiền."),
            ("Video bị xoay sai", "Mục Hiệu ứng → Xoay."),
            ("Bản .exe không có VieNeu / Demucs", "Hai thứ này rất nặng, chỉ có ở bản chạy bằng Python (run.bat)."),
        ]),
    ]},
]

GROUPS = ["Bắt đầu", "Từng mục", "Khác"]


def topic(key: str) -> dict | None:
    return next((t for t in TOPICS if t["key"] == key), None)
