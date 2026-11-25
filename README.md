# 地域の困りごと報告システム (task-app)

Hệ thống web cho phép **người dân** báo cáo sự cố trong khu vực như ổ gà, đèn đường hỏng, đồ chơi công viên hỏng hay rác đổ trộm, kèm ảnh và vị trí. **Nhân viên hành chính** quản lý các báo cáo theo trạng thái 未対応 → 対応中 → 対応済み. **AI** tự động phân loại, đánh giá độ khẩn cấp và gợi ý phòng ban phụ trách. **Admin** quản lý người dùng và dữ liệu master.

File này là tài liệu gốc về kiến trúc. Khi thêm tính năng mới, hãy làm theo các quy tắc ở đây. Nếu cần thay đổi quy tắc, hãy sửa README trước.

---

## 1. Chạy dự án

```bash
# 1. Cài thư viện (trong .venv của dự án)
pip install -r requirements.txt

# 2. Tạo file .env từ mẫu rồi điền SECRET_KEY và DATABASE_URL
cp .env.example .env

# 3. Tạo hoặc cập nhật bảng bằng migration (chạy ở thư mục task-app)
flask --app run db upgrade

# 4. Thêm dữ liệu master (部署, カテゴリ). Chạy lại nhiều lần cũng không bị trùng
python -m scripts.seed
#    Chỉ khi phát triển ở máy local: thêm cờ --with-test-users để tạo cả 3 tài khoản test
python -m scripts.seed --with-test-users

# 5. Chạy server: http://localhost:5000
python run.py
```

**Đổi schema.** Sửa model trong `app/models/`, rồi chạy các lệnh dưới đây. Luôn đọc file migration mới sinh trong `migrations/versions/` trước khi `upgrade`, và commit file đó cùng với model.

```bash
flask --app run db migrate -m "mô tả thay đổi"
flask --app run db upgrade
```

**DB đã tạo bằng `db.create_all()` trước khi có Flask-Migrate.** Chạy `flask --app run db stamp 8d99a80ec994` một lần, sau đó mới `db upgrade`. Revision `8d99a80ec994` chính là bảng `users` cũ.

**Chạy định kỳ (AI緊急度の再判定).** Lệnh `flask --app run rejudge-urgency` phán định lại các báo cáo 未対応 đã quá `AI_REJUDGE_HOURS` giờ. Trên EC2, chạy mỗi giờ bằng systemd timer (cùng cách `certbot.timer`). Sửa `User`, `WorkingDirectory` và đường dẫn `.venv` cho đúng server:

```ini
# /etc/systemd/system/task-app-rejudge.service
[Unit]
Description=task-app AI緊急度の再判定
[Service]
Type=oneshot
User=ubuntu
WorkingDirectory=/home/ubuntu/task-app
ExecStart=/home/ubuntu/task-app/.venv/bin/flask --app run rejudge-urgency

# /etc/systemd/system/task-app-rejudge.timer
[Unit]
Description=task-app AI緊急度の再判定（1時間ごと）
[Timer]
OnCalendar=hourly
Persistent=true
[Install]
WantedBy=timers.target
```

Bật bằng `sudo systemctl daemon-reload && sudo systemctl enable --now task-app-rejudge.timer`, xem log bằng `journalctl -u task-app-rejudge`.

**Quét báo cáo trùng (chạy tay).** Báo cáo mới tự được kiểm tra trùng khi gửi (xem mục 3, 「重複の自動検出」). Báo cáo có trước khi có tính năng này, hoặc lúc đó AI chưa phân loại được, thì chạy `flask --app run detect-duplicates` một lần (local và EC2 sau khi deploy). Lệnh quét mọi báo cáo 未完了 có カテゴリ mà chưa thuộc nhóm nào, in số liệu bằng tiếng Nhật; chạy lại không tạo nhóm trùng lặp; một báo cáo lỗi không làm dừng cả lượt. Chưa có lịch chạy định kỳ.

**Seed và tài khoản test.** Mặc định `python -m scripts.seed` chỉ tạo dữ liệu master (部署, カテゴリ). Tài khoản test (市民 `taro@example.com`, 職員 `staff01`, 管理者 `admin01`; mật khẩu nằm trong `scripts/seed.py`, **không ghi ở README**) chỉ được tạo khi thêm cờ `--with-test-users`, chỉ dùng khi phát triển. **Không chạy seed với `--with-test-users` trên server.** Trong code, `scripts.seed.seed(with_test_users=False)` là hàm dùng được từ test (gọi trên app test), `main(argv)` là lối vào dòng lệnh.

**Tài khoản (lệnh `flask --app run ...`, chạy ở thư mục task-app; cả local lẫn EC2).** 管理者 cũng tạo được 職員/管理者 ở màn hình `/admin/users/register` (2b), còn xoá, mở khoá và cấp 再設定コード cho 職員/管理者 làm ở màn hình `/admin/users` (3a); các lệnh dưới đây vẫn dùng được (nhất là khi không còn 管理者 nào đăng nhập được) và `restore-user` chỉ có ở CLI. Mật khẩu luôn nhập ẩn, có bước xác nhận, ít nhất 8 ký tự:
- `create-admin [--login-id ID] [--name TÊN]`: tạo 管理者. Thiếu tham số thì hỏi. ID không được chứa `@`, tối đa 255 ký tự, không được trùng; tên tối đa 100 ký tự. Lỗi DB bất ngờ thì rollback và báo một câu (không in stack trace).
- `create-staff [--login-id ID] [--name TÊN] [--department TÊN-HOẶC-ID]`: tạo 職員 (2b), dùng chung `user_service.create_staff` với màn hình. Thiếu tham số thì hỏi; thiếu `--department` thì liệt kê 部署 (ID: tên) rồi hỏi. 部署 bắt buộc, nhận tên đầy đủ hoặc ID.
- `issue-reset-code <login_id>`: cấp **再設定コード** cho 職員/管理者 đang hoạt động (2b). In mã dạng `XXXXX-XXXXX` (10 ký tự, không có 0/O/1/I/L) và hạn dùng; mã dùng **một lần**, hiệu lực `PASSWORD_RESET_TOKEN_MINUTES` phút (30), cấp mã mới thì mã cũ hỏng. Trong DB chỉ lưu hash; mã chỉ hiện trên màn hình, không ghi log. Quản trị **phải xác minh người xin là đúng chủ tài khoản ngoài hệ thống** rồi đưa mã trực tiếp; người đó nhập ID + mã + mật khẩu mới ở `/login/staff/reset-password` (hoặc `/login/admin/reset-password`). Nhập sai mã tính vào khoá tạm như đăng nhập.
- `set-password <login_id>`: đổi mật khẩu của bất kỳ tài khoản nào và gỡ khoá đăng nhập tạm. Không bật lại tài khoản đã bị vô hiệu (chỉ nhắc). Tài khoản **đã xoá** (3a) thì từ chối, báo 「先に restore-user で復元してください」; `issue-reset-code` cũng từ chối tài khoản đã xoá, `create-admin`/`create-staff` với ID đã xoá thì báo trùng ID (ID của tài khoản đã xoá vẫn giữ chỗ).
- `restore-user [LOGIN_ID] [--list]` (3a): `restore-user <login_id>` khôi phục tài khoản mà 管理者 đã xoá ở `/admin/users`: đặt lại `del_flag`, `deleted_at`, `deleted_by_id`, đồng thời xoá khoá đăng nhập tạm và vô hiệu mã/token chưa dùng (mật khẩu cũ vẫn dùng được; quên thì `issue-reset-code`/`set-password`). `restore-user --list` in danh sách tài khoản đã xoá (ID, 氏名, 役割, 削除日時 giờ Nhật, 削除者); rỗng thì in 「削除済みのアカウントはありません。」. Thiếu cả hai tham số thì báo lỗi cách dùng.
- Cả `create-admin`, `create-staff` và `set-password` **từ chối mật khẩu trùng mật khẩu thử đã công khai** (`TEST_ACCOUNT_PW` trong `app/constants.py`, dùng chung với `scripts/seed.py`) bằng 「このパスワードは公開されているため使用できません。」.
- `remove-test-users [--dry-run] [--yes] [--force]`: gỡ `taro@example.com`, `staff01`, `admin01` còn dùng mật khẩu thử. Chỉ khớp khi **cả ID lẫn role đúng** (`taro@example.com`=市民, `staff01`=職員, `admin01`=管理者; khác role coi như không có, kể cả khi có `--force`). Tài khoản chưa có dữ liệu gắn (báo cáo, lịch sử, thông báo...) thì **xoá**; có dữ liệu thì **vô hiệu hoá** (`is_active=False`, mật khẩu đổi thành chuỗi ngẫu nhiên). Tài khoản đã đổi mật khẩu thì bỏ qua (trừ khi có `--force`). Tài khoản đã vô hiệu mà **mật khẩu vẫn là mật khẩu thử** (hoặc có `--force`) thì không bỏ qua: không có dữ liệu → xoá; có dữ liệu → giữ vô hiệu và đổi mật khẩu ngẫu nhiên (「無効化済み・パスワード変更」). Tài khoản không có thì bỏ qua nên chạy lại không lỗi. Tài khoản thử **đã bị xoá mềm** (3a) không được coi là còn dùng được (xử lý như tài khoản đã vô hiệu). Nếu sau khi gỡ không còn 管理者 nào dùng được (không bị vô hiệu, không bị xoá) thì **cả lệnh bị từ chối** (exit 1). `--dry-run` chỉ in kết quả dự kiến, `--yes` bỏ bước xác nhận.

Thứ tự khi dọn tài khoản thử (làm ở local trước, rồi EC2):
1. `flask --app run db upgrade`
2. `flask --app run create-admin` (tạo 管理者 thật)
3. `flask --app run remove-test-users --dry-run`, xem kết quả
4. `flask --app run remove-test-users` (hoặc thêm `--yes`)

Cấu hình bảo mật trong `.env` (xem `.env.example`): `SESSION_COOKIE_SECURE` (`true` khi chạy HTTPS như EC2, mặc định `false` cho `http://localhost`), `WTF_CSRF_SSL_STRICT` (mặc định `true`), `LOGIN_MAX_FAILURES` (5, tối thiểu 1) và `LOGIN_LOCK_MINUTES` (15, tối thiểu 1) cho khoá đăng nhập tạm, `PROXY_FIX_HOPS` (0, tối thiểu 0, xem mục 8). Đặt lại mật khẩu (2b): `PASSWORD_RESET_TOKEN_MINUTES` (30, tối thiểu 1), `PUBLIC_BASE_URL` (gốc của link trong mail, không lấy từ Host; EC2: `https://team3app.duckdns.org`, mặc định `http://localhost:5000`), `MAIL_SERVER`/`MAIL_PORT` (587)/`MAIL_USERNAME`/`MAIL_PASSWORD`/`MAIL_FROM`/`MAIL_TIMEOUT` (SMTP + STARTTLS; `MAIL_SERVER` trống thì **không gửi mail, nội dung kèm link được ghi vào log ở mức WARNING**; app cũng cảnh báo lúc khởi động nếu `SESSION_COOKIE_SECURE=true` mà `MAIL_SERVER` trống). Test luôn ép `MAIL_SERVER=''`. Giá trị gõ sai (ví dụ `ture`; hoặc số nguyên sai như `abc`, `0` cho `LOGIN_MAX_FAILURES`) làm app **không khởi động** với thông báo lỗi nêu tên biến, để không vô tình tắt Secure hay tắt khoá đăng nhập. Để trống = dùng mặc định.

