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

**Chạy test.** Test dùng một DB PostgreSQL riêng, mỗi test tạo rồi xoá toàn bộ bảng. Không bao giờ trỏ vào DB dev (conftest sẽ dừng nếu `TEST_DATABASE_URL` trùng `DATABASE_URL`). Chưa đặt `TEST_DATABASE_URL` thì các test dùng DB bị bỏ qua.

```bash
# Tạo DB test một lần bằng tài khoản có quyền CREATEDB (ví dụ postgres)
psql -U postgres -c "CREATE DATABASE saitama_help_test OWNER saitama_app"
# Thêm vào .env: TEST_DATABASE_URL=postgresql+psycopg://saitama_app:<mật khẩu>@localhost:5432/saitama_help_test
python -m pytest
```

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
│   └── seed.py                  ✅ dữ liệu master (部署, カテゴリ) và tài khoản test
├── tests/                       pytest (cần TEST_DATABASE_URL, xem mục 1)
│   ├── conftest.py              ✅ app, DB test, user/カテゴリ/報告 mẫu
│   ├── test_auth.py             ⬜
│   ├── test_report_service.py   ✅ 職員 (一覧, 詳細, AI分析), 市民 (報告する, 写真撮影, 位置情報, マイページ)
│   ├── test_user_service.py     ✅ プロフィール設定, アイコン
│   ├── test_status_service.py   ✅ cập nhật trạng thái, 一括変更
│   ├── test_duplicate_service.py ✅ 重複確認, 統合
│   ├── test_stats_service.py    ✅ dashboard, 地図, 統計
│   └── test_ai_classifier.py    ✅ rule_based, Gemini (client giả, không gọi API thật)
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
    ├── services/                nghiệp vụ (xem mục 4). ✅ phần 職員 và 市民 của report/status/duplicate/stats/storage/user. ⬜ auth_service, assignment_service, đăng ký và quản lý tài khoản trong user_service
    ├── ai/                      ✅ AI: Gemini và phân loại theo từ khóa (xem mục 4)
    ├── forms/                   ⬜ Flask-WTF forms
    ├── fonts/                   ✅ font IPAexゴシック cho PDF (kèm giấy phép IPA). Không đặt trong static để không public
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
        ├── css/                 tokens.css ⬜, base.css ✅, components.css ⬜, responsive.css ✅ (スマホ表示)
        ├── js/                  report_form.js ✅, map.js ✅ (Google Maps), charts.js ⬜
        └── images/              ✅
```

---

## 4. Nhiệm vụ của từng thư mục và file

### Gốc dự án
- **run.py**: chỉ gọi `create_app()` và chạy server. Không viết route ở đây.
- **config.py**: lớp `Config` đọc `.env`, `TestingConfig` cho pytest (đọc `TEST_DATABASE_URL`). Đã có `AI_LOW_CONFIDENCE_THRESHOLD` (mặc định 0.7, ngưỡng 要人間チェック ở AI分析), `GOOGLE_MAPS_API_KEY` và `GOOGLE_MAPS_MAP_ID` (bản đồ 地図表示). Dự định tách thêm `DevelopmentConfig`, `ProductionConfig`, và thêm các key `AI_PROVIDER`, `AI_API_KEY`, `UPLOAD_FOLDER`, `MAX_CONTENT_LENGTH`, `DUPLICATE_RADIUS_M`.
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
| `users` | Đã có. Thêm `icon_path` cho アイコン設定, `postal_code` (dạng `123-4567`) và `address` cho アカウント作成 (chỉ người dân, NULL với nhân viên và admin). `department_id` cho nhân viên để sau, khi có màn hình dùng tới |
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
received (未対応) -> resolved (対応済み)     # chỉ nhân viên
received (未対応) -> cancelled (取り消し)    # chỉ người dân, chỉ khi chưa xử lý
```

Chỉ `status_service.py` được đổi trạng thái. Mỗi lần đổi phải ghi một dòng vào `report_status_histories`.

