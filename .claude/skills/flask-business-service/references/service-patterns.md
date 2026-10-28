# Mẫu viết service (Flask + SQLAlchemy, 3 tầng)

Đọc khi bắt tay vào viết/sửa file trong `app/services/`. **Phong cách của dự án đứng trước** các mẫu dưới đây: nếu code đang dùng `Model.query.filter_by(...)` thì tiếp tục kiểu đó; nếu dùng `db.session.execute(select(...))` thì theo kiểu đó. Không trộn hai kiểu trong cùng một file.

## Mục lục
1. Hình dạng một hàm service
2. Thay dữ liệu mẫu bằng dữ liệu thật mà không làm vỡ template
3. Đọc dữ liệu (một bản ghi, danh sách, phân trang, tổng hợp cho biểu đồ)
4. Ghi dữ liệu và transaction
5. Phân quyền trong service
6. Kiểm tra đầu vào theo Excel và thông báo lỗi
7. Route gọi service
8. Test service

---

## 1. Hình dạng một hàm service

```python
def list_my_reports(user_id: int, *, status: str | None = None, page: int = 1, per_page: int = 20) -> Page:
    """市民本人の報告一覧（新しい順）。UC-05 マイページ 基本フロー 2"""
```
- Tham số thường (id, chuỗi, file đã lấy từ request), **không** nhận `request`, không gọi `flash`/`render_template`/`redirect`/`session`.
- Docstring tiếng Nhật một dòng + mã use case / bước Excel mà hàm phục vụ — để lần sau truy ngược được.
- Trả dữ liệu (model, list model, dataclass, dict) theo đúng hình dạng template đang dùng (mục 2).
- Lỗi nghiệp vụ: `raise` exception của dự án (ví dụ trong `app/services/errors.py`); route bắt và `flash`. Nếu dự án chưa có, tạo tối thiểu:
```python
class ServiceError(Exception):
    """業務エラー（message は画面にそのまま表示できる日本語）"""
class NotFoundError(ServiceError): ...
class PermissionDeniedError(ServiceError): ...
class ValidationError(ServiceError):
    def __init__(self, messages: list[str]):
        super().__init__(" / ".join(messages))
        self.messages = messages
```

## 2. Thay dữ liệu mẫu mà không làm vỡ template

Nguồn hình dạng: mục "hình dạng dữ liệu" trong output `scan_flask_app.py` (ví dụ `reports[] → id, title, status, created_at, urgency_label`) và chính dữ liệu mẫu đang trả về.

1. **Giữ chữ ký hàm** route đang gọi (tên, tham số, kiểu trả về tổng quát). Có thể thêm tham số keyword có mặc định.
2. Lập bảng: thuộc tính template dùng → nguồn thật:

| Template dùng | Nguồn thật | Ghi chú |
|---|---|---|
| `r.title` | `reports.title` | trùng tên, trả model được |
| `r.status_label` | `constants.STATUS_LABELS[r.status]` | thêm `@property` nhỏ trên model hoặc filter Jinja — theo cách dự án đang làm |
| `r.created_at` (mẫu là chuỗi) | `reports.created_at` (datetime) | template đang in thẳng → định dạng bằng filter có sẵn, không đổi kiểu trả về thành chuỗi trong service |
| `summary.total` | `COUNT(*)` | template cần nhưng route chưa truyền → thêm hàm service + truyền ở route |

3. Trả **model** khi tên thuộc tính khớp cột; chỉ dựng dict/dataclass khi cần ghép nhiều bảng hoặc giá trị tính toán. Dict trong mẫu mà template đọc `r.title` vẫn chạy với model (Jinja đọc được cả hai) — nhưng `r['title']` thì không chạy với model: sửa template sang `r.title` khi chuyển.
4. Nhãn hiển thị (tiếng Nhật) lấy từ `constants.py`, không ghép chuỗi trong service.
5. Xoá dữ liệu mẫu và comment TODO tương ứng khi đã thay xong.

## 3. Đọc dữ liệu

```python
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

# một bản ghi (404 khi không có)
report = db.session.get(Report, report_id)
if report is None:
    raise NotFoundError("報告が見つかりません")

# danh sách + nạp sẵn quan hệ template sẽ dùng (tránh N+1 trong vòng for)
stmt = (select(Report)
        .where(Report.user_id == user_id)
        .options(selectinload(Report.photos))
        .order_by(Report.created_at.desc(), Report.id.desc()))
if status:
    stmt = stmt.where(Report.status == status)
reports = db.session.execute(stmt).scalars().all()

# phân trang (Flask-SQLAlchemy 3.x) → template dùng page.items, page.pages, page.has_next...
page = db.paginate(stmt, page=page, per_page=per_page, error_out=False)

# tổng hợp cho biểu đồ (Chart.js): trả list nhãn + list số, đủ mọi mã kể cả 0
rows = dict(db.session.execute(
    select(Report.status, func.count()).group_by(Report.status)).all())
labels = [STATUS_LABELS[c] for c in STATUS_ORDER]
values = [rows.get(c, 0) for c in STATUS_ORDER]
```
- Lọc/sắp xếp/đếm làm trong SQL, không tải hết rồi lọc bằng Python.
- Mọi thời điểm so sánh dùng datetime có timezone (theo kiểu cột của dự án).
- Màn hình tìm kiếm: bỏ qua điều kiện rỗng; `ilike` cho tìm kiếm chuỗi; giới hạn `per_page`.

