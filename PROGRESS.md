# PROGRESS – Nhật ký tiến độ task-app

> **Phiên mới: đọc file này trước**, rồi đọc [README.md](README.md) (kiến trúc, quy tắc, bảng use case).
> Cập nhật file này khi kết thúc mỗi giai đoạn. Quy trình mỗi giai đoạn:
> kế hoạch → người dùng duyệt → làm → test → review → người dùng nghiệm thu.

## Lộ trình (người dùng chốt 2026-11-11)

| GĐ | Nội dung | Trạng thái |
|---|---|---|
| 0 | Nền test & DB thật | Đã nghiệm thu 2026-11-11 |
| 1 | Bảo mật nền: CSRF Flask-WTF, logout POST, cookie SECURE/SAMESITE, seed chỉ chạy ở dev | Đang chờ nghiệm thu |
| 2 | Tài khoản: `auth_service`, reset mật khẩu thật, パスワード変更 cho 市民, đăng ký 職員 lưu DB (`users.department_id`), xoá tài khoản thử taro/staff01/admin01 ở local và EC2 | Chưa làm |
| 3 | Quản trị: ユーザー一覧, khoá/mở tài khoản, quản lý 部署/カテゴリ, bỏ dữ liệu viết cứng ở ポータル | Chưa làm |
| 4 | 市民 còn thiếu: route trả ảnh có kiểm quyền, 対応状況を表示, 報告を取り消す | Chưa làm |
| 5 | 職員 còn thiếu: 担当部署, AI再実行, `urgency_code` + `_urgency_badge`, phân trang, tự phát hiện trùng | Chưa làm |
| 6 | Chất lượng giao diện: responsive 職員, trang lỗi, dọn JS inline, a11y; gom màu 緊急度 (gồm `staff/map.html`, `static/js/map.js`), xoá `mockup_report_triage.html` | Chưa làm |

## Giai đoạn 0 – Nền test & DB thật (2026-11-11)

**Đã làm**
- Người dùng tạo DB `saitama_help_test` bằng role postgres (`saitama_app` không có CREATEDB) và thêm `TEST_DATABASE_URL` vào `.env`.
- `tests/conftest.py`: thêm guard (tên DB test phải kết thúc bằng `_test`) và fixture `login`.
- `tests/test_smoke_routes.py` (mới): 8 hàm, 23 ca. Gồm đăng ký/đăng nhập 市民, gửi báo cáo, 職員 cập nhật trạng thái rồi 市民 thấy kết quả, phân quyền 403/302, trang complete của người khác trả 404. Assert đã làm chặt theo review.
- `README.md`: sửa 11 chỗ lệch với code.
- `flask --app run db check` trên DB dev: "No new upgrade operations detected." (model khớp migration, head `069ae4f17650`).

**Kết quả test thật**
- Lần đầu chạy với DB thật: 119 test cũ đều pass, không phải sửa test cũ nào.
- Hiện tại: **142 passed, 0 failed, 0 skipped**, khoảng 115 giây.
- Lệnh: `$env:PYTHONUTF8=1; .venv\Scripts\python.exe -m pytest -q -rs`
- Không chạy 2 tiến trình pytest song song trên cùng DB test.
- Review test (test-reviewer): ĐẠT.

**Chưa làm được**
- Kiểm tra luồng trên Chrome (phiên không có tích hợp Claude in Chrome) → người dùng tự thử theo hướng dẫn bên dưới. Không tạo dữ liệu thử trong DB dev.

**Cảnh báo bảo mật (xử lý ở GĐ 2)**
- Trên EC2 https://team3app.duckdns.org, tài khoản thử `taro@example.com`, `staff01`, `admin01` vẫn đăng nhập được bằng mật khẩu thử `password123` (đã công khai trong README; kiểm tra 2026-11-11).

**Việc để sau**
- `test_auth.py` còn trống: sai mật khẩu, tài khoản bị khoá, `@` trong ID, logout.
- Test cho chính guard của conftest.
- Tăng tốc test: mỗi test khoảng 1 giây do `create_all`/`drop_all`.
- Phân quyền chéo staff ↔ admin.
- Quyết định có giữ `docs/` trong `.gitignore` hay không.

