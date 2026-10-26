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

# 4. Thêm dữ liệu master (部署, カテゴリ) và tài khoản test. Chạy lại nhiều lần cũng không bị trùng
python -m scripts.seed

# 5. Chạy server: http://localhost:5000
python run.py
```

**Đổi schema.** Sửa model trong `app/models/`, rồi chạy các lệnh dưới đây. Luôn đọc file migration mới sinh trong `migrations/versions/` trước khi `upgrade`, và commit file đó cùng với model.

```bash
flask --app run db migrate -m "mô tả thay đổi"
flask --app run db upgrade
```

**DB đã tạo bằng `db.create_all()` trước khi có Flask-Migrate.** Chạy `flask --app run db stamp 8d99a80ec994` một lần, sau đó mới `db upgrade`. Revision `8d99a80ec994` chính là bảng `users` cũ.

Tài khoản test do `scripts/seed.py` tạo, chỉ dùng khi phát triển:

| Vai trò | ID | Mật khẩu |
|---|---|---|
| 市民 | taro@example.com | password123 |
| 職員 | staff01 | password123 |
| 管理者 | admin01 | password123 |

Công nghệ: Flask 3, Flask-SQLAlchemy, Flask-Migrate (Alembic), Flask-Login, PostgreSQL (psycopg 3), Bootstrap 5, Chart.js.

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
├── scripts/
│   └── seed.py                  ✅ dữ liệu master (部署, カテゴリ) và tài khoản test
├── tests/                       ⬜ pytest
│   ├── conftest.py
│   ├── test_auth.py
│   ├── test_report_service.py
│   ├── test_status_service.py
│   └── test_ai_classifier.py
└── app/
    ├── __init__.py              ✅ create_app(): config, extensions, đăng ký blueprint
    ├── extensions.py            ✅ db, login_manager, migrate
    ├── constants.py             ✅ Role, ReportStatus, Urgency, AiAnalysisStatus, DuplicateGroupStatus, luồng trạng thái, nhãn tiếng Nhật
    ├── models/
    │   ├── __init__.py          ✅ import mọi model
    │   ├── user.py              ✅ User + user_loader
    │   ├── department.py        ✅ 担当部署
    │   ├── category.py          ✅ カテゴリ
    │   ├── report.py            ✅ Report, ReportPhoto
    │   ├── status_history.py    ✅ 対応履歴
    │   ├── ai_analysis.py       ✅ kết quả AI
    │   └── duplicate_group.py   ✅ nhóm báo cáo nghi trùng
    ├── services/                ⬜ nghiệp vụ (xem mục 4)
    ├── ai/                      ⬜ bộ phân loại AI (xem mục 4)
    ├── forms/                   ⬜ Flask-WTF forms
    ├── utils/
    │   ├── decorators.py        ✅ role_required
    │   ├── validators.py        ⬜ quy tắc mật khẩu, ID
    │   └── geo.py               ⬜ tính khoảng cách
    ├── blueprints/
    │   ├── public/              ✅ /, /guide, /faq
    │   ├── auth/                ✅ đăng nhập, đăng xuất, đăng ký, đặt lại mật khẩu
    │   ├── citizen/             ✅ /citizen/...
    │   ├── staff/               ✅ /staff/...
    │   ├── admin/               ✅ /admin/...
    │   └── api/                 ⬜ JSON cho bản đồ, biểu đồ (chưa đăng ký trong create_app)
    ├── templates/
    │   ├── layouts/             base.html ✅, public/citizen/staff/admin.html ⬜
    │   ├── components/          _flash.html ✅, badge/pagination/card ⬜
    │   ├── public/ auth/ citizen/ staff/ admin/   template theo từng blueprint
    │   └── errors/              ⬜ 403, 404, 500
    └── static/
        ├── css/                 tokens.css ⬜, base.css ✅, components.css ⬜
        ├── js/                  report_form.js, map.js, charts.js ⬜
        └── images/              ✅
```

---

## 4. Nhiệm vụ của từng thư mục và file

