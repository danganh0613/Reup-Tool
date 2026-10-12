"""Điều khiển Edit Studio từ ngoài (CLI / MCP / agent DeepSeek) với ít token nhất.

App đang mở là chủ DUY NHẤT của hàng đợi: nó mở một cổng lệnh cục bộ (127.0.0.1, có mã bí mật — ``control``); CLI và
MCP gửi lệnh vào đó (app chưa mở thì tự mở) → không có hai tiến trình tranh file, người dùng thấy ngay kết quả để duyệt.
Năm lệnh thô (``ops``): ``videos`` · ``run`` · ``review`` · ``fix`` · ``settings`` — mỗi lệnh trả JSON gọn (không đường
dẫn, không log, không cả SRT; danh sách có giới hạn + «còn n»)."""
