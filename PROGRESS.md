# PROGRESS – Nhật ký tiến độ task-app

> **Phiên mới: đọc file này trước**, rồi đọc [README.md](README.md) (kiến trúc, quy tắc, bảng use case).
> Cập nhật file này khi kết thúc mỗi giai đoạn. Quy trình mỗi giai đoạn:
> kế hoạch → người dùng duyệt → làm → test → review → người dùng nghiệm thu.

## Lộ trình (người dùng chốt 2026-11-11)

| GĐ | Nội dung | Trạng thái |
|---|---|---|
| 0 | Nền test & DB thật | Đã nghiệm thu 2026-11-11 |
| 1 | Bảo mật nền: CSRF Flask-WTF, logout POST, cookie SECURE/SAMESITE, seed chỉ chạy ở dev | Đã nghiệm thu 2026-11-11 |
| 2 | Tài khoản: `auth_service`, reset mật khẩu thật, パスワード変更 cho 市民, đăng ký 職員 lưu DB (`users.department_id`), xoá tài khoản thử taro/staff01/admin01 ở local và EC2. Tách 2a (khoá tạm, CLI, gỡ tài khoản thử, ProxyFix, session) và 2b (email reset, パスワード変更, đăng ký 職員) | 2a đã nghiệm thu 2026-11-12; 2b đã nghiệm thu 2026-11-16 |
| 3 | Quản trị: ユーザー一覧, khoá/mở tài khoản, quản lý 部署/カテゴリ, bỏ dữ liệu viết cứng ở ポータル. Tách 3a (quản lý tài khoản 職員/管理者) và 3b (master 部署/カテゴリ) | 3a đã nghiệm thu 2026-11-16 (1025 pass/0/0); 3b chưa làm |
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

## Giai đoạn 2a – Tài khoản: khoá tạm đăng nhập & gỡ tài khoản thử (2026-11-11)

**Đã làm**
- Migration `2422f95d18ff` (sau `069ae4f17650`): `users.failed_login_count` (NOT NULL, mặc định 0), `users.locked_until`, `users.department_id` (FK `departments` RESTRICT, index, NULL với dữ liệu cũ), bảng `password_reset_tokens` (dùng ở 2b). Có `downgrade`. Chưa `db upgrade` trên DB dev.
- `auth_service.authenticate()` (kiểm tra đầu vào theo Excel, tách theo vai trò, khoá tạm 5 lần sai / 15 phút bằng UPDATE nguyên tử, `_now()` để test patch) và `set_password_by_operator()`. `auth/routes.py` chỉ gọi service; `session.clear()` khi đăng nhập (trước `login_user`) và đăng xuất (trước `logout_user`, để cookie "ghi nhớ" vẫn bị xoá).
- Đăng ký 市民 kiểm tra email có `@` phía server (`MSG_EMAIL_NO_AT`).
- `utils/validators.validate_new_password()`; 12 hằng `MSG_*` mới trong `constants.py`; `config.py` và `.env.example`: `LOGIN_MAX_FAILURES`, `LOGIN_LOCK_MINUTES`, `PROXY_FIX_HOPS`.
- ProxyFix trong `create_app()` khi `PROXY_FIX_HOPS > 0` (mặc định tắt; `x_host=0`).
- Lệnh CLI: `create-admin`, `set-password`, `remove-test-users` (`app/cli.py`, `user_service.create_admin/remove_test_users`). README mục 1, 4, 7, 8 đã cập nhật; bảng tài khoản thử đã bỏ khỏi README.
- Sửa sau review (security/architecture): `config._env_int` (LOGIN_*, PROXY_FIX_HOPS sai kiểu/dưới mức tối thiểu ⇒ app không khởi động); đếm lượt sai và đăng nhập thành công đều là UPDATE có điều kiện "chưa bị khoá" (request song song không lọt qua khi vừa bị khoá); `remove-test-users`: khớp cả ID lẫn role, tài khoản đã vô hiệu mà vẫn dùng mật khẩu thử thì không bỏ qua (xoá, hoặc giữ vô hiệu và đổi mật khẩu: 「無効化済み・パスワード変更」); `create-admin`/`set-password` từ chối mật khẩu thử đã công khai; `create_admin` kiểm ID ≤255 ký tự và rollback khi lỗi DB bất ngờ; `TEST_ACCOUNT_PW` chuyển sang `app/constants.py` (seed dùng chung); register dùng `MSG_PASSWORD_MISMATCH`.

