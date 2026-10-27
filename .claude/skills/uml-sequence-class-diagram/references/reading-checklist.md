# Đọc hệ thống trước khi vẽ — checklist chi tiết

Mục đích: với mỗi bước chuyển của use case, biết chính xác **màn hình gửi gì, view nhận gì, kiểm tra gì theo thứ tự nào, đọc/ghi entity nào, hiển thị thông báo nguyên văn nào, chuyển đi đâu**. Đọc code thật; README/tài liệu chỉ cho biết ý định.

## Mục lục
1. Định vị dự án và tài liệu
2. Flask (blueprint) — chỗ cần đọc
3. Django — chỗ cần đọc
4. Framework khác
5. Lệnh grep hữu ích
6. Từng loại thông tin → lấy ở đâu
7. Bẫy thường gặp

---

## 1. Định vị dự án và tài liệu
- `README.md`, `docs/`, file đặc tả (ユースケース記述, 画面一覧, 画面遷移図, 用語集). Nếu README có bảng use case (actor / URL / endpoint / service) thì dùng nó làm bản đồ.
- Sơ đồ đã có: `**/*.puml`, `**/*.drawio`, `**/*.dio`, `**/*クラス図*`, `**/*シーケンス*`. Tên gọi trong đó là chuẩn của dự án.
- Hằng số / nhãn hiển thị: `constants.py`, `messages.py`, file i18n (`*.po`, `ja.json`), enum.

## 2. Flask (blueprint)
| Cần biết | Đọc ở đâu |
|---|---|
| URL đầy đủ | `app/__init__.py` (`register_blueprint(..., url_prefix=...)`) + `@bp.route(...)` / `@bp.get` / `@bp.post` trong `app/blueprints/*/routes.py` |
| Guard | decorator trên hàm: `@login_required`, `@role_required(...)`, kiểm tra `current_user.is_authenticated` đầu hàm; định nghĩa decorator (thường `app/utils/decorators.py`) để biết chưa đăng nhập đi đâu, sai role trả gì (403/redirect) |
| Xử lý GET/POST | thân hàm route: `if request.method == "POST"`, `request.form[...]`, `request.form.get(..)`, `request.args` |
| Kiểm tra & thông báo | service (`app/services/*.py`), form class (`app/forms/`, `validators=[...]`), `utils/validators.py`, `flash("...")`, `form.errors`, `raise ValueError("...")` rồi route bắt và flash |
| DB | model (`app/models/*.py`: cột, `unique=True`, quan hệ, phương thức như `check_password`), query trong service (`Model.query.filter_by(...)`, `db.session.execute(select(...))`), `db.session.add/commit/rollback`, `except IntegrityError` |
| Session / cookie | `login_user(user, remember=...)`, `logout_user()`, `session[...]`, `session.clear()`, `response.set_cookie/delete_cookie`; với Flask-Login, "ログイン状態を保持" = `remember=True` (cookie remember_token) |
| Chuyển tiếp | `redirect(url_for("bp.endpoint"))` → tìm hàm có endpoint đó; `render_template("x/y.html", ...)` |
| Màn hình | `templates/<bp>/*.html`, layout cha `templates/layouts/base.html` (menu, nút ログアウト), `templates/components/`, JS trong `static/js/` (confirm, modal) |

## 3. Django
| Cần biết | Đọc ở đâu |
|---|---|
| URL | `urls.py` của project và app (`path("register/", views.register_view, name="register")`, `include(...)`) |
| Guard | `@login_required`, `LoginRequiredMixin`, `UserPassesTestMixin`, `PermissionDenied` → 403 |
| Xử lý | function view (`if request.method == "POST"`) hoặc class view (`get()`, `post()`, `form_valid()`, `form_invalid()`, `get_success_url()`) |
| Kiểm tra & thông báo | `forms.py` (`clean_<field>()`, `clean()`, `ValidationError("...")`, `error_messages`), `messages.error(...)`, model `validators`, `unique=True` |
| DB | `models.py`, `Model.objects.filter/get/create`, `save()`, `transaction.atomic()`, `IntegrityError` |
| Session | `login()`, `logout()`, `request.session[...]`, `set_expiry` (ghi nhớ đăng nhập) |
| Chuyển tiếp | `redirect("name")`, `reverse(...)`, `success_url`, `render(request, "x.html")` |