## Giai đoạn 1 – Bảo mật nền (2026-11-11)

**Đã làm**
- Backend: `CSRFProtect` (`app/extensions.py`, `csrf.init_app` trong `app/__init__.py`); error handler `CSRFError` flash `MSG_CSRF_EXPIRED` (cảnh báo vàng) rồi redirect về trang trước bằng `_previous_page_path()` (chỉ lấy path+query của Referer, chống open redirect; mã hoá lại ký tự điều khiển và dấu cách bằng `quote`, `%xx` sẵn có giữ nguyên); request có `X-Replace-History` nhận JSON `{redirect}`.
- `config.py`: `WTF_CSRF_TIME_LIMIT=None`, `WTF_CSRF_SSL_STRICT`, `SESSION_COOKIE_SECURE` (từ `.env`), HttpOnly và SameSite=Lax cho session và remember cookie. `_env_bool`: `1/true/yes/on` và `0/false/no/off`, rỗng là default, giá trị khác thì `ValueError` (gõ sai trên EC2 làm app không khởi động, chủ ý).
- Đăng xuất là `POST /logout` (GET trả 405). `scripts/seed.py`: mặc định chỉ tạo master, tài khoản thử chỉ khi `--with-test-users`. `.env.example` thêm `SESSION_COOKIE_SECURE`, `WTF_CSRF_SSL_STRICT`.
- Frontend: `csrf_token` trong 32 form POST (19 form + 13 nút đăng xuất), `components/_flash.html` thêm vào 10 trang để hiện thông báo CSRF.
- Tester: `tests/test_security.py` (101 ca) và fixture `csrf_app`, `csrf_client`, `csrf_login`, helper `get_csrf_token` trong `tests/conftest.py`.
- Review (security-reviewer): ĐẠT, 3 điểm thấp. Đã sửa 2 điểm (Referer chứa ký tự điều khiển; `_env_bool` im lặng với giá trị lạ). Điểm 3 ghi ở "Chưa làm được" bên dưới.
- Tài liệu: README mục 1, 4, 6, 7 (bước 3 ✅), 8.

**Kết quả test thật** (2026-11-11, chạy tuần tự toàn bộ bằng .venv): `243 passed, 2 warnings in 203.80s` — 0 failed, 0 skipped. 142 test cũ + 101 ca trong `tests/test_security.py` (mới). 2 warning là DeprecationWarning `datetime.utcnow()` trong flask_login (thư viện ngoài).
- Test chống hồi quy có số cố định: 32 form POST (≥32) và 13 form logout trong templates. Thêm màn hình có form thì phải cập nhật `test_every_post_form_in_templates_has_csrf_token`.
- Review: security-reviewer ĐẠT (3 điểm thấp: 2 đã sửa — mã hoá lại Referer, `_env_bool` báo lỗi khi giá trị lạ; 1 ghi chú WTF_CSRF_SSL_STRICT/ProxyFix để GĐ2). test-reviewer CẦN SỬA nhẹ → đã sửa (B1–B7); các mục thấp còn lại không làm (TIME_LIMIT hành vi, reload config, seed một phần).
- arch_check: 0 lỗi, 0 cảnh báo.

**Chi phí**: ước tính $3.62 (2.17–5.79), ngân sách $6.5; thực tế theo sổ token **≥ $2.63** (cận dưới: sổ thiếu phiên PM lập kế hoạch và vòng sửa backend cuối).

**Chưa làm được / lệch kế hoạch**
- `WTF_CSRF_SSL_STRICT` chưa có tác dụng thực tế sau Nginx: chưa có ProxyFix nên `request.is_secure` là False. Token vẫn được kiểm đầy đủ. Khi làm ProxyFix ở GĐ2 phải kiểm lại Host/Origin.
- Chưa kiểm tra trên trình duyệt thật (footer modal ログアウト, 375px) — người dùng tự xem (xem hướng dẫn thử bên dưới; đăng xuất giờ là nút POST).

**Việc người dùng làm khi deploy EC2**
- Thêm `SESSION_COOKIE_SECURE=true` vào `.env` của EC2. Giá trị gõ sai (ví dụ `ture`) làm app không khởi động, đó là chủ ý.
- Không chạy seed với `--with-test-users` trên server.
- Trang đang mở sẵn lúc deploy sẽ báo 「セッションの有効期限が切れました。もう一度操作してください。」 một lần khi gửi form; chỉ cần tải lại trang.