**Kết quả test thật** (2026-11-11, toàn suite tuần tự bằng .venv): `396 passed, 3 warnings in 169.73s` — 0 failed, 0 skipped. 243 test cũ + 153 ca mới trong 8 file: `test_auth_lockout.py`, `test_auth.py`, `test_cli_accounts.py`, `test_proxy_fix.py`, `test_schema_2a.py`, `test_config_env.py`, `test_auth_race.py`, `test_cli_hardening.py`. 3 warning là DeprecationWarning `utcnow` của flask_login. arch_check: 0 lỗi, 0 cảnh báo.
- Giới hạn test: race chỉ mô phỏng bằng kết nối DB thứ hai trong cùng tiến trình (không phải 2 worker thật); schema kiểm bằng `db.create_all()` + đọc script migration (backend đã tự chạy upgrade→downgrade→upgrade trên DB test rỗng); chưa thử trên trình duyệt.

**Review**: architecture-reviewer ĐẠT (6 điểm thấp; đã sửa 4: `_env_int`, `MSG_PASSWORD_MISMATCH` ở register, `TEST_ACCOUNT_PW` một nguồn trong constants, `raise _record_failure`). security-reviewer CẦN SỬA nhẹ → đã sửa: kiểm cấu hình LOGIN_*/PROXY_FIX_HOPS, `remove-test-users` xử lý cả tài khoản thử đã vô hiệu còn password123 (`ACTION_DISABLED_PW_RESET`), khoá kiểm nguyên tử trong UPDATE (cả nhánh sai và đúng), CLI từ chối password123 + ID >255, khớp ID + role. Không làm: dummy hash cost, xoá file icon khi xoá user, `LOGIN_ID_LABELS` sang constants.

**Câu thông báo mới không có trong Excel**: `MSG_LOGIN_LOCKED`, `MSG_PASSWORD_PUBLIC`, `MSG_ID_TOO_LONG`, `MSG_ACCOUNT_SAVE_FAILED`, `MSG_USER_NOT_FOUND`, `MSG_NO_ADMIN_LEFT`, `MSG_ID_TAKEN`.

**Chi phí**: ước tính $5.31 (3.19–8.50), ngân sách $8.5; thực tế theo sổ token **$6.11** (subagent $4.51, luồng PM $1.61; sổ có thể chưa đủ phần PM).

**Việc người dùng làm sau nghiệm thu (local trước, rồi EC2)**
1. `flask --app run db upgrade`.
2. `flask --app run create-admin` (tạo 管理者 thật).
3. `flask --app run remove-test-users --dry-run`, xem kết quả, rồi chạy không có `--dry-run`.
4. EC2: kiểm tra Nginx có `proxy_set_header Host $host;` và `X-Forwarded-Proto $scheme;`, gunicorn bind `127.0.0.1` hoặc socket; đúng thì đặt `PROXY_FIX_HOPS=1` và restart. Nếu mọi form báo 「セッションの有効期限…」 thì đặt `WTF_CSRF_SSL_STRICT=false`.

**Chưa làm khi nghiệm thu 2a**: email và reset 2 bước của 市民, パスワード変更, đăng ký 職員 lưu DB, đăng xuất mọi phiên khi đổi mật khẩu (các mục này làm ở 2b, xem bên dưới). Còn lại cho giai đoạn 3 trở đi: màn hình mở khoá/đặt lại mật khẩu cho admin, rate limit theo IP, chuẩn hoá ID 職員 về chữ thường.

## Port thiết kế team – Phần A (2026-11-12, nghiệm thu)