## 4. Framework khác
Tìm 6 thứ tương đương: bảng route, guard/middleware, handler, validator + thông báo, truy cập DB, template. Ghi rõ trong báo cáo nếu một phần không xác định được.

## 5. Lệnh grep hữu ích (chạy ở gốc dự án)
```bash
grep -rn "route(\|\.get(\"/\|\.post(\"/\|url_prefix\|path(" --include=*.py .        # bảng URL
grep -rn "render_template\|render(request\|TemplateResponse" --include=*.py .     # view → template
grep -rn "redirect(" --include=*.py .                                              # chuyển tiếp
grep -rn "flash(\|messages\.\(error\|success\|info\|warning\)\|ValidationError(" --include=*.py .  # thông báo
grep -rn "login_required\|role_required\|abort(403\|PermissionDenied" --include=*.py .            # guard
grep -rn "session\[\|login_user\|logout_user\|set_cookie\|delete_cookie\|remember" --include=*.py . # session/cookie
grep -rn "db.session\.\(add\|commit\|rollback\)\|\.save()\|\.create(\|IntegrityError" --include=*.py . # ghi DB
grep -rn "confirm(\|data-bs-toggle=\"modal\"\|onsubmit" templates static                          # hộp thoại phía client
grep -rn "<form\|<button\|type=\"submit\"\|<label\|<input\|<select\|<textarea" templates/<màn hình>.html
```

## 6. Từng loại thông tin → lấy ở đâu
| Trong sơ đồ | Lấy từ |
|---|---|
| Tên màn hình (boundary) | `<title>`, `<h1>`/`<h2>` của template, tên trong 画面一覧; chuẩn hoá thành 〜画面 (trừ tên riêng như マイページ, ダッシュボード) |
| Các ô nhập & thứ tự | thứ tự `<label>`/`<input>` trong form; nhãn tiếng Nhật đúng như hiển thị |
| Tham số POST | `name` của input mà view thực sự đọc (`request.form`), đặt tên bằng nhãn tiếng Nhật ngắn gọn; gồm cả checkbox (ログイン状態を保持) |
| Chữ trên nút | text của `<button type="submit">` / `value` của `<input type="submit">` |
| Điều kiện kiểm tra & thứ tự | thứ tự thực thi thật: route → form → service → model. Kiểm tra trong form class chạy trước code trong route |
| Thông báo nguyên văn | chuỗi trong `flash`/`ValidationError`/constants; nếu ghép động thì ghi bản hiển thị cho trường hợp đang vẽ |
| Truy cập entity | query (điều kiện lọc → 「〜で〜を検索する()」), add/commit (→「〜を保存する(項目…)」), gán cột + commit (→「〜を更新する()」), rollback (→「保存を取り消す()」) |
| Xử lý nội bộ | chuẩn hoá (`.lower()`, `.strip()`), hash mật khẩu, ghi/xoá session, cookie |
| Đích chuyển | `redirect(url_for(...))` → view; `render_template(...)` sau POST (render lại cùng màn hình khi lỗi) |
| Kết quả hiển thị ở màn hình đích | hàm GET của view đích: guard + template |

## 7. Bẫy thường gặp
- **Kiểm tra trùng hai lớp**: HTML `required`/`maxlength` và kiểm tra server cùng tồn tại → vẽ kiểm tra server (đó là việc của view); nhắc phần client trong báo cáo.
- **Thông báo khác nhau theo vai trò** (ví dụ 職員 dùng câu khác) → ghi trong comment cuối file, không gộp mất.
- **So khớp mật khẩu nằm trong model** (`user.check_password`) → vẫn là bước của view gọi entity; trong sequence có thể gộp vào điều kiện alt「利用者が存在しパスワードが一致する場合」như mẫu.
- **Tìm theo nhiều điều kiện** (login_id + role) → ghi đủ:「ログインIDとロールで利用者を検索する()」.
- **Xử lý lỗi bằng exception** (`try/except IntegrityError`) → một alt riêng sau bước lưu.
- **Code chưa làm xong** (service trả dữ liệu mẫu, form chỉ kiểm tra mà không lưu) → vẽ đúng hiện trạng và thêm comment「現状は…」ở đầu file.
- **Route có trong README nhưng chưa có trong code** (hoặc ngược lại) → báo cho người dùng, không tự bịa luồng.