**Quy tắc đã cài cho 職員** (theo `docs/ユースケース図_行政職員 4.xlsx`):
- 詳細・対応更新: bắt buộc nhập 担当者メモ・進捗コメント (2a-1). Ô chọn chỉ có trạng thái hiện tại và trạng thái kế tiếp hợp lệ. Chọn lại trạng thái hiện tại nghĩa là chỉ thêm ghi chú, vẫn ghi một dòng lịch sử với `from_status = to_status`.
- 一括対応ステータス変更: bỏ qua báo cáo đã 取り消し và báo cáo đã ở trạng thái đích. Nếu còn báo cáo nào không chuyển được (ví dụ 対応済み → 対応中) thì không đổi báo cáo nào và báo lỗi kèm ID.
- 代表案件に統合: chỉ nhóm `open`. Đặt `duplicate_of_id` cho các báo cáo còn lại, nhóm chuyển `merged`, không đổi trạng thái nên không ghi lịch sử. Xong thì quay về 重複確認.
- Câu thông báo lấy nguyên văn từ Excel, đặt trong `constants.py` (`MSG_*`). Lỗi nghiệp vụ dùng `ServiceError` (`app/services/__init__.py`), route bắt rồi `flash`.
- Thống kê và dashboard không tính báo cáo 取り消し. Bản đồ chỉ hiện báo cáo chưa xong (未対応, 対応中).
- 地図表示 dùng **Google Maps JavaScript API**. Điền `GOOGLE_MAPS_API_KEY` vào `.env` là chạy. Ghim (`AdvancedMarkerElement`) đặt tại 発生場所 (`address`) mà người dân nhập: `map.js` tìm tọa độ của địa chỉ bằng API tìm địa chỉ của 国土地理院 (`msearch.gsi.go.jp/address-search/AddressSearch`, miễn phí, không cần key, ghi 「出典：国土地理院」 dưới bản đồ), nếu không tìm được thì dùng `latitude`/`longitude` của báo cáo. API này là API nội bộ của 地理院地図, có thể đổi hoặc dừng không báo trước, khi đó ghim tự quay về `latitude`/`longitude`. Màu ghim theo 緊急度 (đỏ 高, vàng 中, xanh 低 hoặc chưa phân loại). Bấm ghim hiện 件名, 住所, 危険度 và link 詳細を見る. `AdvancedMarkerElement` cần map ID (`GOOGLE_MAPS_MAP_ID`, mặc định `DEMO_MAP_ID` chỉ để dev). Không có key thì hiện ảnh tĩnh `static/images/map.avif` như trước. Key được gửi xuống trình duyệt nên phải giới hạn HTTP referrer và chỉ bật Maps JavaScript API trong Google Cloud.
- レポート出力: `GET /staff/stats/export/<pdf|csv>` (`staff.stats_export`) trả file tải về, nội dung giống màn hình 統計 (サマリー, カテゴリ別, 月別). Nút 確定 trong modal tải file bằng `fetch`, xong mới hiện 「PDF形式での出力が完了しました。」/「CSV形式での出力が完了しました。」. CSV là UTF-8 có BOM để mở bằng Excel không lỗi font. PDF tạo bằng reportlab, nhúng font IPAexゴシック (`app/fonts/ipaexg.ttf`, giấy phép IPA kèm cùng thư mục, chỉ nhúng các ký tự đã dùng).