Kế hoạch A20261112-095405. Port từ HTML của nhóm thiết kế: hiển thị lỗi AI ở màn chi tiết và AI分析 của 職員, accordion 緊急度. Test sau Phần A: **422 passed**. Người dùng đã nghiệm thu.

## Giai đoạn 2b – Tài khoản: reset mật khẩu thật, đổi mật khẩu, đăng ký 職員 (2026-11-12 bắt đầu; nghiệm thu 2026-11-16)

Kế hoạch A20261112-111447. Không có migration (dùng bảng `password_reset_tokens` của 2a).

**Đã làm (backend)**
- `app/utils/mail.py` (mới): `send_mail`, `MailError`; `MAIL_SERVER` trống thì ghi nội dung (kèm link) vào log WARNING. `config.py`/`.env.example`: `PUBLIC_BASE_URL`, `MAIL_*`, `PASSWORD_RESET_TOKEN_MINUTES=30`; `TestingConfig` ép `MAIL_SERVER=''`. App cảnh báo khi `SESSION_COOKIE_SECURE=true` mà thiếu `MAIL_SERVER`.
- `auth_service`: `request_password_reset`, `is_reset_token_valid`, `reset_password_with_token`, `issue_reset_code`, `reset_password_with_code`, `change_password`. Token mail 市民 và 再設定コード 職員/管理者 dùng chung bảng, phân biệt theo role; mã sai tính vào khoá tạm.
- `user_service`: `_create_account` chung, `create_staff`, `create_admin` (thêm tham số tuỳ chọn `confirm_password`), `list_departments`, `find_department`. `validators.validate_operator_password(password, confirm=None)`.
- `User.get_id()` = `id:HMAC(SECRET_KEY, password_hash)[:20]`, `load_user` so khớp: đổi mật khẩu bằng cách nào cũng đăng xuất phiên/cookie ghi nhớ khác.
- CLI: `create-staff`, `issue-reset-code`. Route: `auth.reset_password`, `auth.reset_password_confirm` (mới), `auth.reset_password_staff/_admin` (thêm ô mã), `citizen.change_password`, `admin.user_register` (lưu thật, 職員/管理者). README cập nhật (`docs/db/db_design.md` đã có từ 2a, không đổi trong 2b).
- Frontend: 7 template sửa + `auth/reset_password_confirm.html` mới (chưa kiểm tra trên trình duyệt). Tester: bộ test mới chưa viết lúc ghi dòng này.
- Kế hoạch gốc A20261112-111447 hết hạn duyệt, được duyệt lại bằng A20261116-104748 (không đổi phạm vi).

- Kết quả test: **733 pass / 0 fail / 0 skip** (đã nghiệm thu 2026-11-16).

**Hướng dẫn thử (local)**
1. 市民: `/reset-password` nhập email đã đăng ký → luôn hiện cùng một thông báo; `MAIL_SERVER` trống nên link nằm trong log của `python run.py` (tìm 「MAIL_SERVER が未設定」). Mở link → đặt mật khẩu mới → 完了 → đăng nhập bằng mật khẩu mới. Mở lại link → quay về `/reset-password` kèm thông báo lỗi.
2. 市民 đã đăng nhập: プロフィール → 「パスワードを変更する」 (`/citizen/password`). Thử sai mật khẩu hiện tại (tính vào khoá tạm), trùng mật khẩu cũ, thành công. Đăng nhập cùng tài khoản ở trình duyệt khác trước đó → sau khi đổi, tải lại sẽ bị đăng xuất.
3. 管理者: `/admin/users/register` tạo 職員 (chọn 部署) và 管理者; đăng nhập thử bằng tài khoản vừa tạo. Cũng có `flask --app run create-staff`.
4. 職員/管理者 quên mật khẩu: `flask --app run issue-reset-code <ID>` → nhập ID + mã + mật khẩu mới ở `/login/staff/reset-password` (hoặc `/login/admin/reset-password`).