**Chạy test.** Test dùng một DB PostgreSQL riêng, mỗi test tạo rồi xoá toàn bộ bảng. Không bao giờ trỏ vào DB dev (conftest sẽ dừng nếu `TEST_DATABASE_URL` trùng `DATABASE_URL`). Chưa đặt `TEST_DATABASE_URL` thì các test dùng DB bị bỏ qua.

`saitama_app` **không có quyền CREATEDB**, nên phải tạo DB test bằng tài khoản `postgres`, một lần. Lệnh trên Windows (PowerShell):

```powershell
& "C:\Program Files\PostgreSQL\17\bin\psql.exe" -U postgres -h localhost -c "CREATE DATABASE saitama_help_test OWNER saitama_app"
```

`TEST_DATABASE_URL` giống `DATABASE_URL`, chỉ đổi `/saitama_help` thành `/saitama_help_test`. Thêm vào `.env`, rồi chạy test:

```bash
# TEST_DATABASE_URL=postgresql+psycopg://saitama_app:<mật khẩu>@localhost:5432/saitama_help_test
python -m pytest
```

conftest từ chối chạy nếu tên DB test không kết thúc bằng `_test`. Thư mục `docs/` nằm trong `.gitignore`, nên không có trong git (chỉ có trên máy đã tạo ra nó).

Công nghệ: Flask 3, Flask-SQLAlchemy, Flask-Migrate (Alembic), Flask-Login, PostgreSQL (psycopg 3), reportlab (PDF), Bootstrap 5, Chart.js.

---

## 2. Kiến trúc: ba tầng

```
Route (app/blueprints)  ->  Service (app/services, app/ai)  ->  Model (app/models)
```

| Tầng | Được làm | Không được làm |
|---|---|---|
| **Route** | Đọc request/form, gọi service, `render_template`, `redirect`, `flash`, trả JSON | Viết nghiệp vụ, truy vấn DB phức tạp, gọi API AI trực tiếp |
| **Service** | Toàn bộ nghiệp vụ: kiểm tra quy tắc, ghi DB, gọi AI, lưu ảnh | Import `request`, `render_template`, `flash` |
| **Model** | Khai báo bảng, quan hệ, phương thức nhỏ như `set_password` | Chứa nghiệp vụ |

**Quy tắc import một chiều:** route → service → model. Model không import service. Service không import route.

**Lợi ích:**
- Thêm tính năng thường chỉ cần thêm một hàm service và một route.
- Service test được bằng pytest mà không cần trình duyệt.
- Đổi nhà cung cấp AI chỉ cần sửa `app/ai/`. Đổi nơi lưu ảnh chỉ cần sửa `storage_service.py`. Route không phải sửa gì.

Hiện tại các route cũ vẫn tự xử lý form, ví dụ trang đăng ký. Khi tạo service tương ứng, hãy chuyển logic đó sang service.

---

## 3. Cấu trúc thư mục

Ký hiệu: ✅ đã có code, ⬜ file giữ chỗ chỉ có một dòng chú thích mô tả nhiệm vụ.

```
task-app/
├── run.py                       ✅ điểm khởi động: create_app() rồi chạy server
├── config.py                    ✅ đọc .env, cấu hình Flask
├── requirements.txt             ✅
├── .env.example                 ✅ mẫu biến môi trường (không chứa giá trị thật)
├── .gitignore                   ✅ bỏ qua .env, .venv, __pycache__, ảnh upload
├── instance/uploads/            ⬜ ảnh người dân gửi lên (không commit)
├── migrations/                  ✅ Flask-Migrate (Alembic). versions/ chứa lịch sử thay đổi schema
├── docs/db/                     ✅ thiết kế DB (db_design.md) và danh sách dữ liệu màn hình
├── docs/sequence/               ✅ sơ đồ sequence (PlantUML): auth/ (đăng nhập, đăng ký), citizen/, staff/, admin/, common/ (dùng chung)
├── docs/class/                  sơ đồ class: auth/ ✅, citizen/ staff/ admin/ ⬜. Mỗi thư mục con của sequence là một sơ đồ <グループ名>_クラス図 (.drawio, .json, .png)
├── scripts/
│   └── seed.py                  ✅ dữ liệu master (部署, カテゴリ); tài khoản test chỉ khi có `--with-test-users`
├── tests/                       pytest (cần TEST_DATABASE_URL, xem mục 1)
│   ├── conftest.py              ✅ app, DB test, user/カテゴリ/報告 mẫu
│   ├── test_auth.py             ⬜
│   ├── test_security.py         ✅ CSRF (token, redirect về trang trước, logout POST), cờ cookie, `_env_bool`, seed
│   ├── test_report_service.py   ✅ 職員 (一覧, 詳細, AI分析), 市民 (報告する, 写真撮影, 位置情報, マイページ)
│   ├── test_user_service.py     ✅ プロフィール設定, アイコン
│   ├── test_status_service.py   ✅ cập nhật trạng thái, 緊急度の判定, 一括変更
│   ├── test_notification_service.py ✅ thông báo cho 職員 (未読, 既読)
│   ├── test_duplicate_service.py ✅ 重複確認, 統合
│   ├── test_stats_service.py    ✅ dashboard, 地図, 統計
│   └── test_ai_classifier.py    ✅ rule_based, Gemini (client giả, không gọi API thật)
└── app/
    ├── __init__.py              ✅ create_app(): config, extensions, đăng ký blueprint và lệnh CLI
    ├── cli.py                   ✅ lệnh flask: `rejudge-urgency` (AI再判定, chạy định kỳ), `detect-duplicates` (quét báo cáo trùng, chạy tay), `create-admin`, `create-staff`, `issue-reset-code`, `set-password`, `restore-user`, `remove-test-users` (xem mục 1)
    ├── extensions.py            ✅ db, login_manager, migrate
    ├── constants.py             ✅ Role, ReportStatus, Urgency, AiAnalysisStatus, DuplicateGroupStatus, CitizenNotificationType, luồng trạng thái, nhãn tiếng Nhật
    ├── models/
    │   ├── __init__.py          ✅ import mọi model
    │   ├── user.py              ✅ User + user_loader (`get_id()` = `id:HMAC(SECRET_KEY, password_hash)[:20]`, 2b)
    │   ├── department.py        ✅ 担当部署
    │   ├── category.py          ✅ カテゴリ
    │   ├── report.py            ✅ Report, ReportPhoto
    │   ├── status_history.py    ✅ 対応履歴
    │   ├── ai_analysis.py       ✅ kết quả AI
    │   ├── duplicate_group.py   ✅ nhóm báo cáo nghi trùng
    │   └── notification.py      ✅ StaffNotification (thông báo cho 職員, AI再判定, 既読 chung) và CitizenNotification (お知らせ của 市民, 既読 theo từng người)
    ├── services/                nghiệp vụ (xem mục 4). ✅ phần 職員 và 市民 của report/status/duplicate/stats/storage/user, đăng nhập, khoá tạm, đặt lại và đổi mật khẩu trong auth_service, tạo 職員/管理者 và lệnh tài khoản trong user_service. ⬜ assignment_service, quản lý tài khoản (khoá/mở, danh sách) trong user_service
    ├── ai/                      ✅ AI: Gemini và phân loại theo từ khóa (xem mục 4)
    ├── forms/                   ⬜ Flask-WTF forms
    ├── fonts/                   ✅ font IPAexゴシック cho PDF (kèm giấy phép IPA). Không đặt trong static để không public
    ├── utils/
    │   ├── decorators.py        ✅ role_required
    │   ├── mail.py              ✅ send_mail() (SMTP + STARTTLS, hoặc ghi log khi MAIL_SERVER trống), MailError
    │   ├── validators.py        ✅ quy tắc mật khẩu
    │   └── geo.py               ✅ địa chỉ ⇔ tọa độ (国土地理院), khoảng cách Haversine `distance_m`, `bounding_box`
    ├── blueprints/
    │   ├── public/              ✅ /, /guide, /faq
    │   ├── auth/                ✅ đăng nhập, đăng xuất, đăng ký, đặt lại mật khẩu
    │   ├── citizen/             ✅ /citizen/...
    │   ├── staff/               ✅ /staff/...
    │   ├── admin/               ✅ /admin/...
    │   └── api/                 ⬜ JSON cho bản đồ, biểu đồ (chưa đăng ký trong create_app)
    ├── templates/
    │   ├── layouts/             base.html ✅, public/citizen/staff/admin.html ⬜
    │   ├── components/          _flash.html ✅, _status_badge.html ✅, _urgency_badge/pagination/card ⬜
    │   ├── public/ auth/ citizen/ staff/ admin/   template theo từng blueprint
    │   └── errors/              ⬜ 403, 404, 500
    └── static/
        ├── css/                 tokens.css ⬜, base.css ✅, components.css ⬜, responsive.css ✅ (スマホ表示)
        ├── js/                  navigation.js ✅ (lịch sử trình duyệt), report_form.js ✅, map.js ✅ (Google Maps), charts.js ⬜
        └── images/              ✅
```

---

## 4. Nhiệm vụ của từng thư mục và file

