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

# 3. Tạo bảng và tài khoản test (chạy ở thư mục task-app)
python -m scripts.seed

# 4. Chạy server: http://localhost:5000
python run.py
```

Tài khoản test do `scripts/seed.py` tạo, chỉ dùng khi phát triển:

| Vai trò | ID | Mật khẩu |
|---|---|---|
| 市民 | taro@example.com | password123 |
| 職員 | staff01 | password123 |
| 管理者 | admin01 | password123 |

Công nghệ: Flask 3, Flask-SQLAlchemy, Flask-Login, PostgreSQL (psycopg 3), Bootstrap 5, Chart.js.

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
├── scripts/
│   └── seed.py                  ✅ tạo bảng và tài khoản test
├── tests/                       ⬜ pytest
│   ├── conftest.py
│   ├── test_auth.py
│   ├── test_report_service.py
│   ├── test_status_service.py
│   └── test_ai_classifier.py
└── app/
    ├── __init__.py              ✅ create_app(): config, extensions, đăng ký blueprint
    ├── extensions.py            ✅ db, login_manager
    ├── constants.py             ⬜ Role, ReportStatus, Urgency, luồng trạng thái, nhãn tiếng Nhật
    ├── models/
    │   ├── __init__.py          ✅ import mọi model
    │   ├── user.py              ✅ User + user_loader
    │   ├── department.py        ⬜ 担当部署
    │   ├── category.py          ⬜ カテゴリ
    │   ├── report.py            ⬜ Report, ReportPhoto
    │   ├── status_history.py    ⬜ 対応履歴
    │   └── ai_analysis.py       ⬜ kết quả AI
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
- **extensions.py**: khởi tạo mọi extension ở một chỗ để tránh import vòng. Sau này sẽ thêm `migrate = Migrate()` và `csrf = CSRFProtect()`.
- **constants.py**: định nghĩa trạng thái, độ khẩn cấp và vai trò bằng Enum. Mã lưu trong DB là tiếng Anh, nhãn hiển thị là tiếng Nhật. Template và service đều lấy nhãn từ đây, không viết tay.

### app/models/
Mỗi file là một nhóm bảng. Khi thêm model mới, phải import nó trong `models/__init__.py`.

| Bảng | Nội dung chính |
|---|---|
| `users` | Đã có. Dự định thêm `department_id` cho nhân viên |
| `departments` | 担当部署 như 道路課, 公園課, 環境課 |
| `categories` | 道路, 街灯, 公園, 不法投棄, その他. Mỗi loại có phòng ban mặc định |
| `reports` | Nội dung, vĩ độ, kinh độ, địa chỉ, `status`, `urgency`, `priority`, `category_id`, `department_id`, `citizen_id`, `duplicate_of_id`, thời gian |
| `report_photos` | `report_id`, đường dẫn file, loại file |
| `report_status_histories` | Trạng thái cũ và mới, người đổi, ghi chú, thời điểm. Đây là 対応履歴. Ghi chú có thể dùng làm lời nhắn gửi người dân |
| `ai_analyses` | Mỗi lần AI phân tích: category, urgency, department, priority, độ tin cậy, phản hồi gốc, tên model |

Kết quả AI được lưu ở bảng riêng, không ghi đè lên `reports`. Nhờ vậy nhân viên có thể sửa phân loại mà vẫn giữ kết quả AI gốc. Dữ liệu này cũng dùng để đo độ chính xác của AI so với quyết định cuối cùng của nhân viên.

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
| 市民 | 報告完了画面 | `/citizen/reports/<id>/complete` | ⬜ | report_service |
| 市民 | 履歴を表示する | `/citizen/reports` | ✅ `citizen.report_list` | report_service |
| 市民 | 対応状況を表示する | `/citizen/reports/<id>` | ⬜ | report_service |
| 市民 | 報告を取り消す | `POST /citizen/reports/<id>/cancel` | ⬜ | status_service |
| 市民 | パスワード変更 | `/citizen/password` | ⬜ | auth_service |
| 市民 | プロフィール設定 | `/citizen/profile` | ✅ `citizen.profile` | user_service |
| 市民 | アイコン設定 | `/citizen/profile/icon` | ✅ `citizen.profile_icon` | user_service, storage_service |
| 職員 | ダッシュボード | `/staff/dashboard` | ✅ `staff.dashboard` | stats_service |
| 職員 | 報告一覧 | `/staff/reports` | ⬜ | report_service |
| 職員 | 報告内容, 対応状況, 対応履歴 | `/staff/reports/<id>` | ⬜ | report_service, status_service |
| 職員 | 対応状況を更新する | `POST /staff/reports/<id>/status` | ⬜ | status_service |
| 職員 | 担当部署を設定する | `POST /staff/reports/<id>/department` | ⬜ | assignment_service |
| 職員 | AI 分類, 緊急度判定 | tự động khi tạo, `POST /staff/reports/<id>/reanalyze` | ⬜ | ai |
| 職員 | 地図表示 | `/staff/map`, `/api/reports/map` | ⬜ | stats_service |
| 職員 | 重複確認 | `/staff/duplicates` | ⬜ | duplicate_service |
| 職員 | 統計 | `/staff/stats`, `/api/stats` | ⬜ | stats_service |
| 管理者 | ポータル | `/admin/portal` | ✅ `admin.portal` | |
| 管理者 | ユーザー一覧 | `/admin/users` | ⬜ | user_service |
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
2. ⬜ Thêm **Flask-Migrate**: `pip install Flask-Migrate`, khai báo trong `extensions.py`, chạy `flask db init` và `flask db migrate`. Sau đó seed không cần `create_all` nữa.
3. ⬜ Thêm **Flask-WTF** để có CSRF cho mọi form.
4. ⬜ Viết lại **đặt lại mật khẩu**: người dân nhận token qua email, nhân viên và admin được admin cấp lại.
5. ⬜ Viết `constants.py` và các model phòng ban, danh mục, báo cáo, ảnh, lịch sử, kết quả AI, kèm migration và dữ liệu seed.
6. ⬜ Làm luồng báo cáo của người dân với `rule_based.py`.
7. ⬜ Làm danh sách, chi tiết, cập nhật trạng thái và lịch sử cho nhân viên. Thay số liệu hard-code trên dashboard bằng dữ liệu thật.
8. ⬜ Thêm blueprint `api`, bản đồ Leaflet và biểu đồ.
9. ⬜ Thêm `llm_classifier.py` và `duplicate_service.py`.
10. ⬜ Làm quản lý người dùng và dữ liệu master cho admin.
11. ⬜ Thêm layout theo vai trò, component và trang lỗi để bỏ phần lặp trong template.

## 8. Vấn đề đã biết

- **Đặt lại mật khẩu chưa thực sự đổi mật khẩu.** Ba route reset chỉ kiểm tra form rồi chuyển trang. Không được lưu mật khẩu theo form hiện tại, vì ai biết email người khác cũng sẽ đổi được mật khẩu của họ. Xem bước 4 của lộ trình.
- **Chưa có CSRF.** Xem bước 3.
- **Form báo cáo chưa gửi dữ liệu.** Ô ảnh và ô nội dung chưa có `name`, form thiếu `enctype="multipart/form-data"`, vị trí đang là chuỗi cố định.
- **Dashboard nhân viên** đang hiển thị số liệu và biểu đồ hard-code.
- **Danh sách báo cáo của người dân** lấy dữ liệu mẫu từ `report_service.list_by_citizen()`, chưa đọc DB. Khi có model `Report` và `constants.py`, chỉ cần sửa hàm này, route không đổi.
- **Màn hình プロフィール設定 chưa lưu gì.** Nút 変更を保存する chỉ hiện `alert` mẫu, nút パスワードを変更する đang trỏ tới trang reset công khai `auth.reset_password` (sẽ đổi sang `/citizen/password`). Màn hình アイコン設定 chưa lưu ảnh: ô file chưa có `name`, form thiếu `enctype`, `users` chưa có cột ảnh đại diện.