**Việc người dùng tự làm trên EC2 sau nghiệm thu**
- `.env`: `PUBLIC_BASE_URL=https://team3app.duckdns.org`; khi có SMTP thì `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_FROM` (chưa có thì mail không gửi, link nằm trong log: ai đọc được log đều thấy; app cảnh báo lúc khởi động).
- Tạo 職員 thật bằng `flask --app run create-staff` (hoặc màn hình 管理者); cấp mã quên mật khẩu bằng `issue-reset-code <ID>` rồi đưa trực tiếp cho đúng người sau khi xác minh ngoài hệ thống.
- **Sau deploy, mọi người dùng phải đăng nhập lại một lần** (định dạng phiên đổi).
- Nhớ chạy `flask --app run db upgrade` nếu chưa chạy migration 2a `2422f95d18ff`.

**Giới hạn đã chấp nhận**: 職員 chưa tự đổi mật khẩu khi đang đăng nhập; chưa có rate limit theo IP; thời gian phản hồi `/reset-password` lộ nhẹ email có tồn tại; màn quản trị cấp mã/mở khoá làm ở GĐ3a (xem bên dưới).

## Giai đoạn 3a – Quản trị: quản lý tài khoản 職員/管理者 (2026-11-16, đã nghiệm thu: 1025 pass/0/0)

Kế hoạch A20261116-122658. Người dùng chốt: danh sách chỉ 職員 và 管理者; 削除 là **xoá mềm** (`del_flag`, `deleted_at`, `deleted_by_id`), khôi phục bằng `flask --app run restore-user`; không có nút 無効/有効; ID tài khoản đã xoá vẫn giữ chỗ.

**Đã làm (backend, 2026-11-16)**
- Migration `d4a91c7e5b36` (sau `2422f95d18ff`): 3 cột, index `ix_users_deleted_by_id`, FK `users_deleted_by_id_fkey` (RESTRICT), CHECK `ck_users_del_flag_consistent`. **Người dùng tự chạy `flask --app run db upgrade`** (local và EC2). Chưa chạy trên DB dev.
- `User.is_usable` (hybrid: `is_active AND NOT del_flag`) thay `is_active` ở `load_user` và mọi đường auth; tài khoản đã xoá bị coi như ID không tồn tại ở `authenticate`.
- `user_service`: `list_accounts`, `count_accounts`, `account_filters`, `account_row`, `delete_account`, `unlock_account`, `issue_reset_code_for_account`, `restore_account`, `list_deleted_accounts`. `auth_service`: `is_locked`, `invalidate_reset_tokens`.
- Route: `admin.user_list`, `user_delete`, `user_unlock`, `user_reset_code`, `admin.portal` dữ liệu thật. Mã 再設定コード qua `session['reset_code']`, hiện 1 lần trong 120 giây.
- CLI: `restore-user [LOGIN_ID] [--list]`; `set-password`, `issue-reset-code`, `create-*`, `remove-test-users` xử lý tài khoản đã xoá.

**Trạng thái**: đã nghiệm thu 2026-11-16 (1025 pass/0/0; security ĐẠT, architecture đã sửa).