### Gốc dự án
- **run.py**: chỉ gọi `create_app()` và chạy server. Không viết route ở đây.
- **config.py**: lớp `Config` đọc `.env`, `TestingConfig` cho pytest (đọc `TEST_DATABASE_URL`). Đã có `AI_LOW_CONFIDENCE_THRESHOLD` (mặc định 0.7, ngưỡng 要人間チェック ở AI分析), `AI_REJUDGE_HOURS` (mặc định 12, số giờ trước khi phán định lại báo cáo 未対応), `AI_RERUN_BATCH_LIMIT` (mặc định 5) và `AI_RERUN_TIME_BUDGET_SECONDS` (mặc định 20) cho nút AIモデル再実行 chung, `DUPLICATE_RADIUS_M` (mặc định 100, bán kính m) và `DUPLICATE_WINDOW_DAYS` (mặc định 30, chênh lệch ngày tạo) cho 重複の自動検出, `GOOGLE_MAPS_API_KEY` và `GOOGLE_MAPS_MAP_ID` (bản đồ 地図表示), `GEOCODER_URL` và `GEOCODER_TIMEOUT` (tìm tọa độ từ 発生場所 khi báo cáo không có GPS), `REVERSE_GEOCODER_URL` (đổi tọa độ GPS thành địa chỉ). Đã có `AI_PROVIDER` (`gemini` hoặc `rule_based`), nhóm `GEMINI_*` (`GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_TIMEOUT_MS`, `GEMINI_MAX_RETRIES`, `GEMINI_TEMPERATURE`, `GEMINI_MAX_OUTPUT_TOKENS`, `GEMINI_THINKING_LEVEL`), `UPLOAD_FOLDER` (mặc định `instance/uploads`) và `MAX_CONTENT_LENGTH` (32MB một request). Bảo mật: `WTF_CSRF_TIME_LIMIT = None` (token sống cùng session), `WTF_CSRF_SSL_STRICT` (đọc từ `.env`, mặc định true), `SESSION_COOKIE_SECURE` (đọc từ `.env`, mặc định false, `REMEMBER_COOKIE_SECURE` theo cùng giá trị), cờ `HttpOnly` và `SameSite=Lax` cho cookie session và remember. Hàm `_env_bool(name, default)`: `1/true/yes/on` là True, `0/false/no/off` là False, không đặt hoặc rỗng là default, giá trị khác thì `raise ValueError` (fail fast lúc khởi động). `TestingConfig` đặt `WTF_CSRF_ENABLED = False`. Dự định tách thêm `DevelopmentConfig`, `ProductionConfig`, và thêm `DUPLICATE_RADIUS_M`.
- **instance/uploads/**: nơi lưu ảnh. Ảnh **không** đặt trong `static/` vì có thể chứa thông tin cá nhân. Chỉ người báo cáo và nhân viên được xem, thông qua một route có kiểm tra quyền.
- **scripts/**: script chạy tay như seed hay import dữ liệu. Chạy bằng `python -m scripts.<tên>` từ thư mục task-app.
- **tests/**: `conftest.py` tạo app với `TestingConfig` và DB riêng. Mỗi service có một file test. Lưu ý môi trường test: `TestingConfig` tắt CSRF; fixture `app` giữ một app context suốt test nên `g.csrf_token` dùng chung giữa các client (muốn token của "session khác" phải `g.pop('csrf_token')`). Test CSRF dùng fixture `csrf_app`, `csrf_client`, `csrf_login` và helper `get_csrf_token`. `make_report` không tạo `report_status_histories` (lịch sử bắt đầu từ 0). Gọi seed trong test bằng `scripts.seed.seed()` trên app test, và `main()` với `create_app` được monkeypatch; không chạy `python -m scripts.seed` trong test.

### app/ lõi
- **app/\_\_init\_\_.py**: `create_app()`. Khi thêm blueprint mới, hãy import và `register_blueprint` ở đây. Đã có `csrf.init_app(app)` và error handler cho `CSRFError`: ghi log, `flash(MSG_CSRF_EXPIRED, 'warning')` rồi redirect về trang trước (`_previous_page_path()`; request có `X-Replace-History` được `after_request` đổi thành JSON `{redirect: ...}`). `_previous_page_path()` chỉ lấy path và query của `Referer` (bỏ host để tránh open redirect), mã hoá lại bằng `quote` (ký tự điều khiển, dấu cách thành `%xx`, `%xx` sẵn có giữ nguyên), Referer không có, bắt đầu bằng `//` hoặc chứa `\` thì về `public.index`. Error handler 403/404/500 và filter Jinja sẽ đăng ký ở đây (chưa có, các file `templates/errors/` mới là file giữ chỗ).
- **extensions.py**: khởi tạo mọi extension ở một chỗ để tránh import vòng. Đã có `db`, `login_manager`, `migrate = Migrate()` và `csrf = CSRFProtect()`.
- **constants.py**: định nghĩa trạng thái, độ khẩn cấp và vai trò bằng Enum. Mã lưu trong DB là tiếng Anh, nhãn hiển thị là tiếng Nhật. Template và service đều lấy nhãn từ đây, không viết tay.
  - Lấy nhãn: `ReportStatus.label_of(code)`. Lấy trạng thái kế tiếp hợp lệ: `next_statuses(current, role)`.
  - `CHECK` của model lấy danh sách giá trị qua `XXX.check_sql('cột')`. Khi thêm giá trị vào Enum, phải tạo migration để cập nhật `CHECK`.

### app/models/
Mỗi file là một nhóm bảng. Khi thêm model mới, phải import nó trong `models/__init__.py`, rồi tạo migration. Thiết kế chi tiết gồm cột, kiểu, ràng buộc, index và lý do của từng cột nằm trong [docs/db/db_design.md](docs/db/db_design.md).

| Bảng | Nội dung chính |
|---|---|
| `users` | Đã có. Thêm `icon_path` cho アイコン設定, `postal_code` (dạng `123-4567`) và `address` cho アカウント作成 (chỉ người dân, NULL với nhân viên và admin). `failed_login_count` (số lần sai liên tiếp, NOT NULL mặc định 0) và `locked_until` (khoá đăng nhập tạm đến thời điểm này) cho `auth_service`. `department_id` (FK `departments`, RESTRICT, NULL với dữ liệu cũ và 管理者) là 担当部署 của 職員, ghi bởi `create_staff` (màn hình 新規アカウント登録 và lệnh `create-staff`). 3a (xoá mềm): `del_flag` (boolean NOT NULL, mặc định false), `deleted_at` (timestamptz NULL), `deleted_by_id` (FK `users.id` `users_deleted_by_id_fkey`, RESTRICT, có index) luôn được ghi cùng một lần; CHECK `ck_users_del_flag_consistent` bắt buộc đã xoá thì có `deleted_at`, chưa xoá thì `deleted_at` và `deleted_by_id` đều NULL. `login_id` vẫn UNIQUE nên ID tài khoản đã xoá vẫn giữ chỗ. `is_active` là cờ 利用停止 riêng (chỉ `remove-test-users` đặt). **"Dùng được" là `User.is_usable` = `is_active AND NOT del_flag`** (dùng được cả ở Python lẫn trong câu truy vấn), không dùng `is_active` đơn lẻ |
| `password_reset_tokens` | Token mail của 市民 và 再設定コード của 職員/管理者 (2b): `user_id` (FK CASCADE), `token_hash` (UNIQUE, chỉ lưu SHA-256), `expires_at`, `used_at` (NULL = chưa dùng), `pending_password_hash` (hash mật khẩu mới đang chờ xác nhận, chỉ token mail của 市民 có; xoá khi token bị dùng/vô hiệu; NULL thì token mail không dùng được), `created_at`. Một bảng dùng chung, phân biệt theo `role` của user (token mail chỉ tiêu thụ được với 市民, mã chỉ với 職員/管理者). Mỗi user chỉ có 1 token/mã còn hiệu lực (cái cũ bị đặt `used_at`) |
| `departments` | 担当部署. Tạm thời có 道路管理課, 道路照明課, 公園緑地課, 環境課, 市民生活課 |
| `categories` | `code` (khoá cố định cho AI), `name` (道路, 街灯, 公園, 不法投棄, その他), `default_department_id` |
| `reports` | `citizen_id`, `title` (件名), `description`, `latitude`, `longitude`, `address` (ô 位置情報 trên màn hình 報告する của người dân, phía 職員 gọi là 発生場所), `location_source` (nguồn tọa độ: `photo`/`device` là GPS, `address` là tìm từ 発生場所), `gps_address` (địa chỉ suy ra từ GPS để 職員 đối chiếu), `status`, `category_id`, `urgency`, `department_id`, `duplicate_group_id`, `duplicate_of_id`, thời gian. `category_id`, `urgency`, `department_id` là giá trị đã chốt, NULL nghĩa là chưa phân loại |
| `report_photos` | `report_id`, `file_path` (tên ngẫu nhiên), `content_type` |
| `report_status_histories` | `from_status` (NULL là mới tiếp nhận), `to_status`, `changed_by_id` (NULL là hệ thống), `from_urgency`/`to_urgency` (職員 đổi 緊急度; `to_urgency` NULL là không đổi, `from_urgency` NULL là từ 未判定), `urgency_reason` (判断変更理由), `comment` (ghi chú nội bộ), `public_message` (lời nhắn gửi người dân), thời điểm. Đây là 対応履歴, chỉ thêm dòng |
| `ai_analyses` | Mỗi lần AI phân tích: `status` (succeeded/failed), `trigger_type` (`initial` khi tạo báo cáo, `rejudge` khi phán định lại tự động, `manual` khi 職員 bấm AIモデル再実行 ở AI分析; nhãn 「手動再実行」), category, urgency, department, `confidence` (0 đến 1), `raw_response` (JSONB), `model_name`, `error_message` |
| `staff_notifications` | Thông báo cho 職員: `report_id`, `message`, `created_at`, `read_at`/`read_by_id` (NULL là chưa đọc). 既読 dùng chung cho mọi 職員 |
| `citizen_notifications` | お知らせ của 市民 (2026-11-18, migration `f6c3d9a2b148`): `user_id` (FK `users.id` CASCADE, chủ báo cáo), `report_id` (FK RESTRICT, index), `type` (`report_received`/`ai_judged`/`staff_update`, CHECK từ `CitizenNotificationType`), `message` (một dòng), `detail` (NULL thì hiện `message`), `created_at`, `read_at` (NULL là chưa đọc). 既読 **theo từng người** (khác `staff_notifications`). Index `(user_id, created_at)` |
| `duplicate_groups` | Nhóm báo cáo nghi trùng: `status` (open/merged/dismissed), `similarity`, người và thời điểm xử lý |

Không lưu `priority`. 対応優先度 tính từ `urgency` và trạng thái chưa xong. Mọi báo cáo đều do người dân gửi (`citizen_id NOT NULL`).

Kết quả AI được lưu ở bảng riêng, không ghi đè lên `reports`. Nhờ vậy nhân viên có thể sửa phân loại mà vẫn giữ kết quả AI gốc. Dữ liệu này cũng dùng để đo độ chính xác của AI so với quyết định cuối cùng của nhân viên. Lần phân tích mới nhất là dòng có `created_at` lớn nhất, nếu bằng nhau thì lấy `id` lớn nhất (`Report.latest_ai_analysis`).

**Nhóm trùng.** Một báo cáo thuộc tối đa một nhóm qua `duplicate_group_id`. Khi 統合, các báo cáo còn lại nhận `duplicate_of_id` trỏ về 代表案件, và nhóm chuyển sang `merged`. Khi 重複なし, nhóm chuyển sang `dismissed` (báo cáo vẫn giữ `duplicate_group_id` nên không bị gom lại).

### Luồng trạng thái

```
received (未対応)      -> in_progress (対応中) / on_hold (保留・他部署へ引継ぎ) / watching (保留・経過観察)
                       / not_applicable (対象外・対応不要) / resolved (対応完了)
received (未対応)      -> cancelled (取り消し)   # chỉ người dân, chỉ khi chưa xử lý
in_progress (対応中)   -> contractor (業者対応中) / confirming (完了確認中) / resolved / on_hold / watching
contractor (業者対応中) -> confirming / in_progress
confirming (完了確認中) -> resolved / in_progress
on_hold                -> in_progress
watching               -> in_progress / resolved / not_applicable
resolved, not_applicable, cancelled: trạng thái cuối, không đổi tiếp
```

Mọi bước chuyển, trừ `cancelled`, đều do nhân viên thực hiện. Các trạng thái chưa xong (`OPEN_STATUSES`) gồm tất cả, trừ `resolved`, `not_applicable` và `cancelled`. Nhóm 「対応中」 trên thẻ thống kê gồm `in_progress`, `contractor`, `confirming` (`WORKING_STATUSES`), nhóm 「保留中」 gồm `on_hold`, `watching` (`HOLD_STATUSES`). Người dân thấy đúng nhãn của từng trạng thái. Màu badge của trạng thái chỉ định nghĩa ở `components/_status_badge.html`.

Chỉ `status_service.py` được đổi trạng thái. Mỗi lần đổi phải ghi một dòng vào `report_status_histories`.

**Quy tắc đã cài cho 職員** (theo `docs/ユースケース図_行政職員 4.xlsx`):
- 詳細・対応更新: bắt buộc nhập 担当者メモ・進捗コメント (2a-1). Ô chọn chỉ có trạng thái hiện tại và trạng thái kế tiếp hợp lệ. Chọn lại trạng thái hiện tại nghĩa là chỉ thêm ghi chú, vẫn ghi một dòng lịch sử với `from_status = to_status`.
- 緊急度の判定 (không có trong Excel, thêm theo thiết kế màn hình): nằm trong cùng form với 対応状況の更新 và dùng chung một nút lưu. 職員の最終判断 được lưu vào `reports.urgency`; kết quả AI vẫn giữ nguyên ở `ai_analyses`. Nếu chọn 緊急度 khác với lần AI phán định thành công gần nhất thì bắt buộc nhập 判断変更理由 (`MSG_URGENCY_REASON_REQUIRED`). Chọn lại đúng kết quả AI, hoặc báo cáo chưa có kết quả AI, thì không cần lý do. Thay đổi được ghi vào cùng dòng 対応履歴 với lần cập nhật (`status_service.update_report_status`).
- 一括対応ステータス変更: bỏ qua báo cáo đã 取り消し và báo cáo đã ở trạng thái đích. Nếu còn báo cáo nào không chuyển được (ví dụ 対応済み → 対応中) thì không đổi báo cáo nào và báo lỗi kèm ID.
- 代表案件に統合: chỉ nhóm `open`. Đặt `duplicate_of_id` cho các báo cáo còn lại, nhóm chuyển `merged`, không đổi trạng thái nên không ghi lịch sử. Xong thì quay về 重複確認.
- 重複なし (thêm theo thiết kế màn hình, không có trong Excel; `MSG_GROUP_DISMISSED`): `POST /staff/duplicates/<group_id>/dismiss` (`duplicate_service.dismiss_group`), chỉ nhóm `open` (không thì `MSG_GROUP_NOT_OPEN`, flash đỏ). Nhóm chuyển `dismissed` kèm `resolved_by_id`/`resolved_at`, không đổi báo cáo, biến khỏi danh sách.
- 重複の自動検出 (thêm theo thiết kế màn hình, không có trong Excel; kế hoạch A20261124-143227): theo quy tắc, **không** gọi Gemini. Hai báo cáo vào cùng một nhóm `open` khi **cùng カテゴリ, cách nhau ≤ `DUPLICATE_RADIUS_M` (100 m, Haversine), cả hai thuộc `OPEN_STATUSES`, không có `duplicate_of_id`, và thời điểm tạo cách nhau ≤ `DUPLICATE_WINDOW_DAYS` (30 ngày). `duplicate_service.detect_for_report(report_id)` chạy sau bước AI trong `create_report` (cần カテゴリ; lỗi chỉ ghi log, không làm hỏng việc gửi báo cáo) và trong lệnh `detect-duplicates` (`detect_all()`). Gom nhóm: nếu một ứng viên đã nằm trong nhóm `open` thì báo cáo vào nhóm đó (khớp nhiều nhóm thì chọn nhóm có cặp 類似度 cao nhất, **không gộp hai nhóm**); nếu không thì tạo nhóm mới gồm báo cáo và các ứng viên chưa có nhóm. Báo cáo thuộc nhóm `merged`/`dismissed` không phải ứng viên và không bị gom lại; báo cáo đã có nhóm thì bỏ qua (nên chạy lại là idempotent). Khoá `pg_advisory_xact_lock` làm mọi lần phát hiện chạy tuần tự để hai báo cáo gửi cùng lúc không tạo hai nhóm giống nhau (chỉ PostgreSQL).
- 類似度 của nhóm = trung bình mọi cặp của `0.5·(1−khoảng cách/bán kính) + 0.2·(1−Δngày/cửa sổ) + 0.3·độ giống tiêu đề` (mỗi thành phần kẹp 0..1; tiêu đề so bằng `SequenceMatcher` sau chuẩn hoá NFKC và bỏ khoảng trắng), tính lại mỗi khi thêm thành viên, lưu ở `duplicate_groups.similarity`. 重複確認 ẩn nhóm `open` còn **dưới 2 báo cáo thuộc `OPEN_STATUSES`** (các báo cáo kia đã xong/取り消し). Màn hiện 住所 từng báo cáo, khoảng cách (m) tới báo cáo đầu nhóm và ngày phát hiện nhóm.
- Câu thông báo lấy nguyên văn từ Excel, đặt trong `constants.py` (`MSG_*`). Lỗi nghiệp vụ dùng `ServiceError` (`app/services/__init__.py`), route bắt rồi `flash`.
- Thống kê và dashboard không tính báo cáo 取り消し. Bản đồ hiện báo cáo chưa xong, tức theo `OPEN_STATUSES` (gồm cả 保留, 業者対応中, 完了確認中).
- 地図表示 dùng **Google Maps JavaScript API**. Điền `GOOGLE_MAPS_API_KEY` vào `.env` là chạy. Ghim (`AdvancedMarkerElement`) đặt tại `latitude`/`longitude` của báo cáo. Khi gửi 報告する, `report_service.create_report()` ưu tiên GPS (tọa độ trên form, tức vị trí lúc chụp hoặc của thiết bị → tọa độ trong draft → EXIF của ảnh đính kèm). Nếu không có GPS, nó tìm tọa độ của 発生場所 (`address`) bằng API tìm địa chỉ của 国土地理院 (`utils/geo.search_address()`, `msearch.gsi.go.jp/address-search/AddressSearch`, miễn phí, không cần key, ghi 「出典：国土地理院」 dưới bản đồ) rồi lưu vào `latitude`/`longitude`. Không tìm được thì báo `MSG_ADDRESS_NOT_FOUND` (câu này không có trong Excel). Nguồn tọa độ lưu ở `reports.location_source`. Khi có GPS, server đổi tọa độ thành địa chỉ (`utils/geo.reverse_geocode()`, 逆ジオコーダー của 国土地理院) và lưu vào `reports.gps_address`. Nếu 発生場所 bỏ trống thì dùng địa chỉ này làm 発生場所, nên có GPS thì 発生場所 không còn bắt buộc. 報告詳細 của 職員 hiện 位置情報の取得元, 位置情報（GPS）の住所, 緯度経度 và link xem bản đồ, để đối chiếu khi người dân nhập sai địa chỉ. API này là API nội bộ của 地理院地図, có thể đổi hoặc dừng không báo trước; khi đó báo cáo không có GPS sẽ không gửi được. Đổi URL bằng `GEOCODER_URL`, để trống thì tắt tìm kiếm (test đặt trống, không gọi API thật). Màu ghim theo 緊急度 (đỏ 高, vàng 中, xanh 低 hoặc chưa phân loại). Bấm ghim hiện 件名, 住所, 危険度 và link 詳細を見る. `AdvancedMarkerElement` cần map ID (`GOOGLE_MAPS_MAP_ID`, mặc định `DEMO_MAP_ID` chỉ để dev). Không có key thì hiện ảnh tĩnh `static/images/map.avif` như trước. Key được gửi xuống trình duyệt nên phải giới hạn HTTP referrer và chỉ bật Maps JavaScript API trong Google Cloud.
- AI緊急度の再判定 (thêm theo thiết kế màn hình, không có trong Excel): báo cáo còn 未対応 và đã tạo quá `AI_REJUDGE_HOURS` giờ (mặc định 12) được AI phán định lại một lần (`report_service.rejudge_stale_reports()`, ghi `ai_analyses.trigger_type = 'rejudge'`). AI lỗi cũng tính là đã phán định lại, không thử lại. Kết quả phán định lại **không đổi** `reports` (kể cả khi `urgency` đang NULL). Mỗi báo cáo tạo một thông báo trên dashboard: AI ra 緊急度 khác hiện tại thì nhắc xem lại ở 緊急度の判定, giống thì ghi 変更なし, lỗi thì ghi 失敗. AI分析 hiện quy tắc này và cột 再判定状況.
- レポート出力: `GET /staff/stats/export/<pdf|csv>` (`staff.stats_export`) trả file tải về, nội dung giống màn hình 統計 (サマリー, カテゴリ別, 月別). Nút 確定 trong modal tải file bằng `fetch`, xong mới hiện 「PDF形式での出力が完了しました。」/「CSV形式での出力が完了しました。」. CSV là UTF-8 có BOM để mở bằng Excel không lỗi font. PDF tạo bằng reportlab, nhúng font IPAexゴシック (`app/fonts/ipaexg.ttf`, giấy phép IPA kèm cùng thư mục, chỉ nhúng các ký tự đã dùng).

### app/services/
| File | Nhiệm vụ |
|---|---|
| `auth_service.py` | ✅ `authenticate(role, login_id, password)` (kiểm tra đầu vào theo Excel, tách theo vai trò, khoá tạm sau `LOGIN_MAX_FAILURES` lần sai trong `LOGIN_LOCK_MINUTES` phút, đếm bằng một câu UPDATE nguyên tử; giờ lấy qua `auth_service._now()` để test patch), `set_password_by_operator(login_id, password)` (cho lệnh `set-password`). ✅ 2b: `request_password_reset(email, new_password, confirm_password)` (市民: kiểm email + mật khẩu một lần, lưu hash mật khẩu mới ở `pending_password_hash` chứ **chưa đổi** mật khẩu thật; chống lạm dụng 60 giây/3 lần mỗi giờ, token `secrets.token_urlsafe(32)` chỉ lưu SHA-256, gửi mail sau commit qua `app.utils.mail`), `is_reset_token_valid(token)`, `confirm_password_reset(token)` (bấm nút ở màn xác nhận: tiêu thụ token bằng một UPDATE nguyên tử rồi gán hash đang chờ vào user, mở khoá, vô hiệu token khác), `issue_reset_code(login_id)` (CLI) và `reset_password_with_code(role, login_id, code, new, confirm)` (職員/管理者, mã sai tính vào khoá tạm), `change_password(user_id, current_password=, new_password=, confirm_password=)`. 3a: `is_locked(user)`, `invalidate_reset_tokens(user_id)`; mọi đường đăng nhập/đặt lại dùng `User.is_usable`, tài khoản đã xoá bị coi như ID không tồn tại ở `authenticate` (cùng `MSG_LOGIN_FAILED`, không tăng số lần sai). Mọi cách đổi mật khẩu đều đổi dấu vân tay trong `User.get_id()` nên phiên cũ hết hiệu lực |
| `user_service.py` | Đăng ký người dân, khóa và mở khóa tài khoản nhân viên. ✅ Cập nhật プロフィール (tên, email), lưu và xoá アイコン của người dân, `create_admin()`/`create_staff()` (chung `_create_account`; dùng cho màn hình và CLI), `list_departments()`, `find_department(tên-hoặc-ID)`. ✅ 3a: `account_filters(role, department, q)`, `list_accounts(role=, department_id=, q=, limit=)` (職員/管理者 chưa xoá, mới nhất trước, ILIKE đã escape `%` `_`), `count_accounts()`, `account_row(user)` (dict cho template), `delete_account(user_id, actor_id=)` (xoá mềm; khoá các 管理者 dùng được theo thứ tự id rồi mới đếm nên không thể hết 管理者; chặn xoá chính mình), `unlock_account(user_id)`, `issue_reset_code_for_account(user_id)` (gọi `auth_service.issue_reset_code`, trả dict có mã để route đưa vào session), `restore_account(login_id)`, `list_deleted_accounts()`. `remove_test_users()` cho lệnh CLI (`TEST_LOGIN_IDS` đặt ở đây; dò động mọi FK không CASCADE trỏ vào `users.id` để biết tài khoản có dữ liệu gắn hay không) |
| `report_service.py` | Tạo báo cáo theo thứ tự lưu ảnh, gọi AI, kiểm tra trùng. Danh sách theo người dân. Tìm kiếm, lọc, phân trang cho nhân viên |
| `status_service.py` | Nơi duy nhất đổi trạng thái: kiểm tra luồng hợp lệ, ghi lịch sử, xử lý 取り消し. Cũng là nơi ghi việc 職員 đổi 緊急度 (vì cùng ghi vào 対応履歴) |
| `assignment_service.py` | Gán phòng ban theo gợi ý AI hoặc do nhân viên chọn |
| `duplicate_service.py` | ✅ Danh sách nhóm `open` (ẩn nhóm < 2 báo cáo chưa xong), `get_group`, `merge_group`, `dismiss_group(group_id, *, actor_id)`. Phát hiện trùng: `detect_for_report(report_id)` (khoá advisory → ứng viên: SQL thô theo `bounding_box` + Haversine → gom nhóm → một commit; trả dict `group_id/created/size/added/similarity` hoặc `None`), `detect_all()` (cho lệnh CLI; trả `scanned/created/joined/grouped/failed`), `pair_similarity`, `group_similarity`. Chỉ đánh dấu ứng viên trùng, 職員 xác nhận |
| `storage_service.py` | ✅ Kiểm tra định dạng thật (Pillow: JPEG, PNG, WebP) và dung lượng (10MB một ảnh), đặt tên ngẫu nhiên, lưu vào `UPLOAD_FOLDER` (`reports/`, `drafts/`, `icons/`), đọc GPS từ EXIF |
| `stats_service.py` | Đếm theo trạng thái, category, urgency, khu vực cho dashboard, bản đồ, thống kê |
| `notification_service.py` | ✅ Thông báo cho 職員: tạo, danh sách chưa đọc, 既読 (chung cho mọi 職員) |
| `citizen_notification_service.py` | ✅ お知らせ của 市民 (tách khỏi `notification_service`). Tạo, **không commit** (cùng transaction với sự kiện gốc): `create(report, type_, message, detail=None)`, `notify_report_received(report)`, `notify_ai_judged(report, analysis)`, `notify_status_changed(report, from_status, to_status, *, public_message=None)`. Đọc: `list_for_user(user_id, limit=100)` (mới nhất trước; mỗi phần tử là dict `id, report_id, type, type_label, created_at_text, message, detail` (thay bằng `message` nếu NULL), `is_unread`), `count_unread(user_id)`, `count_unread_or_zero(user_id)` (cho chuông; lỗi DB thì 0). Ghi (có commit): `mark_read(notification_id, *, user_id)` (trả số chưa đọc còn lại; `None` nếu không thuộc user hoặc không tồn tại ⇒ route trả 404), `mark_all_read(user_id)`. Mọi truy vấn lọc `user_id`. Được gọi từ `report_service.create_report`, `report_service.analyze_report`, `status_service.change_status` |

### app/ai/
| File | Nhiệm vụ |
|---|---|
| `base.py` | Interface `classify(text, photos, choices=) -> ClassificationResult`, các lớp kết quả, `AIError` |
| `rule_based.py` | Phân loại theo từ khóa như 穴, 街灯, 遊具, ゴミ. Dùng khi dev, khi test, và làm dự phòng khi API lỗi |
| `gemini_client.py` | Gọi Gemini (`google-genai`, `generate_content`, JSON theo schema). Đổi mọi lỗi SDK thành `AIError`. `prepare_image()` thu nhỏ ảnh và bỏ EXIF (GPS) trước khi gửi |
| `llm_classifier.py` | 3 chức năng dùng Gemini: phân loại báo cáo, kiểm tra ảnh chụp, đoán địa chỉ từ tọa độ |
| `prompts.py` | Prompt (`PROMPT_VERSION`) và cách đưa danh sách category, phòng ban hợp lệ cho AI chọn |
| `__init__.py` | `get_classifier()`, `get_photo_checker()`, `get_address_estimator()` chọn Gemini hoặc `rule_based` theo cấu hình |

**AI = Google Gemini.** Chỉ cần điền `GEMINI_API_KEY` vào `.env` là chạy. Không có key, hoặc đặt `AI_PROVIDER=rule_based`, thì dùng phân loại theo từ khóa, còn kiểm tra ảnh và đoán địa chỉ bị bỏ qua. Model mặc định `gemini-3.5-flash-lite`: bản stable, chưa có ngày ngừng hỗ trợ, rẻ và nhanh, nhận ảnh. Đổi model bằng `GEMINI_MODEL`.

| Bước trong Excel 県民 | Chức năng AI | Khi AI lỗi |
|---|---|---|
| 新規報告 3: AIで緊急度の判定 | Phân loại カテゴリ, 緊急度, 担当部署 kèm ảnh báo cáo. Lưu vào `ai_analyses`. Nếu `reports` chưa có giá trị thì dùng kết quả AI làm giá trị ban đầu (nhân viên sửa được) | Báo cáo vẫn lưu. Ghi một dòng `failed`, rồi phân loại lại bằng `rule_based` |
| 写真撮影 4b-1, 4c-1 | Ảnh có nét không, có phải ảnh dùng để báo cáo được không | Bỏ qua kiểm tra, cho đi tiếp |
| 位置情報の確認 3 | Đoán địa chỉ (đến 町名・丁目) từ tọa độ GPS | Hiện thông báo, người dân tự nhập địa chỉ ở 報告する |

Gọi AI sau khi đã commit báo cáo, bọc trong try. Nếu AI lỗi, báo cáo vẫn được lưu và có thể phân tích lại sau. Khi cần chạy nền bằng RQ hoặc Celery, chỉ sửa `report_service.py`. Không gửi tên hay email cho AI. Ảnh được bỏ EXIF trước khi gửi. Chỉ tọa độ được gửi để đoán địa chỉ.

### app/forms/, app/utils/
- **forms/**: định nghĩa form Flask-WTF, gồm quy tắc validate và CSRF. Ba màn hình reset mật khẩu sẽ dùng chung form.
- **utils/decorators.py**: `role_required(User.ROLE_xxx)` bảo vệ route theo vai trò.
- **utils/validators.py**: quy tắc dùng ở nhiều nơi. Hiện có `validate_new_password(password, confirm=None)` (trống, dưới 8 ký tự, không khớp; trả danh sách `(category, text)`, không ném lỗi) và `validate_operator_password(password, confirm=None)` (thêm: cấm mật khẩu thử công khai; dùng cho mọi chỗ đặt mật khẩu mới: reset, đổi, tạo tài khoản, CLI). Quy tắc ID nhân viên và admin không chứa `@` đang nằm trong `auth_service` và `user_service`.
- **utils/mail.py**: `send_mail(to, subject, body)` gửi qua SMTP + STARTTLS (`MAIL_*`), lỗi thành `MailError` (chỉ mang tên loại lỗi, kể cả khi header có xuống dòng); `to` phải là đúng một địa chỉ (không khoảng trắng, `,`, `;`, CR/LF), nếu không thì `MailError` và không gửi (cả ở chế độ log); `MAIL_SERVER` trống thì không gửi mà ghi nội dung vào log (WARNING). Link trong mail chỉ lấy từ `PUBLIC_BASE_URL`.
- **utils/geo.py**: `search_address()` tìm tọa độ từ địa chỉ bằng API của 国土地理院 (dùng khi báo cáo không có GPS). `reverse_geocode()` đổi tọa độ GPS thành địa chỉ bằng 逆ジオコーダー của 国土地理院. API này chỉ trả mã thành phố (`muniCd`), nên `SAITAMA_MUNICIPALITIES` chứa bảng mã → tên của 埼玉県 (từ `maps.gsi.go.jp/js/muni.js`). `distance_m(lat1, lng1, lat2, lng2)` tính khoảng cách (m) bằng Haversine (R = 6371008.8 m); `bounding_box(lat, lng, radius_m)` trả khung `(min_lat, max_lat, min_lng, max_lng)` hơi rộng hơn vòng tròn, để SQL lọc thô ứng viên trùng rồi mới kiểm lại bằng `distance_m`.

### app/blueprints/
Mỗi actor trong sơ đồ use case là một blueprint. Mỗi blueprint gồm `__init__.py` để re-export biến blueprint, và `routes.py` để khai báo blueprint và route.

**Quy ước:**
- Endpoint luôn có tiền tố blueprint, ví dụ `url_for('citizen.mypage')`, không viết `url_for('mypage')`.
- Template nằm trong thư mục cùng tên blueprint, ví dụ `citizen/report_new.html`.
- Route cần đăng nhập phải có `@role_required(User.ROLE_xxx)`.

**Bảng use case.** ✅ là đã có route, ⬜ là dự kiến.

| Actor | Use case | URL | Endpoint | Service |
|---|---|---|---|---|
| 市民 | アカウント作成 | `/register` | ✅ `auth.register` | chưa có service, logic nằm ở `auth/routes.py` (dự kiến user_service ở 2b). Server đã kiểm tra email phải có `@` (`MSG_EMAIL_NO_AT`) |
| Chung | ログイン, ログアウト | `/login/<role>`, `/logout` | ✅ `auth.login_*`, `auth.logout` | auth_service. Đăng nhập và đăng xuất đều `session.clear()` |
| Quản trị (CLI) | tạo 管理者/職員, cấp 再設定コード, đặt mật khẩu, khôi phục tài khoản đã xoá, gỡ tài khoản thử | lệnh `flask --app run create-admin`, `create-staff`, `issue-reset-code`, `set-password`, `restore-user`, `remove-test-users` | ✅ `app/cli.py` | user_service, auth_service |
| 市民 | パスワード再設定 (1 màn nhập email + mật khẩu mới, xác nhận qua email) | `/reset-password` (email, mật khẩu mới, xác nhận), `/reset-password/confirm/<token>` (GET: màn xác nhận; POST: nút 「パスワードの変更を確定する」), `/reset-password/complete` | ✅ `auth.reset_password`, `auth.reset_password_confirm`, `auth.reset_password_complete` | auth_service (`request_password_reset`, `confirm_password_reset`), utils/mail. Mật khẩu mới chỉ có hiệu lực khi người dùng bấm nút (POST, CSRF) ở trang xác nhận; mở link (GET) không đổi gì, tránh việc trình quét mail tự mở link làm mật khẩu của kẻ xấu có hiệu lực. Mật khẩu cũ dùng được tới lúc xác nhận. Luôn cùng một thông báo dù email có tồn tại hay không; trang confirm có `Referrer-Policy: same-origin` (không gửi Referer ra site ngoài nhưng vẫn gửi cho chính site, cần cho kiểm tra CSRF trên HTTPS) và `Cache-Control: no-store` |
| 職員, 管理者 | パスワード再設定 (bằng 再設定コード) | `/login/staff/reset-password`, `/login/admin/reset-password` (+ `/complete`) | ✅ `auth.reset_password_staff(_complete)`, `auth.reset_password_admin(_complete)` | auth_service (`reset_password_with_code`). Trang 職員 chỉ đổi được tài khoản 職員, trang 管理者 chỉ 管理者. Mã do quản trị cấp bằng `issue-reset-code` |
| 市民 | マイページ | `/citizen/mypage` | ✅ `citizen.mypage` | report_service |
| 市民 | 報告する | `/citizen/report` | ✅ `citizen.report_new` | report_service, ai |
| 市民 | 写真撮影 | `/citizen/report/camera` | ✅ `citizen.report_camera` | report_service, storage_service, ai |
| 市民 | 撮影写真の確認 | `/citizen/report/photo`, ảnh: `/citizen/report/photo/image` | ✅ `citizen.report_photo`, `citizen.report_photo_image` | storage_service |
| 市民 | 位置情報の確認 | `/citizen/report/location` | ✅ `citizen.report_location` | report_service, ai |
| 市民 | 報告完了画面 | `/citizen/reports/<id>/complete` | ✅ `citizen.report_complete` | report_service |
| 市民 | 履歴を表示する | `/citizen/reports` | ✅ `citizen.report_list` | report_service |
| 市民 | 対応状況を表示する | `/citizen/reports/<id>` | ⬜ | report_service |
| 市民 | 報告を取り消す | `POST /citizen/reports/<id>/cancel` | ⬜ | status_service |
| 市民 | お知らせを表示する (chuông ở header + màn お知らせ) | `/citizen/notifications` | ✅ `citizen.notifications` | citizen_notification_service (`list_for_user`). Biến template `notifications`. Chỉ 市民, 職員/管理者 bị chặn. Mọi template của `citizen_bp` có biến `citizen_unread_count` (context processor; 0 nếu chưa đăng nhập hoặc không phải 市民) |
| 市民 | お知らせを既読にする | `POST /citizen/notifications/<id>/read` (fetch, header `X-CSRFToken`, trả JSON `{"unread_count": n}`, không phải của mình ⇒ 404), `POST /citizen/notifications/read` (「すべて既読にする」, redirect về `citizen.notifications`) | ✅ `citizen.notification_read`, `citizen.notifications_read_all` | citizen_notification_service (`mark_read`, `mark_all_read`) |
| 市民 | パスワード変更 | `/citizen/password` | ✅ `citizen.change_password` | auth_service. Thành công thì `session.clear()` + `login_user` lại (giữ remember nếu có) nên phiên khác bị đăng xuất |
| 市民 | プロフィール設定 | `/citizen/profile` | ✅ `citizen.profile` | user_service |
| 市民 | アイコン設定 | `/citizen/profile/icon`, ảnh: `/citizen/profile/icon/image` | ✅ `citizen.profile_icon`, `citizen.profile_icon_image` | user_service, storage_service |
| 市民 | アイコン撮影 | `/citizen/profile/icon/camera` (ảnh chụp gửi tới `citizen.profile_icon`) | ✅ `citizen.profile_icon_camera` | user_service, storage_service |
| 市民 | アイコン削除 | `POST /citizen/profile/icon/delete` | ✅ `citizen.profile_icon_delete` | user_service, storage_service |
| 職員 | ダッシュボード | `/staff/dashboard` | ✅ `staff.dashboard` | stats_service (`priority_reports`: 本日の優先対応案件は最大 10 件、`count_priority_reports` の総数が 10 を超えると 報告一覧 ボタンを表示; `map_points`: 地域の問題マップ（Google Maps、map.js 共用）) |
| 職員 | 報告一覧 | `/staff/reports` | ✅ `staff.report_list` | report_service |
| 職員 | 報告内容, 対応状況, 対応履歴 | `/staff/reports/<id>` | ✅ `staff.report_detail` | report_service, status_service |
| 職員 | 対応状況を更新する | `POST /staff/reports/<id>/status` | ✅ `staff.report_status_update` | status_service |
| 職員 | 担当部署を設定する | `POST /staff/reports/<id>/department` | ⬜ | assignment_service |
| 職員 | AI 分類, 緊急度判定 | tự động khi tạo; chạy lại bằng `POST /staff/reports/<id>/reanalyze` (từng dòng) và `POST /staff/ai-analysis/reanalyze` (nút chung, tối đa `AI_RERUN_BATCH_LIMIT` báo cáo / `AI_RERUN_TIME_BUDGET_SECONDS` giây) | ✅ `staff.report_reanalyze`, `staff.ai_reanalyze_all` | report_service, ai |
| 職員 | AI分析・自動判定ログ | `/staff/ai-analysis` | ✅ `staff.ai_analysis` | report_service |
| Hệ thống | AI緊急度の再判定 | lệnh `flask --app run rejudge-urgency` (chạy định kỳ) | ✅ `app/cli.py` | report_service, notification_service, ai |
| 職員 | お知らせを既読にする | `POST /staff/notifications/<id>/read`, `POST /staff/notifications/read` | ✅ `staff.notification_read`, `staff.notifications_read_all` | notification_service |
| 職員 | 地図表示 | `/staff/map` ✅, `/api/reports/map` ⬜ | ✅ `staff.report_map` | stats_service |
| 職員 | 対応管理・重複確認 | `/staff/duplicates` | ✅ `staff.duplicates` | duplicate_service |
| 職員 | 代表案件に統合する | `/staff/duplicates/<group_id>/merge` | ✅ `staff.duplicate_merge` | duplicate_service |
| 職員 | 一括対応ステータス変更 | `/staff/duplicates/<group_id>/status` | ✅ `staff.duplicate_batch_status` | status_service |
| 職員 | 重複なしにする | `POST /staff/duplicates/<group_id>/dismiss` | ✅ `staff.duplicate_dismiss` | duplicate_service |
| Hệ thống | 重複の自動検出 | khi gửi 報告する (sau bước AI) và lệnh `flask --app run detect-duplicates` (chạy tay) | ✅ `report_service.create_report`, `app/cli.py` | duplicate_service, utils/geo |
| 職員 | 統計 | `/staff/stats` ✅, `/api/stats` ⬜ | ✅ `staff.stats` | stats_service |
| 職員 | レポート出力 (PDF/CSV) | `/staff/stats/export/<fmt>` | ✅ `staff.stats_export` | stats_service |
| 管理者 | ポータル | `/admin/portal` | ✅ `admin.portal` | user_service (`list_accounts(limit=5)`, `count_accounts`, `account_row`): 5 tài khoản mới nhất + tổng số (3a) |
| 管理者 | ユーザー一覧 | `/admin/users` (query `role`, `department`, `q`) | ✅ `admin.user_list` | user_service. 職員/管理者 chưa xoá, lọc 役割/部署, tìm tên/ID. Biến template: `users`, `departments`, `filters`, `user_total`, `issued_code`. Trang luôn có `Cache-Control: no-store` và `Referrer-Policy: same-origin` (không dùng `no-referrer`: trên HTTPS Flask-WTF cần Referer để kiểm tra CSRF) |
| 管理者 | ユーザー削除 (xoá mềm) | `POST /admin/users/<id>/delete` | ✅ `admin.user_delete` | user_service (`delete_account`). Không xoá được chính mình hay 管理者 dùng được cuối cùng; log WARNING (action, actor_id, target_id); luôn redirect về `user_list` giữ bộ lọc |
| 管理者 | ロック解除 | `POST /admin/users/<id>/unlock` | ✅ `admin.user_unlock` | user_service (`unlock_account`) |
| 管理者 | 再設定コード発行 | `POST /admin/users/<id>/reset-code` | ✅ `admin.user_reset_code` | user_service (`issue_reset_code_for_account`). Mã đưa vào `session['reset_code']` (kèm `issued` epoch) rồi redirect `user_list`; GET `session.pop` và chỉ hiện mã nếu chưa quá 120 giây (quá hạn: `MSG_RESET_CODE_GONE`). Mã nằm tạm trong cookie session có ký (không mã hoá) tối đa 120 giây |
| 管理者 | 新規職員アカウント登録 (職員 hoặc 管理者) | `/admin/users/register`, `/admin/users/register/complete` | ✅ `admin.user_register`, `admin.user_register_complete` | user_service (`create_staff`, `create_admin`, `list_departments`). Lỗi thì PRG, dữ liệu nhập (trừ mật khẩu) giữ ở `session['user_form']` |
| 管理者 | システム管理 | `/admin/departments`, `/admin/categories` | ⬜ | user_service |

### app/templates/, app/static/
- **layouts/base.html**: khung HTML chung, nạp Bootstrap, Bootstrap Icons, Chart.js và các file CSS. Các layout theo vai trò sẽ kế thừa base và chứa navbar, sidebar. Trang con chỉ viết phần nội dung.
- **components/**: các mảnh dùng lại bằng `{% include %}`. Màu badge trạng thái chỉ định nghĩa ở đây (`_status_badge.html`). Màu badge 緊急度 hiện còn viết ở 4 template (`staff/dashboard.html`, `report_list.html`, `report_detail.html`, `ai_analysis.html`), sẽ gom vào `_urgency_badge.html` ở giai đoạn 6.
- **staff/mockup_report_triage.html**: mockup giao diện màn hình phân loại báo cáo, chưa có route. Dùng làm tham khảo khi làm `staff/report_list.html` và `staff/report_detail.html`, làm xong thì xóa.
- **static/css/**: `tokens.css` chứa biến màu và khoảng cách, `base.css` chứa style chung, `components.css` chứa style cho từng thành phần, `responsive.css` chứa style cho điện thoại (nạp cuối cùng trong `base.html`, dùng các class như `citizen-page`, `report-page`, `guide-page`). Không căn giữa cả `body`. Màn hình nào cần căn giữa trên điện thoại thì dùng class riêng (`login-page-container`, `reset-password-container`, `report-camera-container`, `camera-page-container`…). Style cho sidebar của 県民 phải đặt dưới `.citizen-page` để không ảnh hưởng tới 職員 và 管理者.
- **Lịch sử trình duyệt (`static/js/navigation.js`, nạp trong `base.html`).** Đi qua lại giữa màn cha và màn con, hoặc gửi form, không được làm nút Back của trình duyệt quay về màn con hay hiện lại cùng một màn hình. Quy ước:
  - Màn con khai báo màn cha bằng `{% block nav_parent %}{{ url_for('...') }}{% endblock %}`. Hiện có: 写真撮影, 撮影写真の確認, 位置情報の確認 (cha là 報告する), アイコン設定, アイコン撮影 (cha là プロフィール設定), 代表案件に統合, 一括対応ステータス変更 (cha là 重複確認), 新規職員アカウント登録 và màn hoàn tất (cha là ユーザー一覧, từ 3a; ユーザー一覧 là màn độc lập, không có nav_parent).
  - Link di chuyển trong màn con, hoặc từ màn con về màn cha, thêm `data-nav="replace"`. Khi về màn cha mà mục lịch sử ngay trước đó chính là màn cha, script gọi `history.back()` rồi tải lại màn cha để hiện nội dung mới (ảnh vừa chụp, flash). Trường hợp khác thì dùng `location.replace`. Từ JS thì gọi `window.historyNav.go(url)`.
  - Form thêm `data-nav="replace"` sẽ được gửi bằng `fetch` kèm header `X-Replace-History`. `create_app()` đổi response redirect thành JSON `{"redirect": url}` (flash vẫn giữ trong session), rồi script chuyển trang mà không thêm mục lịch sử. Vì vậy route của các form này **luôn trả redirect, kể cả khi lỗi** (flash + redirect, PRG). Nội dung đã nhập thì giữ trong session (`report_form`, `profile_form`) để hiện lại. Không render template ngay trong POST.
  - Link mở màn con từ màn cha và link giữa các màn độc lập (sidebar, 一覧 → 詳細) vẫn là link thường.
- **static/js/**: `report_form.js` lấy GPS (ảnh xem trước do server render, trỏ tới `citizen.report_photo_image`, JS không vẽ; ở 写真撮影, chỉ lấy GPS sau khi người dân chọn 今回だけ使用 hoặc オンにする trong modal 位置情報確認, キャンセル thì quay về 報告する; ảnh chụp từ canvas không có EXIF GPS nên nút 撮影する chờ lấy xong vị trí rồi mới gửi; GPS độ chính xác cao lỗi/timeout thì lấy lại 1 lần với độ chính xác thấp), `map.js` vẽ Google Maps với ghim theo 緊急度 (đỏ 高, vàng 中, xanh 低), dữ liệu lấy từ `<script id="mapReports">` trong `staff/map.html`, `charts.js` còn rỗng (chỉ một dòng chú thích). Biểu đồ Chart.js hiện viết inline trong `staff/stats.html` và `staff/dashboard.html`; sau này chuyển sang `charts.js` và lấy dữ liệu từ API.

---

## 5. Cách thêm một tính năng mới

Ví dụ: thêm màn hình 履歴を表示する cho người dân.

1. **Model**: nếu cần bảng mới, tạo file trong `app/models/` và import trong `models/__init__.py`.
2. **Service**: viết hàm trong service phù hợp, ví dụ `report_service.list_by_citizen(user_id)`. Hàm nhận tham số thường và trả dữ liệu, không đụng `request`.
3. **Form**: nếu có nhập liệu, thêm form trong `app/forms/`.
4. **Route**: thêm route vào blueprint của actor, nhớ `@role_required`.
5. **Template**: tạo trong thư mục của blueprint, dùng layout và component có sẵn.
6. **Test**: thêm test cho service trong `tests/`.
7. **README**: cập nhật bảng use case, đổi ⬜ thành ✅.

Nếu thêm blueprint mới, hãy tạo `app/blueprints/<tên>/__init__.py` và `routes.py`, rồi đăng ký trong `create_app()`.

---

## 6. Quy ước chung

- Comment trong code viết bằng tiếng Nhật, theo phong cách code hiện có.
- Mã trạng thái, vai trò và độ khẩn cấp trong DB là tiếng Anh. Nhãn hiển thị tiếng Nhật lấy từ `constants.py`.
- Email người dân lưu ở dạng chữ thường. ID nhân viên và admin không chứa `@`.
- Không commit `.env` và ảnh trong `instance/uploads/`.
- Không đặt bí mật như API key hay mật khẩu DB trong code. Luôn đọc qua `config.py` từ `.env`.
- **Mọi form POST phải có `csrf_token`**: `<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">` (kể cả form đăng xuất và form tự submit bằng JS). Request `fetch` gửi token qua header `X-CSRFToken`. Route thay đổi dữ liệu chỉ nhận POST, không dùng GET (đăng xuất là `POST /logout`, GET trả 405). Thiếu hoặc sai token thì server flash `MSG_CSRF_EXPIRED` rồi quay về trang trước. Khi thêm form mới, thêm token ngay, và trang hiển thị flash phải include `components/_flash.html`.

---

## 7. Lộ trình phát triển

1. ✅ Tách app thành package, dùng `create_app()` và blueprint theo actor.
2. ✅ Thêm **Flask-Migrate**. Đã khai báo trong `extensions.py` và có thư mục `migrations/`. Seed không còn gọi `create_all`.
3. ✅ Thêm **Flask-WTF** (`CSRFProtect`) để có CSRF cho mọi form POST, đăng xuất bằng POST, cờ cookie, seed tài khoản test chỉ khi có `--with-test-users` (giai đoạn 1, đã nghiệm thu 2026-11-11). Giai đoạn 2a thêm khoá đăng nhập tạm, làm mới session, ProxyFix và lệnh tài khoản (xem mục 8).
4. ✅ Viết lại **đặt lại mật khẩu** (2b): người dân nhập email + mật khẩu mới ở một màn rồi xác nhận bằng link trong email (từ 2026-11-17; trước đó là 2 bước), 職員 và 管理者 dùng 再設定コード do quản trị cấp bằng CLI; thêm đổi mật khẩu của 市民, đăng ký 職員/管理者 lưu DB, đăng xuất phiên cũ khi đổi mật khẩu.
5. ✅ Viết `constants.py` và các model phòng ban, danh mục, báo cáo, ảnh, lịch sử, kết quả AI, nhóm trùng, kèm migration và dữ liệu seed. Các service đã đọc và ghi DB thật (bước 6 và 7).
6. ✅ Làm luồng báo cáo của người dân (chụp ảnh, GPS, đoán địa chỉ, gửi báo cáo, AI phán định), マイページ, 報告一覧, プロフィール, アイコン. Có cả `rule_based.py` và Gemini.
7. ✅ Làm danh sách, chi tiết, cập nhật trạng thái và lịch sử cho nhân viên. Thay số liệu hard-code trên dashboard bằng dữ liệu thật. Kèm AI分析 (chỉ đọc kết quả), 重複確認, 統合, 一括変更, 統計. Còn lại: xem mục 8.
8. Bản đồ Google Maps ✅. ⬜ Thêm blueprint `api` và biểu đồ.
9. Thêm `llm_classifier.py` ✅ (Gemini) và phần phát hiện trùng của `duplicate_service.py` ✅ (2026-11-24: tự phát hiện khi gửi báo cáo, lệnh `detect-duplicates`, 重複なし).
10. Quản lý người dùng và dữ liệu master cho admin. ✅ 3a: danh sách, xoá mềm, mở khoá, cấp 再設定コード, ポータル dữ liệu thật, CLI `restore-user` (phần giao diện do frontend làm theo hợp đồng ở mục 5). ⬜ 3b: master 部署/カテゴリ.
11. ⬜ Thêm layout theo vai trò, component và trang lỗi để bỏ phần lặp trong template.

## 8. Vấn đề đã biết

- **Đặt lại mật khẩu (2b) và các giới hạn đã chấp nhận.**
  - Khi chưa cấu hình SMTP (`MAIL_SERVER` trống, hiện trạng EC2), mail **không được gửi** mà nội dung, **gồm cả link chứa token**, được ghi vào log ứng dụng (WARNING; xem bằng `journalctl`). Ai đọc được log đều đặt lại được mật khẩu 市民 đang có yêu cầu. Cấu hình `MAIL_*` và `PUBLIC_BASE_URL` trên EC2 để dùng thật. App cảnh báo khi khởi động nếu `SESSION_COOKIE_SECURE=true` mà `MAIL_SERVER` trống.
  - Thời gian phản hồi của `/reset-password` hơi lâu hơn khi email tồn tại (có gửi SMTP), nên về lý thuyết đo thời gian có thể đoán được email đã đăng ký. Thông báo thì luôn giống nhau. Chưa có rate limit theo IP (chỉ giới hạn theo tài khoản: 60 giây/3 lần mỗi giờ).
  - 職員/管理者 **chưa tự đổi mật khẩu khi đang đăng nhập** (chỉ có reset bằng 再設定コード hoặc `set-password`); việc xin mã phải qua quản trị ngoài hệ thống (管理者 cấp ở `/admin/users`, hoặc `issue-reset-code`).
  - Đổi mật khẩu bằng bất kỳ cách nào (đổi, mail, mã, `set-password`) làm mọi phiên và cookie ghi nhớ cũ của tài khoản đó hết hiệu lực. Lần deploy 2b đầu tiên, **mọi người dùng phải đăng nhập lại một lần** (phiên cũ không có dấu vân tay mật khẩu).
- **Xoá tài khoản 職員/管理者 là xoá mềm (3a); mọi service sau này phải dùng `User.is_usable`.** `del_flag` + `deleted_at` + `deleted_by_id` ghi cùng một lần; `is_usable = is_active AND NOT del_flag` là điều kiện duy nhất cho "tài khoản dùng được" (đăng nhập, `load_user`, đặt lại mật khẩu, cấp mã, đếm 管理者 còn lại). **Không** dùng `is_active` đơn lẻ vì sẽ bỏ sót tài khoản đã xoá. Hệ quả:
  - Tài khoản đã xoá không đăng nhập được và nhận cùng thông báo như ID không tồn tại (không tăng số lần sai); phiên và cookie ghi nhớ cũ chết ở request kế tiếp; mã/token chưa dùng bị vô hiệu.
  - `login_id` vẫn giữ chỗ: tạo mới trùng ID đã xoá chỉ báo 「既に使用されています」 mà không nói là tài khoản đã xoá. Muốn dùng lại ID thì `restore-user`.
  - Chưa có màn hình khôi phục (chỉ CLI), chưa có bảng nhật ký thao tác (chỉ `deleted_by_id` và log WARNING `action=delete|unlock|reset_code actor_id target_id`).
  - Rủi ro đã chấp nhận (a): mã 再設定コード vừa cấp ở `/admin/users` nằm tạm trong cookie session (có ký, không mã hoá) tối đa 120 giây trên trình duyệt của 管理者; trang có `Cache-Control: no-store`.
  - Rủi ro đã chấp nhận (b): `restore-user` làm phiên và cookie ghi nhớ cũ của tài khoản hợp lệ lại (dấu vân tay mật khẩu không đổi). Nếu nghi cookie cũ bị lộ thì chạy `set-password` ngay sau khi khôi phục (CLI cũng nhắc, và cảnh báo thêm nếu mật khẩu vẫn là `password123`).
  - Sau khi deploy 3a phải chạy `flask --app run db upgrade` (local và EC2).
  - Cảnh báo downgrade: migration `d4a91c7e5b36` khi `db downgrade` sẽ đặt `is_active=false` cho mọi tài khoản đã xoá mềm trước khi bỏ cột `del_flag` (để không sống lại). Upgrade lại không tự bật `is_active`; muốn dùng lại thì sửa tay trong DB.
- **ProxyFix mặc định TẮT (`PROXY_FIX_HOPS=0`), nên `WTF_CSRF_SSL_STRICT` chỉ có tác dụng khi bật nó.** Khi tắt, `request.is_secure` luôn False sau Nginx, bước kiểm Referer/Host chỉ cho HTTPS không chạy; token CSRF vẫn được kiểm đầy đủ. Bật bằng `PROXY_FIX_HOPS=1` (số tầng proxy) sẽ tin `X-Forwarded-For` và `X-Forwarded-Proto` (không tin `X-Forwarded-Host`). **Chỉ bật khi đã kiểm tra trên EC2:** (1) Nginx có `proxy_set_header Host $host;` và `proxy_set_header X-Forwarded-Proto $scheme;`, (2) gunicorn chỉ bind `127.0.0.1` hoặc socket (không để client đi thẳng vào gunicorn tự gửi header giả), rồi restart. Nếu sau đó mọi form đều báo 「セッションの有効期限が切れました…」 thì đặt `WTF_CSRF_SSL_STRICT=false` rồi restart (và báo lại để xem Host/Referer).
- **Chống dò mật khẩu: khoá tạm theo tài khoản (2a), chưa có rate limit theo IP và chưa có rate limit cho đăng ký.** Sai mật khẩu `LOGIN_MAX_FAILURES` (5) lần liên tiếp thì khoá `LOGIN_LOCK_MINUTES` (15) phút, lưu ở `users.failed_login_count`/`locked_until` nên đúng khi chạy nhiều worker gunicorn. Khi đang khoá, mật khẩu đúng cũng bị từ chối và sai thêm không gia hạn khoá. Hệ quả chấp nhận: ai biết ID cũng có thể khoá người khác 15 phút, và thông báo khoá làm lộ ID có tồn tại. 管理者/職員 bị khoá thì 管理者 bấm ロック解除 ở `/admin/users` (hoặc dùng `set-password`). Việc xoá tài khoản thử trên EC2 do người dùng chạy `remove-test-users` (xem mục 1); cho đến khi chạy, `password123` vẫn đăng nhập được. `session.clear()` khi đăng nhập và đăng xuất nên không còn session fixation hay nháp của người trước. Đăng xuất mọi phiên khi đổi mật khẩu đã làm ở 2b. Chưa có: chuẩn hoá ID 職員 về chữ thường. Mã sai ở trang 再設定コード cũng tính vào khoá tạm (cùng bộ đếm với đăng nhập), nên trong lúc khoá cũng không đặt lại được bằng mã (dùng `set-password`).
- **Luồng báo cáo của người dân.** Ảnh chụp, tọa độ và địa chỉ đoán được giữ trong `session['report_draft']` cho đến khi gửi 報告する. Ảnh chụp được lưu tạm ở `instance/uploads/drafts/`, khi gửi thì chuyển sang `reports/`. Còn thiếu:
  - Camera và GPS của trình duyệt chỉ chạy trên **HTTPS** hoặc `localhost`. Nếu chạy HTTP thì trang 写真撮影 chuyển sang ô chọn ảnh, còn GPS chỉ lấy được từ EXIF của ảnh. Khi không có GPS, tọa độ được tìm từ 発生場所 mà người dân nhập (API 国土地理院), nên vẫn gửi được báo cáo nếu địa chỉ tìm được. Server EC2 đã bật HTTPS tại `https://team3app.duckdns.org` (Nginx + Let's Encrypt/Certbot, tự gia hạn bằng `certbot.timer`, HTTP chuyển hướng 301 sang HTTPS). Nếu Learner Lab đổi Public IP thì phải cập nhật IP trên DuckDNS, nếu không trang web và việc gia hạn chứng chỉ sẽ lỗi. Người dùng từ chối quyền vị trí và ảnh không có GPS thì tọa độ lấy từ 発生場所. Độ chính xác chỉ đến mức địa chỉ (thường là 丁目). Nguồn tọa độ lưu ở `reports.location_source`, hiện ở 報告詳細. Có thể làm thêm phần chọn điểm trên bản đồ (Google Maps).
  - Ở 報告する, nếu chưa có tọa độ lúc chụp thì `report_form.js` lấy vị trí hiện tại của thiết bị, và vị trí này được ưu tiên hơn địa chỉ đã nhập. Nếu người dân báo cáo khi đã rời khỏi chỗ xảy ra sự cố, ghim sẽ nằm ở vị trí lúc gửi (報告詳細 hiện 端末の位置情報（GPS） và địa chỉ của vị trí đó để 職員 nhận ra).
  - `gps_address` chỉ có cho vị trí trong 埼玉県 (bảng mã thành phố trong `utils/geo.py`). Báo cáo tạo trước migration `5c2e8f1b7a90` có `location_source` là NULL, hiện là 不明.
  - Địa chỉ do Gemini đoán từ tọa độ có thể sai. Người dân sửa được ở 報告する. Nếu cần chính xác thì nên dùng API reverse geocoding (ví dụ của 国土地理院).
  - Ảnh tạm trong `drafts/` của người bỏ dở giữa chừng chưa được tự động xoá.
  - Thông báo kiểm tra phía trình duyệt (JS) ở ログイン, アカウント作成, パスワード再設定 của 県民 đã theo câu chữ Excel 県民. Thông báo phía server của ログイン (cả 3 vai trò) đã theo Excel qua `auth_service` (`MSG_ID_*`, `MSG_PASSWORD_*` trong `constants.py`); アカウント作成 và パスワード再設定 trong `auth/routes.py` vẫn chưa giống hẳn (ví dụ còn 「パスワードは8文字以上で入力してください。」, câu khi bỏ trống hết). Màn hình パスワード変更 (đặt lại bằng email) chưa làm, xem bước 4.
- **Màn hình `auth` (ログイン, アカウント作成, パスワード再設定) chưa dùng `navigation.js`.** Khi nhập sai, route render lại template ngay trong POST, nên lịch sử có thêm một mục của cùng màn hình. Muốn sửa thì chuyển sang PRG như form của 市民 (xem mục 4, static/js).
- **緊急度の判定, AI再判定, trạng thái mới (2026-11-10) chưa có trong [docs/db/db_design.md](docs/db/db_design.md) và sơ đồ trong `docs/`.** Cột `report_status_histories.from_urgency/to_urgency/urgency_reason`, và 5 trạng thái mới mới chỉ được mô tả trong README này (bảng `staff_notifications` và `citizen_notifications` đã có ở mục 3.9, 3.10 của db_design.md từ 2026-11-18).
- **お知らせ của 市民 (2026-11-18).** Thông báo chỉ sinh từ lúc deploy, **không backfill** cho báo cáo cũ. Ba nguồn, cùng transaction với sự kiện gốc (insert thông báo lỗi thì cả bước đó bị rollback):
  - `create_report` ⇒ 報告受付. `analyze_report` ⇒ AI判定, **chỉ** khi `trigger_type=initial` và có kết quả thành công (kể cả khi Gemini lỗi rồi rơi về `rule_based`); AI lỗi hoàn toàn, 再判定 (`rejudge`) và 手動再実行 (`manual`) **không** báo cho 市民.
  - `status_service.change_status` ⇒ 職員から, **chỉ** khi actor là 職員 và trạng thái thực sự đổi (kể cả 一括変更). Chỉ thêm ghi chú, chỉ đổi 緊急度, hay 市民 tự 取り消し thì không báo. `comment` (ghi chú nội bộ), tên 職員 và `urgency_reason` không bao giờ vào `message`/`detail`; chỉ `public_message` được thêm vào cuối `detail`.
  - 「すべて既読にする」 (`mark_all_read`) đánh dấu **mọi** thông báo chưa đọc của người đó, kể cả những cái nằm ngoài 100 dòng đang hiện. Danh sách chỉ hiện 100 mới nhất; thông báo tăng không giới hạn và không bị dọn.
  - Câu chữ `MSG_CNOTICE_*` không có trong Excel (người dùng duyệt cùng kế hoạch A20261118-123834). Chưa có link từ thông báo sang 対応状況 (route `/citizen/reports/<id>` chưa làm) và chưa gửi mail.
  - Sau khi deploy phải chạy lệnh `db upgrade` của Flask-Migrate (local và EC2). DB dev đang ở `d4a91c7e5b36` nên sẽ áp cả `e5b2c8f1a937` và `f6c3d9a2b148`.
- **AIモデル再実行 (2026-11-24, kế hoạch A20261124-123404).** Câu chữ `MSG_AI_RERUN_*` không có trong Excel (người dùng duyệt cùng kế hoạch). Cần chạy `db upgrade` (revision `a7d2e4b9c561`: CHECK `ck_ai_analyses_trigger_type` thêm `manual`).
  - Nút từng dòng (`report_service.reanalyze_report`): chỉ ở dòng log mới nhất của mỗi báo cáo, mọi trạng thái trừ 取り消し (gọi thẳng với báo cáo 取り消し ⇒ flash đỏ). Nút chung (`reanalyze_pending_reports`): báo cáo trong `OPEN_STATUSES` mà chưa có lần AI nào thành công, hoặc (chỉ khi Gemini đang bật) lần thành công mới nhất là `rule_based` do Gemini lỗi. Chạy báo cáo chưa từng có dòng AI trước, rồi lần thử cũ nhất trước; tối đa `AI_RERUN_BATCH_LIMIT` (5) báo cáo và `AI_RERUN_TIME_BUDGET_SECONDS` (20) giây; bỏ qua báo cáo có dòng AI trong 60 giây gần nhất (chống bấm trùng).
  - Mỗi lần ghi dòng `trigger_type='manual'`; thành công thì chỉ điền ô **trống** của `reports` (như `initial`, khác `rejudge`), **không** tạo `citizen_notifications` hay `staff_notifications`. Kết quả hiện bằng flash sau PRG về `staff.ai_analysis` (giữ `search`/`id`).
  - Giới hạn: chạy đồng bộ trong request (tối đa khoảng 20 giây cộng thêm thời gian của lần gọi cuối); chọn đối tượng bằng Python trên các dòng `ai_analyses` của báo cáo chưa xong, ổn với quy mô hiện tại. Số đếm ở nút (`reanalyze_target_count`) dùng cùng điều kiện nên tạm giảm trong 60 giây sau khi chạy.
- **AI再判定:** `analyze_report()` commit kết quả AI trước, sau đó mới tạo thông báo. Nếu bước tạo thông báo lỗi, báo cáo vẫn tính là đã phán định lại, nên thông báo đó bị mất. Nút 「すべて既読にする」 đánh dấu mọi thông báo chưa đọc, kể cả những cái nằm ngoài 10 dòng đang hiện.
- **Màn hình nhân viên đã đọc và ghi DB thật, nhưng còn thiếu:**
  - 添付写真 ở 報告詳細 chưa hiện vì chưa có route trả ảnh có kiểm tra quyền (`storage_service` đã có).
  - 担当部署を設定する (`POST /staff/reports/<id>/department`) chưa có route.
  - Danh sách 報告一覧 và AI分析 chưa phân trang. AI分析 chỉ hiện 50 lần phân tích mới nhất.
- **Khi không có `GOOGLE_MAPS_API_KEY`, ghim bản đồ đặt theo phép chiếu gần đúng.** `stats_service.map_points()` vẫn trả `top`/`left` (%) trên ảnh tĩnh theo khung `MAP_BOUNDS` cho trường hợp này. Vị trí chỉ gần đúng.
- **重複の自動検出 (2026-11-24, kế hoạch A20261124-143227): giới hạn v1.** Không cần migration. Câu chữ `MSG_GROUP_DISMISSED` và modal 重複なし không có trong Excel (người dùng duyệt cùng kế hoạch). Sau khi deploy, chạy `flask --app run detect-duplicates` một lần (local và EC2) để gom báo cáo cũ.
  - Nhóm có thể dạng **chuỗi**: A–B ≤ 100 m và B–C ≤ 100 m nhưng A–C > 100 m vẫn cùng nhóm (thành phần khoảng cách của cặp A–C là 0 nên 類似度 nhóm thấp hơn). Khoảng cách hiện trên màn là so với báo cáo đầu nhóm.
  - **Không gộp nhóm**: nếu báo cáo mới khớp hai nhóm `open`, nó chỉ vào nhóm có cặp 類似度 cao nhất. Khi vào nhóm `open` có sẵn, các ứng viên chưa có nhóm không bị kéo theo (lệnh quét sẽ đưa chúng vào sau).
  - **Không phát hiện trùng với 代表案件 đã 統合** (nhóm `merged`) hoặc nhóm `dismissed`: báo cáo mới giống báo cáo đã xử lý xong nhóm sẽ không được báo. Không tự gắn `duplicate_of_id` (chỉ 職員 quyết định).
  - Báo cáo AI không phân loại được (không có カテゴリ) bị bỏ qua lúc gửi; lệnh quét bắt lại sau khi 職員 hoặc AI đặt カテゴリ. Chưa có lịch chạy định kỳ cho lệnh quét. `merge_group` chưa khoá hàng như `dismiss_group`, nên merge đồng thời với một lần phát hiện thêm báo cáo vào đúng nhóm đó có thể bỏ sót báo cáo mới (hiếm; báo cáo vẫn nằm trong nhóm, chỉ chưa nhận `duplicate_of_id`).
  - Khoá advisory là một khoá toàn cục (phát hiện chạy tuần tự), chỉ PostgreSQL; với số báo cáo hiện tại là đủ.
- **Cờ `ai_detected` trong dữ liệu mẫu của `duplicate_service`** quyết định nút AI確認 hay 個別確認. Đã chốt như sau:
  - Không có báo cáo do AI tạo. Người báo cáo luôn là người dân.
  - `ai_detected` là giá trị tính được từ `ai_analyses` (`duplicate_service._group_dict`): báo cáo có `ai_analyses` thành công thì `ai_detected` đúng.
  - Dữ liệu mẫu có người báo cáo "AIシステム" đã bỏ khi chuyển sang DB.
- **Màn hình 新規職員アカウント登録 (2b) đã lưu thật** (職員 bắt buộc 所属部署 lấy từ `departments` với value là `id`; 管理者 không có 部署). Còn lại: `users.department_id` chưa hiển thị ở đâu.
- **Template còn viết cứng hoặc dùng nhãn tiếng Nhật làm value.** Cần sửa khi chuyển service sang DB:
  - Đã sửa cho 市民: マイページ (tên, số liệu, 最近の報告), form 報告する.
  - Đã sửa cho 職員: thẻ AI分析, ô chọn trạng thái (`next_statuses()`), nhãn AI判定担当部署 (gợi ý AI mới nhất), bộ lọc bản đồ (`categories.id`, mã `Urgency`), tên カテゴリ ở 統計 và dashboard (lấy từ master).
  - Danh sách đầy đủ ở mục 9 của [docs/db/db_design.md](docs/db/db_design.md).