### app/services/
| File | Nhiệm vụ |
|---|---|
| `auth_service.py` | Đăng nhập, đổi mật khẩu khi đã đăng nhập, tạo và kiểm tra token reset mật khẩu |
| `user_service.py` | Đăng ký người dân, admin tạo, khóa và mở khóa tài khoản nhân viên. ✅ Cập nhật プロフィール (tên, email) và アイコン của người dân |
| `report_service.py` | Tạo báo cáo theo thứ tự lưu ảnh, gọi AI, kiểm tra trùng. Danh sách theo người dân. Tìm kiếm, lọc, phân trang cho nhân viên |
| `status_service.py` | Nơi duy nhất đổi trạng thái: kiểm tra luồng hợp lệ, ghi lịch sử, xử lý 取り消し |
| `assignment_service.py` | Gán phòng ban theo gợi ý AI hoặc do nhân viên chọn |
| `duplicate_service.py` | Tìm báo cáo cùng category, gần nhau, chưa xong, gửi gần đây. Chỉ đánh dấu ứng viên trùng, nhân viên xác nhận |
| `storage_service.py` | ✅ Kiểm tra định dạng thật (Pillow: JPEG, PNG, WebP) và dung lượng (10MB một ảnh), đặt tên ngẫu nhiên, lưu vào `UPLOAD_FOLDER` (`reports/`, `drafts/`, `icons/`), đọc GPS từ EXIF |
| `stats_service.py` | Đếm theo trạng thái, category, urgency, khu vực cho dashboard, bản đồ, thống kê |

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
| 市民 | 報告する | `/citizen/report` | ✅ `citizen.report_new` | report_service, ai |
| 市民 | 写真撮影 | `/citizen/report/camera` | ✅ `citizen.report_camera` | report_service, storage_service, ai |
| 市民 | 撮影写真の確認 | `/citizen/report/photo`, ảnh: `/citizen/report/photo/image` | ✅ `citizen.report_photo`, `citizen.report_photo_image` | storage_service |
| 市民 | 位置情報の確認 | `/citizen/report/location` | ✅ `citizen.report_location` | report_service, ai |
| 市民 | 報告完了画面 | `/citizen/reports/<id>/complete` | ✅ `citizen.report_complete` | report_service |
| 市民 | 履歴を表示する | `/citizen/reports` | ✅ `citizen.report_list` | report_service |
| 市民 | 対応状況を表示する | `/citizen/reports/<id>` | ⬜ | report_service |
| 市民 | 報告を取り消す | `POST /citizen/reports/<id>/cancel` | ⬜ | status_service |
| 市民 | パスワード変更 | `/citizen/password` | ⬜ | auth_service |
| 市民 | プロフィール設定 | `/citizen/profile` | ✅ `citizen.profile` | user_service |
| 市民 | アイコン設定 | `/citizen/profile/icon`, ảnh: `/citizen/profile/icon/image` | ✅ `citizen.profile_icon`, `citizen.profile_icon_image` | user_service, storage_service |
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
| 職員 | レポート出力 (PDF/CSV) | `/staff/stats/export/<fmt>` | ✅ `staff.stats_export` | stats_service |
| 管理者 | ポータル | `/admin/portal` | ✅ `admin.portal` | |
| 管理者 | ユーザー一覧 | `/admin/users` | ⬜ | user_service |
| 管理者 | 新規職員アカウント登録 | `/admin/users/register`, `/admin/users/register/complete` | ✅ `admin.user_register`, `admin.user_register_complete` | user_service |
| 管理者 | システム管理 | `/admin/departments`, `/admin/categories` | ⬜ | user_service |