## Việc nhỏ ngoài lộ trình
- 2026-11-17 (kế hoạch A20261117-120553): ダッシュボード 職員: 本日の優先対応案件 hiện tối đa 10 (`PRIORITY_LIMIT` 5 → 10), thêm `stats_service.count_priority_reports()` và biến template `priority_total` để frontend hiện nút 報告一覧 khi có hơn 10; điều kiện 優先対応 tách vào `_priority_conditions()` dùng chung. Không đổi DB.
- 2026-11-17 (kế hoạch A20261117-104147): sửa lỗi production trên HTTPS (`WTF_CSRF_SSL_STRICT=True`): trang có `Referrer-Policy: no-referrer` làm form POST không gửi Referer ⇒ Flask-WTF báo CSRFError 「The referrer header is missing.」 rồi bị đưa về `public.index`. Backend: đổi sang `Referrer-Policy: same-origin` ở `admin.user_list` và `_no_leak` (`auth/routes.py`, dùng cho trang `reset_password_confirm`); vẫn không gửi Referer ra site ngoài. Giữ `Cache-Control: no-store`. Test cũ (`test_account_routes_2b.py`, `test_admin_users_3a.py`) còn assert `no-referrer` ⇒ tester cập nhật.
- 2026-11-17 (kế hoạch A20261117-100122): 市民 パスワード再設定 trả về **một màn** (email + mật khẩu mới + xác nhận), mật khẩu chỉ có hiệu lực khi mở link trong mail rồi bấm 「パスワードの変更を確定する」 (POST). Backend xong (frontend/tester làm song song):
  - Migration `e5b2c8f1a937` (sau `d4a91c7e5b36`): `password_reset_tokens.pending_password_hash VARCHAR(255) NULL`. **Người dùng tự chạy `flask --app run db upgrade`** (local và EC2); chưa chạy trên DB dev. Link gửi trước khi deploy bị vô hiệu.
  - `auth_service`: `request_password_reset(email, new_password, confirm_password)` (đổi chữ ký), `confirm_password_reset(token)` (mới), bỏ `reset_password_with_token`; `is_reset_token_valid` thêm điều kiện có hash chờ. Hash chờ bị xoá khi token dùng/vô hiệu. `MSG_RESET_MAIL_SENT`, `MAIL_RESET_BODY` đổi chữ. Luồng 職員/管理者 và `citizen/change_password` không đổi.
- 2026-11-16 (kế hoạch A20261116-144424): ダッシュボード 職員 thêm khung 地域の問題マップ (Google Maps, dùng lại `map.js` của 地図表示); `staff.dashboard` truyền `map_reports`, `google_maps_api_key`, `google_maps_map_id`. Chờ tester/người dùng nghiệm thu.

## Hướng dẫn thử như khách hàng

Chạy local: `python run.py` (interpreter `task-app\.venv\Scripts\python.exe`), mở http://localhost:5000.

1. `/register`: đăng ký 市民 bằng email tự chọn → trang hoàn tất.
2. `/login/citizen`: đăng nhập → マイページ.
3. 報告する: gửi form trống → thông báo đỏ.
4. Điền đủ, đính ảnh JPEG (có GPS càng tốt), gửi → màn hoàn tất hiện 緊急度.
5. 履歴 (`/citizen/reports`): báo cáo mới ở trạng thái 未対応.
6. Thử file .txt hoặc ảnh >10MB → báo lỗi, không ra 500.
7. Đăng xuất (nút POST), đăng nhập `/login/staff` bằng `staff01` / mật khẩu thử trong `scripts/seed.py` (tài khoản thử chỉ có sau `python -m scripts.seed --with-test-users`) → dashboard có báo cáo mới.
8. 報告一覧 → 詳細: đổi sang 対応中 kèm ghi chú → thông báo thành công. Gửi ghi chú rỗng → cảnh báo.
9. Mở 地図, 統計 (thử tải PDF/CSV), AI分析.
10. Đăng nhập lại 市民: 履歴 hiện 対応中. Gõ `/staff/dashboard` → 403.

**Lưu ý môi trường**
- `.env` có `GEMINI_API_KEY` nên mỗi báo cáo gọi Gemini thật (khoảng $0.001). Đặt `AI_PROVIDER=rule_based` nếu không muốn gọi.
- Key Google Maps phải cho phép `localhost` (chưa kiểm chứng).
- Camera/GPS chỉ chạy trên localhost hoặc HTTPS.
- Phạm vi file của các agent nằm ở `.claude/hooks/lib/workflow.py` (SCOPES); chỉ người dùng sửa được.

## Phần còn "giả" hoặc chưa có

- ポータル: danh sách tài khoản đã lấy từ DB (3a, backend và template). Reset mật khẩu, パスワード変更 và 新規アカウント登録 đã thật từ 2b.
- 市民 chưa xem được chi tiết 対応状況, chưa 取り消し được.
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