## 4. Ghi dữ liệu và transaction

- **Một use case = một commit**, đặt ở service (không commit trong model, không commit trong route).
- Cần id trước khi commit (để lưu ảnh, ghi lịch sử) → `db.session.flush()`.
- Ràng buộc duy nhất → bắt `IntegrityError`, `rollback()`, ném lỗi nghiệp vụ với câu trong Excel:
```python
try:
    db.session.add(user)
    db.session.commit()
except IntegrityError:
    db.session.rollback()
    raise ValidationError(["このメールアドレスは既に登録されています"])
```
- Đổi trạng thái chỉ qua service trạng thái của dự án (ví dụ `status_service.change_status(report, new_status, actor)`), service này kiểm tra chuyển trạng thái hợp lệ theo Excel và ghi bảng lịch sử trong cùng transaction.
- File (ảnh) lưu qua service lưu trữ của dự án; nếu commit DB thất bại sau khi đã ghi file, xoá file vừa ghi.
- Gọi dịch vụ ngoài (AI, mail) **sau** commit của dữ liệu chính (xem `gemini.md` mục 7).
- Không xoá cứng dữ liệu nghiệp vụ nếu Excel nói "取消/取り下げ" — đó thường là đổi trạng thái.

## 5. Phân quyền trong service

Decorator ở route chặn sai vai trò; service chặn sai **chủ sở hữu**:
```python
def get_report_for_citizen(report_id: int, user_id: int) -> Report:
    report = db.session.get(Report, report_id)
    if report is None or report.user_id != user_id:
        raise NotFoundError("報告が見つかりません")   # không để lộ báo cáo của người khác có tồn tại hay không
    return report
```
Quy tắc thao tác theo trạng thái (ví dụ 市民 chỉ huỷ được khi `received`) cũng kiểm ở service, kể cả khi template đã ẩn nút.

## 6. Kiểm tra đầu vào theo Excel

- Quy tắc lấy từ 入力項目定義 / 業務ルール / 例外フロー: bắt buộc, độ dài, định dạng, số lượng ảnh, đuôi file, dung lượng, khoảng ngày.
- Câu thông báo chép **nguyên văn** từ Excel vào `constants.py` (hoặc file thông báo của dự án), service tham chiếu hằng số.
- HTML `required`/`maxlength` chỉ là hỗ trợ; server luôn kiểm lại.
- Excel và màn hình khác nhau (ví dụ Excel 3 ảnh, input cho chọn không giới hạn) → service theo Excel, ghi chênh lệch vào báo cáo; sửa template chỉ khi developer đồng ý hoặc đó là lỗi rõ ràng (thiếu `name`, thiếu `enctype`).
- Nếu dự án có Flask-WTF: quy tắc mức ô nằm trong form class, quy tắc nghiệp vụ (trùng, quyền, trạng thái) vẫn ở service.

## 7. Route gọi service

```python
@bp.route("/reports/new", methods=["GET", "POST"])
@role_required(User.ROLE_CITIZEN)
def report_new():
    if request.method == "POST":
        try:
            report = report_service.create_report(
                user_id=current_user.id,
                title=request.form.get("title", ""),
                body=request.form.get("body", ""),
                photos=request.files.getlist("photos"),
                location=(request.form.get("lat"), request.form.get("lng")),
            )
        except ValidationError as e:
            for m in e.messages:
                flash(m, "danger")
            return render_template("citizen/report_new.html", form_data=request.form), 400
        flash(MSG_REPORT_CREATED, "success")
        return redirect(url_for("citizen.report_detail", report_id=report.id))
    return render_template("citizen/report_new.html", form_data={})
```
- Route: đọc request → gọi service → flash/redirect/render. Không query, không vòng lặp nghiệp vụ.
- Lỗi nhập: render lại cùng màn hình, giữ giá trị đã nhập.
- Thành công: PRG (redirect sau POST) tới đúng màn hình mà Excel ghi ở bước cuối / 事後条件.

## 8. Test service

- Mỗi service một file `tests/test_<service>.py`. Dùng `TestingConfig` với DB riêng (SQLite in-memory nếu model không dùng kiểu riêng của PostgreSQL; nếu có → DB PostgreSQL test riêng). Không chạy test trên DB dev.
- Mỗi bước có nhánh trong Excel ≈ một test: luồng chính, từng 例外フロー (đúng câu thông báo), sai chủ sở hữu, sai trạng thái, AI lỗi (báo cáo vẫn lưu).
- Fixture tối thiểu:
```python
@pytest.fixture
def app():
    app = create_app("testing")
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()
```