### Gốc dự án
- **run.py**: chỉ gọi `create_app()` và chạy server. Không viết route ở đây.
- **config.py**: lớp `Config` đọc `.env`. Dự định tách thêm `DevelopmentConfig`, `TestingConfig`, `ProductionConfig`, và thêm các key `AI_PROVIDER`, `AI_API_KEY`, `UPLOAD_FOLDER`, `MAX_CONTENT_LENGTH`, `DUPLICATE_RADIUS_M`.
- **instance/uploads/**: nơi lưu ảnh. Ảnh **không** đặt trong `static/` vì có thể chứa thông tin cá nhân. Chỉ người báo cáo và nhân viên được xem, thông qua một route có kiểm tra quyền.
- **scripts/**: script chạy tay như seed hay import dữ liệu. Chạy bằng `python -m scripts.<tên>` từ thư mục task-app.
- **tests/**: `conftest.py` tạo app với `TestingConfig` và DB riêng. Mỗi service có một file test.

### app/ lõi
- **app/\_\_init\_\_.py**: `create_app()`. Khi thêm blueprint mới, hãy import và `register_blueprint` ở đây. Error handler 403/404/500 và filter Jinja cũng đăng ký ở đây.
- **extensions.py**: khởi tạo mọi extension ở một chỗ để tránh import vòng. Đã có `migrate = Migrate()`. Sau này sẽ thêm `csrf = CSRFProtect()`.
- **constants.py**: định nghĩa trạng thái, độ khẩn cấp và vai trò bằng Enum. Mã lưu trong DB là tiếng Anh, nhãn hiển thị là tiếng Nhật. Template và service đều lấy nhãn từ đây, không viết tay.
  - Lấy nhãn: `ReportStatus.label_of(code)`. Lấy trạng thái kế tiếp hợp lệ: `next_statuses(current, role)`.
  - `CHECK` của model lấy danh sách giá trị qua `XXX.check_sql('cột')`. Khi thêm giá trị vào Enum, phải tạo migration để cập nhật `CHECK`.

### app/models/
Mỗi file là một nhóm bảng. Khi thêm model mới, phải import nó trong `models/__init__.py`, rồi tạo migration. Thiết kế chi tiết gồm cột, kiểu, ràng buộc, index và lý do của từng cột nằm trong [docs/db/db_design.md](docs/db/db_design.md).

| Bảng | Nội dung chính |
|---|---|
| `users` | Đã có. Thêm `icon_path` cho アイコン設定. `department_id` cho nhân viên để sau, khi có màn hình dùng tới |
| `departments` | 担当部署. Tạm thời có 道路管理課, 道路照明課, 公園緑地課, 環境課, 市民生活課 |
| `categories` | `code` (khoá cố định cho AI), `name` (道路, 街灯, 公園, 不法投棄, その他), `default_department_id` |
| `reports` | `citizen_id`, `title` (件名), `description`, `latitude`, `longitude`, `address`, `status`, `category_id`, `urgency`, `department_id`, `duplicate_group_id`, `duplicate_of_id`, thời gian. `category_id`, `urgency`, `department_id` là giá trị đã chốt, NULL nghĩa là chưa phân loại |
| `report_photos` | `report_id`, `file_path` (tên ngẫu nhiên), `content_type` |
| `report_status_histories` | `from_status` (NULL là mới tiếp nhận), `to_status`, `changed_by_id` (NULL là hệ thống), `comment` (ghi chú nội bộ), `public_message` (lời nhắn gửi người dân), thời điểm. Đây là 対応履歴, chỉ thêm dòng |
| `ai_analyses` | Mỗi lần AI phân tích: `status` (succeeded/failed), category, urgency, department, `confidence` (0 đến 1), `raw_response` (JSONB), `model_name`, `error_message` |
| `duplicate_groups` | Nhóm báo cáo nghi trùng: `status` (open/merged/dismissed), `similarity`, người và thời điểm xử lý |

Không lưu `priority`. 対応優先度 tính từ `urgency` và trạng thái chưa xong. Mọi báo cáo đều do người dân gửi (`citizen_id NOT NULL`).

Kết quả AI được lưu ở bảng riêng, không ghi đè lên `reports`. Nhờ vậy nhân viên có thể sửa phân loại mà vẫn giữ kết quả AI gốc. Dữ liệu này cũng dùng để đo độ chính xác của AI so với quyết định cuối cùng của nhân viên. Lần phân tích mới nhất là dòng có `created_at` lớn nhất, nếu bằng nhau thì lấy `id` lớn nhất (`Report.latest_ai_analysis`).

**Nhóm trùng.** Một báo cáo thuộc tối đa một nhóm qua `duplicate_group_id`. Khi 統合, các báo cáo còn lại nhận `duplicate_of_id` trỏ về 代表案件, và nhóm chuyển sang `merged`.

### Luồng trạng thái

```
received (未対応) -> in_progress (対応中) -> resolved (対応済み)
received (未対応) -> cancelled (取り消し)    # chỉ người dân, chỉ khi chưa xử lý
```

Chỉ `status_service.py` được đổi trạng thái. Mỗi lần đổi phải ghi một dòng vào `report_status_histories`.

### app/services/
| File | Nhiệm vụ |
|---|---|
| `auth_service.py` | Đăng nhập, đổi mật khẩu khi đã đăng nhập, tạo và kiểm tra token reset mật khẩu |
| `user_service.py` | Đăng ký người dân, admin tạo, khóa và mở khóa tài khoản nhân viên |
| `report_service.py` | Tạo báo cáo theo thứ tự lưu ảnh, gọi AI, kiểm tra trùng. Danh sách theo người dân. Tìm kiếm, lọc, phân trang cho nhân viên |
| `status_service.py` | Nơi duy nhất đổi trạng thái: kiểm tra luồng hợp lệ, ghi lịch sử, xử lý 取り消し |
| `assignment_service.py` | Gán phòng ban theo gợi ý AI hoặc do nhân viên chọn |
| `duplicate_service.py` | Tìm báo cáo cùng category, gần nhau, chưa xong, gửi gần đây. Chỉ đánh dấu ứng viên trùng, nhân viên xác nhận |
| `storage_service.py` | Kiểm tra đuôi và dung lượng ảnh, đặt tên ngẫu nhiên, lưu file |
| `stats_service.py` | Đếm theo trạng thái, category, urgency, khu vực cho dashboard, bản đồ, thống kê |

### app/ai/
| File | Nhiệm vụ |
|---|---|
| `base.py` | Interface `classify(text, photos) -> ClassificationResult` |
| `rule_based.py` | Phân loại theo từ khóa như 穴, 街灯, 遊具, ゴミ. Dùng khi dev, khi test, và làm dự phòng khi API lỗi |
| `llm_classifier.py` | Gọi LLM có hỗ trợ ảnh, chuyển JSON trả về thành `ClassificationResult` |
| `prompts.py` | Prompt và danh sách category, phòng ban hợp lệ để AI chọn |
| `__init__.py` | `get_classifier()` đọc `AI_PROVIDER` và trả về bộ phân loại phù hợp |

Gọi AI ngay sau khi lưu báo cáo, bọc trong try. Nếu AI lỗi, báo cáo vẫn được lưu và có thể phân tích lại sau. Khi cần chạy nền bằng RQ hoặc Celery, chỉ sửa `report_service.py`.

### app/forms/, app/utils/
- **forms/**: định nghĩa form Flask-WTF, gồm quy tắc validate và CSRF. Ba màn hình reset mật khẩu sẽ dùng chung form.
- **utils/decorators.py**: `role_required(User.ROLE_xxx)` bảo vệ route theo vai trò.
- **utils/validators.py**: quy tắc dùng ở nhiều nơi, như mật khẩu tối thiểu 8 ký tự hay ID nhân viên và admin không chứa `@`.
- **utils/geo.py**: tính khoảng cách Haversine giữa hai tọa độ.

### app/blueprints/
Mỗi actor trong sơ đồ use case là một blueprint. Mỗi blueprint gồm `__init__.py` để re-export biến blueprint, và `routes.py` để khai báo blueprint và route.

**Quy ước:**
- Endpoint luôn có tiền tố blueprint, ví dụ `url_for('citizen.mypage')`, không viết `url_for('mypage')`.
- Template nằm trong thư mục cùng tên blueprint, ví dụ `citizen/report_new.html`.
- Route cần đăng nhập phải có `@role_required(User.ROLE_xxx)`.

**Bảng use case.** ✅ là đã có route, ⬜ là dự kiến.

| Actor | Use case | URL | Endpoint | Service |
|---|---|---|---|---|
| 市民 | アカウント作成 | `/register` | ✅ `auth.register` | user_service |
| Chung | ログイン, ログアウト | `/login/<role>`, `/logout` | ✅ `auth.login_*`, `auth.logout` | auth_service |
| Chung | パスワード再設定 | `/reset-password`, `/login/staff/reset-password`, `/login/admin/reset-password` | ✅ `auth.reset_password*` | auth_service |
| 市民 | マイページ | `/citizen/mypage` | ✅ `citizen.mypage` | report_service |
| 市民 | 報告する | `/citizen/report` | ✅ `citizen.report_new` | report_service |
| 市民 | 報告完了画面 | `/citizen/report/complete` (sau này `/citizen/reports/<id>/complete`) | ✅ `citizen.report_complete` | report_service |
| 市民 | 履歴を表示する | `/citizen/reports` | ✅ `citizen.report_list` | report_service |
| 市民 | 対応状況を表示する | `/citizen/reports/<id>` | ⬜ | report_service |
| 市民 | 報告を取り消す | `POST /citizen/reports/<id>/cancel` | ⬜ | status_service |
| 市民 | パスワード変更 | `/citizen/password` | ⬜ | auth_service |
| 市民 | プロフィール設定 | `/citizen/profile` | ✅ `citizen.profile` | user_service |
| 市民 | アイコン設定 | `/citizen/profile/icon` | ✅ `citizen.profile_icon` | user_service, storage_service |
| 職員 | ダッシュボード | `/staff/dashboard` | ✅ `staff.dashboard` | stats_service |
| 職員 | 報告一覧 | `/staff/reports` | ✅ `staff.report_list` | report_service |
| 職員 | 報告内容, 対応状況, 対応履歴 | `/staff/reports/<id>` | ✅ `staff.report_detail` | report_service, status_service |
| 職員 | 対応状況を更新する | `POST /staff/reports/<id>/status` | ✅ `staff.report_status_update` | status_service |
| 職員 | 担当部署を設定する | `POST /staff/reports/<id>/department` | ⬜ | assignment_service |
| 職員 | AI 分類, 緊急度判定 | tự động khi tạo, `POST /staff/reports/<id>/reanalyze` | ⬜ | ai |
| 職員 | AI分析・自動判定ログ | `/staff/ai-analysis` | ✅ `staff.ai_analysis` | report_service |
| 職員 | 地図表示 | `/staff/map` ✅, `/api/reports/map` ⬜ | ✅ `staff.report_map` | stats_service |
| 職員 | 対応管理・重複確認 | `/staff/duplicates` | ✅ `staff.duplicates` | duplicate_service |
| 職員 | 代表案件に統合する | `/staff/duplicates/<group_id>/merge` | ✅ `staff.duplicate_merge` | duplicate_service |
| 職員 | 一括対応ステータス変更 | `/staff/duplicates/<group_id>/status` | ✅ `staff.duplicate_batch_status` | status_service |
| 職員 | 統計 | `/staff/stats` ✅, `/api/stats` ⬜ | ✅ `staff.stats` | stats_service |
| 管理者 | ポータル | `/admin/portal` | ✅ `admin.portal` | |
| 管理者 | ユーザー一覧 | `/admin/users` | ⬜ | user_service |
| 管理者 | 新規職員アカウント登録 | `/admin/users/register`, `/admin/users/register/complete` | ✅ `admin.user_register`, `admin.user_register_complete` | user_service |
| 管理者 | システム管理 | `/admin/departments`, `/admin/categories` | ⬜ | user_service |

### app/templates/, app/static/
- **layouts/base.html**: khung HTML chung, nạp Bootstrap, Bootstrap Icons, Chart.js và các file CSS. Các layout theo vai trò sẽ kế thừa base và chứa navbar, sidebar. Trang con chỉ viết phần nội dung.
- **components/**: các mảnh dùng lại bằng `{% include %}`. Màu badge trạng thái và độ khẩn cấp chỉ định nghĩa ở đây.
- **staff/mockup_report_triage.html**: mockup giao diện màn hình phân loại báo cáo, chưa có route. Dùng làm tham khảo khi làm `staff/report_list.html` và `staff/report_detail.html`, làm xong thì xóa.
- **static/css/**: `tokens.css` chứa biến màu và khoảng cách, `base.css` chứa style chung, `components.css` chứa style cho từng thành phần.
- **static/js/**: `report_form.js` lấy GPS và hiện ảnh xem trước, `map.js` vẽ bản đồ Leaflet với ghim đỏ cho khẩn cấp, vàng cho đang xử lý, xanh cho đã xong, `charts.js` vẽ biểu đồ Chart.js từ API.

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

---

## 7. Lộ trình phát triển

1. ✅ Tách app thành package, dùng `create_app()` và blueprint theo actor.
2. ✅ Thêm **Flask-Migrate**. Đã khai báo trong `extensions.py` và có thư mục `migrations/`. Seed không còn gọi `create_all`.
3. ⬜ Thêm **Flask-WTF** để có CSRF cho mọi form.
4. ⬜ Viết lại **đặt lại mật khẩu**: người dân nhận token qua email, nhân viên và admin được admin cấp lại.
5. ✅ Viết `constants.py` và các model phòng ban, danh mục, báo cáo, ảnh, lịch sử, kết quả AI, nhóm trùng, kèm migration và dữ liệu seed. Các service vẫn trả dữ liệu mẫu, sẽ chuyển sang DB ở các bước 6 và 7.
6. ⬜ Làm luồng báo cáo của người dân với `rule_based.py`.
7. ⬜ Làm danh sách, chi tiết, cập nhật trạng thái và lịch sử cho nhân viên. Thay số liệu hard-code trên dashboard bằng dữ liệu thật.
8. ⬜ Thêm blueprint `api`, bản đồ Leaflet và biểu đồ.
9. ⬜ Thêm `llm_classifier.py` và `duplicate_service.py`.
10. ⬜ Làm quản lý người dùng và dữ liệu master cho admin.
11. ⬜ Thêm layout theo vai trò, component và trang lỗi để bỏ phần lặp trong template.

## 8. Vấn đề đã biết

- **Đặt lại mật khẩu chưa thực sự đổi mật khẩu.** Ba route reset chỉ kiểm tra form rồi chuyển trang. Không được lưu mật khẩu theo form hiện tại, vì ai biết email người khác cũng sẽ đổi được mật khẩu của họ. Xem bước 4 của lộ trình.
- **Chưa có CSRF.** Xem bước 3.
- **Form báo cáo chưa lưu dữ liệu.** Form đã có đủ ô nhập ứng với bảng `reports` và `report_photos`: `title`, `photos`, `address`, `latitude`, `longitude` (ẩn), `description`, kèm `enctype="multipart/form-data"`. Còn thiếu hai việc:
  - Route `citizen.report_new` vẫn chỉ chuyển sang trang hoàn tất, chưa gọi `report_service`.
  - `latitude` và `longitude` chưa được điền vì `report_form.js` (lấy GPS) chưa viết. Cột trong DB là `NOT NULL`, nên phải làm phần lấy vị trí trước khi lưu.
- **Dashboard nhân viên** đang hiển thị số liệu và biểu đồ hard-code.
- **Các màn hình nhân viên** 報告一覧, 報告詳細, 地図表示, AI分析・自動判定, 対応管理・重複確認, 案件統合, 一括ステータス変更, 統計 lấy dữ liệu mẫu từ `report_service`, `stats_service`, `duplicate_service`, chưa đọc DB. Tìm kiếm, lọc theo ID và lọc bản đồ đang lọc trên dữ liệu mẫu trong service. Thẻ tổng hợp trên màn hình AI分析 vẫn hard-code. Nút AIモデル再実行 chưa hoạt động.
- **Các thao tác cập nhật của nhân viên chưa lưu gì.** Cập nhật trạng thái ở 報告詳細, 統合 và 一括ステータス変更 chỉ `flash` rồi quay lại trang. Khi có model, trạng thái phải đổi qua `status_service`, còn 統合 đặt `duplicate_of_id` qua `duplicate_service`. Cột ghi chú ở 報告詳細 chưa được dùng.
- **Trạng thái 保留 và 対象外 trong bản thiết kế chưa có trong luồng trạng thái.** Bản thiết kế có thêm 保留・他部署へ引継ぎ, 保留・経過観察, 対象外・対応不要. Màn hình hiện chỉ dùng 未対応, 対応中, 対応済み theo mục 4. Nếu cần các trạng thái này, phải sửa luồng trạng thái trong README trước.
- **Cờ `ai_detected` trong dữ liệu mẫu của `duplicate_service`** quyết định nút AI確認 hay 個別確認. Đã chốt như sau:
  - Không có báo cáo do AI tạo. Người báo cáo luôn là người dân.
  - Khi chuyển service sang DB, `ai_detected` là giá trị tính được: báo cáo có `ai_analyses` thành công thì `ai_detected` đúng.
  - Dữ liệu mẫu có người báo cáo "AIシステム" sẽ bỏ khi chuyển.
- **レポート出力 chỉ là giao diện.** Modal chọn PDF/CSV chỉ hiện thông báo hoàn tất trên trình duyệt, chưa tạo file.
- **Bản đồ** đang là ảnh tĩnh `static/images/map.avif` với ghim đặt theo `top`/`left` (%). Sẽ thay bằng Leaflet (`map.js`) dùng vĩ độ, kinh độ.
- **Danh sách báo cáo của người dân** lấy dữ liệu mẫu từ `report_service.list_by_citizen()`, chưa đọc DB. Khi có model `Report` và `constants.py`, chỉ cần sửa hàm này, route không đổi.
- **Màn hình プロフィール設定 chưa lưu gì.** Nút 変更を保存する chỉ hiện `alert` mẫu, nút パスワードを変更する đang trỏ tới trang reset công khai `auth.reset_password` (sẽ đổi sang `/citizen/password`). Màn hình アイコン設定 chưa lưu ảnh: ô file chưa có `name`, form thiếu `enctype`. Cột `users.icon_path` đã có.
- **Màn hình 新規職員アカウント登録 chưa lưu gì.** Route `admin.user_register` chỉ chuyển sang trang hoàn tất. Khi làm `user_service` cần:
  - Kiểm tra ID không chứa `@`, mật khẩu tối thiểu 8 ký tự, mật khẩu xác nhận khớp, ID chưa tồn tại.
  - Thêm cột `users.department_id` (kèm migration) để lưu 所属部署.
  - Ô 所属部署 đang viết cứng tên phòng ban theo bản thiết kế (道路維持課, 都市公園課…), khác với master `departments`. Nên lấy từ bảng `departments` và dùng `id` làm value.
  - Danh sách tài khoản ở ポータル vẫn viết cứng.
- **Template còn viết cứng hoặc dùng nhãn tiếng Nhật làm value.** Cần sửa khi chuyển service sang DB:
  - マイページ viết cứng tên "山田 太郎", số liệu và danh sách 最近の報告.
  - Thẻ tổng hợp ở AI分析 viết cứng.
  - Ô chọn trạng thái ở 報告詳細 cho chọn 未対応. Nên chỉ hiện `next_statuses()`.
  - Nhãn "AI判定担当部署" ở 報告詳細 đang hiện phòng ban đã chốt, không phải gợi ý của AI.
  - Bộ lọc bản đồ thiếu 不法投棄 và dùng nhãn làm value. Nên dùng `categories.id` và mã `Urgency`.
  - Tên カテゴリ ở 統計 và dashboard khác với master.
  - Danh sách đầy đủ ở mục 9 của [docs/db/db_design.md](docs/db/db_design.md).