**Việc còn lại (giai đoạn sau)**
- Rate limit cho đăng nhập và đăng ký.
- ProxyFix (và kiểm lại Host/Origin cho CSRF).
- Xoá tài khoản thử trên EC2 (`password123` vẫn đăng nhập được), cùng việc xoá taro/staff01/admin01 ở GĐ2.
- Đổi session sau khi đăng nhập (session fixation, rủi ro thấp).

**Ghi chú môi trường test (tester nhờ ghi lại)**
- Fixture `app` giữ một app context suốt test, nên `g.csrf_token` dùng chung giữa các client; muốn có token của "session khác" phải `g.pop('csrf_token')`.
- Fixture CSRF: `csrf_app`, `csrf_client`, `csrf_login`, helper `get_csrf_token(client, path)`.
- `make_report` không tạo `report_status_histories` (lịch sử bắt đầu từ 0).
- Gọi seed an toàn trong test: `scripts.seed.seed()` trên app test, và `main()` với `create_app` monkeypatch. Không chạy `python -m scripts.seed` trong test.

## Hướng dẫn thử như khách hàng

Chạy local: `python run.py` (interpreter `task-app\.venv\Scripts\python.exe`), mở http://localhost:5000.

1. `/register`: đăng ký 市民 bằng email tự chọn → trang hoàn tất.
2. `/login/citizen`: đăng nhập → マイページ.
3. 報告する: gửi form trống → thông báo đỏ.
4. Điền đủ, đính ảnh JPEG (có GPS càng tốt), gửi → màn hoàn tất hiện 緊急度.
5. 履歴 (`/citizen/reports`): báo cáo mới ở trạng thái 未対応.
6. Thử file .txt hoặc ảnh >10MB → báo lỗi, không ra 500.
7. Đăng xuất (nút POST), đăng nhập `/login/staff` bằng `staff01` / mật khẩu thử trong README (tài khoản thử chỉ có sau `python -m scripts.seed --with-test-users`) → dashboard có báo cáo mới.
8. 報告一覧 → 詳細: đổi sang 対応中 kèm ghi chú → thông báo thành công. Gửi ghi chú rỗng → cảnh báo.
9. Mở 地図, 統計 (thử tải PDF/CSV), AI分析.
10. Đăng nhập lại 市民: 履歴 hiện 対応中. Gõ `/staff/dashboard` → 403.

**Lưu ý môi trường**
- `.env` có `GEMINI_API_KEY` nên mỗi báo cáo gọi Gemini thật (khoảng $0.001). Đặt `AI_PROVIDER=rule_based` nếu không muốn gọi.
- Key Google Maps phải cho phép `localhost` (chưa kiểm chứng).
- Camera/GPS chỉ chạy trên localhost hoặc HTTPS.
- Phạm vi file của các agent nằm ở `.claude/hooks/lib/workflow.py` (SCOPES); chỉ người dùng sửa được.

## Phần còn "giả" hoặc chưa có

- Reset mật khẩu (3 màn) chỉ chuyển trang, không đổi mật khẩu.
- 新規職員アカウント登録 không lưu; ポータル hiện danh sách viết cứng.
- 市民 chưa xem được chi tiết 対応状況, chưa 取り消し được, chưa có パスワード変更.
- 職員 không thấy ảnh đính kèm, chưa có 担当部署設定, nút AIモデル再実行 không chạy, 重複確認 trống.
- Chưa có trang lỗi 403/404/500 riêng. (CSRF và logout POST đã có từ GĐ1.)
- Giao diện 職員 trên điện thoại: sidebar có thể chiếm cả màn hình (chưa kiểm chứng).

## Mẫu cho giai đoạn mới

```
## Giai đoạn N – <tên> (<ngày>)
**Đã làm**: <việc> / <file đã đổi>
**Kết quả test thật**: <lệnh> → <passed/failed/skipped>
**Chưa làm được / lệch kế hoạch**: ...
**Việc còn lại**: ...
```