### app/templates/, app/static/
- **layouts/base.html**: khung HTML chung, nạp Bootstrap, Bootstrap Icons, Chart.js và các file CSS. Các layout theo vai trò sẽ kế thừa base và chứa navbar, sidebar. Trang con chỉ viết phần nội dung.
- **components/**: các mảnh dùng lại bằng `{% include %}`. Màu badge trạng thái và độ khẩn cấp chỉ định nghĩa ở đây.
- **staff/mockup_report_triage.html**: mockup giao diện màn hình phân loại báo cáo, chưa có route. Dùng làm tham khảo khi làm `staff/report_list.html` và `staff/report_detail.html`, làm xong thì xóa.
- **static/css/**: `tokens.css` chứa biến màu và khoảng cách, `base.css` chứa style chung, `components.css` chứa style cho từng thành phần, `responsive.css` chứa style cho điện thoại (nạp cuối cùng trong `base.html`, dùng các class như `citizen-page`, `report-page`, `guide-page`).
- **static/js/**: `report_form.js` lấy GPS và hiện ảnh xem trước (ở 写真撮影, chỉ lấy GPS sau khi người dân chọn 今回だけ使用 hoặc オンにする trong modal 位置情報確認, キャンセル thì quay về 報告する), `map.js` vẽ Google Maps với ghim theo 緊急度 (đỏ 高, vàng 中, xanh 低), dữ liệu lấy từ `<script id="mapReports">` trong `staff/map.html`, `charts.js` vẽ biểu đồ Chart.js từ API.

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
6. ✅ Làm luồng báo cáo của người dân (chụp ảnh, GPS, đoán địa chỉ, gửi báo cáo, AI phán định), マイページ, 報告一覧, プロフィール, アイコン. Có cả `rule_based.py` và Gemini.
7. ✅ Làm danh sách, chi tiết, cập nhật trạng thái và lịch sử cho nhân viên. Thay số liệu hard-code trên dashboard bằng dữ liệu thật. Kèm AI分析 (chỉ đọc kết quả), 重複確認, 統合, 一括変更, 統計. Còn lại: xem mục 8.
8. Bản đồ Google Maps ✅. ⬜ Thêm blueprint `api` và biểu đồ.
9. Thêm `llm_classifier.py` ✅ (Gemini) và phần phát hiện trùng của `duplicate_service.py` ⬜.
10. ⬜ Làm quản lý người dùng và dữ liệu master cho admin.
11. ⬜ Thêm layout theo vai trò, component và trang lỗi để bỏ phần lặp trong template.

## 8. Vấn đề đã biết

- **Đặt lại mật khẩu chưa thực sự đổi mật khẩu.** Ba route reset chỉ kiểm tra form rồi chuyển trang. Không được lưu mật khẩu theo form hiện tại, vì ai biết email người khác cũng sẽ đổi được mật khẩu của họ. Xem bước 4 của lộ trình.
- **Chưa có CSRF.** Xem bước 3.
- **Luồng báo cáo của người dân.** Ảnh chụp, tọa độ và địa chỉ đoán được giữ trong `session['report_draft']` cho đến khi gửi 報告する. Ảnh chụp được lưu tạm ở `instance/uploads/drafts/`, khi gửi thì chuyển sang `reports/`. Còn thiếu:
  - Camera và GPS của trình duyệt chỉ chạy trên **HTTPS** hoặc `localhost`. Nếu chạy HTTP thì trang 写真撮影 chuyển sang ô chọn ảnh, còn tọa độ chỉ lấy được từ EXIF của ảnh. Khi đó có thể không gửi được báo cáo, vì DB bắt buộc tọa độ. Server EC2 đã bật HTTPS tại `https://team3app.duckdns.org` (Nginx + Let's Encrypt/Certbot, tự gia hạn bằng `certbot.timer`, HTTP chuyển hướng 301 sang HTTPS). Nếu Learner Lab đổi Public IP thì phải cập nhật IP trên DuckDNS, nếu không trang web và việc gia hạn chứng chỉ sẽ lỗi. Người dùng từ chối quyền vị trí vẫn không gửi được báo cáo nếu ảnh không có GPS. Có thể làm thêm phần chọn điểm trên bản đồ (Google Maps).
  - Địa chỉ do Gemini đoán từ tọa độ có thể sai. Người dân sửa được ở 報告する. Nếu cần chính xác thì nên dùng API reverse geocoding (ví dụ của 国土地理院).
  - Ảnh tạm trong `drafts/` của người bỏ dở giữa chừng chưa được tự động xoá.
  - Chưa phát hiện nhóm trùng khi tạo báo cáo (`duplicate_service`).
  - Thông báo ở アカウント作成 và ログイン (`auth`) chưa giống hệt câu chữ trong Excel 県民. Màn hình パスワード変更 (đặt lại bằng email) chưa làm, xem bước 4.
- **`responsive.css` căn giữa `body` theo chiều dọc trên màn hình dưới 768px** (`display: flex` cho `body`). Quy tắc này áp dụng cho mọi màn hình, kể cả 職員 và 管理者.
- **Màn hình nhân viên đã đọc và ghi DB thật, nhưng còn thiếu:**
  - Nút AIモデル再実行 ở AI分析 chưa hoạt động (có thể gọi `report_service.analyze_report()`).
  - Chưa có bước phát hiện nhóm trùng khi tạo báo cáo (`duplicate_service`, `utils/geo.py`), nên 重複確認 chỉ hiện nhóm đã có trong `duplicate_groups`.
  - 添付写真 ở 報告詳細 chưa hiện vì chưa có `storage_service` và route trả ảnh có kiểm tra quyền.
  - 担当部署を設定する (`POST /staff/reports/<id>/department`) chưa có route.
  - Danh sách 報告一覧 và AI分析 chưa phân trang. AI分析 chỉ hiện 50 lần phân tích mới nhất.
- **Khi không có `GOOGLE_MAPS_API_KEY`, ghim bản đồ đặt theo phép chiếu gần đúng.** `stats_service.map_points()` vẫn trả `top`/`left` (%) trên ảnh tĩnh theo khung `MAP_BOUNDS` cho trường hợp này. Vị trí chỉ gần đúng.
- **Trạng thái 保留 và 対象外 trong bản thiết kế chưa có trong luồng trạng thái.** Bản thiết kế có thêm 保留・他部署へ引継ぎ, 保留・経過観察, 対象外・対応不要. Màn hình hiện chỉ dùng 未対応, 対応中, 対応済み theo mục 4. Nếu cần các trạng thái này, phải sửa luồng trạng thái trong README trước.
- **Cờ `ai_detected` trong dữ liệu mẫu của `duplicate_service`** quyết định nút AI確認 hay 個別確認. Đã chốt như sau:
  - Không có báo cáo do AI tạo. Người báo cáo luôn là người dân.
  - Khi chuyển service sang DB, `ai_detected` là giá trị tính được: báo cáo có `ai_analyses` thành công thì `ai_detected` đúng.
  - Dữ liệu mẫu có người báo cáo "AIシステム" sẽ bỏ khi chuyển.
- **Nút パスワードを変更する ở プロフィール設定** vẫn trỏ tới trang reset công khai `auth.reset_password` (sẽ đổi sang `/citizen/password`). お名前, メールアドレス và アイコン đã lưu DB.
- **Màn hình 新規職員アカウント登録 chưa lưu gì.** Route `admin.user_register` chỉ chuyển sang trang hoàn tất. Khi làm `user_service` cần:
  - Kiểm tra ID không chứa `@`, mật khẩu tối thiểu 8 ký tự, mật khẩu xác nhận khớp, ID chưa tồn tại.
  - Thêm cột `users.department_id` (kèm migration) để lưu 所属部署.
  - Ô 所属部署 đang viết cứng tên phòng ban theo bản thiết kế (道路維持課, 都市公園課…), khác với master `departments`. Nên lấy từ bảng `departments` và dùng `id` làm value.
  - Danh sách tài khoản ở ポータル vẫn viết cứng.
- **Template còn viết cứng hoặc dùng nhãn tiếng Nhật làm value.** Cần sửa khi chuyển service sang DB:
  - Đã sửa cho 市民: マイページ (tên, số liệu, 最近の報告), form 報告する.
  - Đã sửa cho 職員: thẻ AI分析, ô chọn trạng thái (`next_statuses()`), nhãn AI判定担当部署 (gợi ý AI mới nhất), bộ lọc bản đồ (`categories.id`, mã `Urgency`), tên カテゴリ ở 統計 và dashboard (lấy từ master).
  - Danh sách đầy đủ ở mục 9 của [docs/db/db_design.md](docs/db/db_design.md).
