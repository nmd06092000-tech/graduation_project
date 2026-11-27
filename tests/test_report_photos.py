# 添付写真の配信（権限チェック付き）と、報告詳細（市民・職員）での写真表示
import itertools

from app.extensions import db
from app.models import ReportPhoto, User
from app.services import report_service, storage_service
from tests.conftest import jpeg_bytes, make_account

BIGINT_OVER = 2 ** 63
_counter = itertools.count(1)


def _add_photo(report, *, write_file=True):
    """UPLOAD_FOLDER に実ファイルを置き、報告に写真を1枚ひも付ける"""
    relative = f'reports/test{next(_counter):04d}.jpg'
    if write_file:
        path = storage_service.path_of(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(jpeg_bytes())
    photo = ReportPhoto(report_id=report.id, file_path=relative, content_type='image/jpeg')
    db.session.add(photo)
    db.session.commit()
    return photo


def _other_citizen_report(make_report):
    other = make_account('other@example.com', User.ROLE_CITIZEN)
    report = make_report(citizen_id=other.id)
    return report, _add_photo(report)


# ---------- report_service.get_photo_file ----------

# 本人の写真：ファイルのパスと Content-Type が返る
def test_get_photo_file_returns_file_for_owner(citizen, make_report):
    report = make_report()
    photo = _add_photo(report)

    result = report_service.get_photo_file(report.id, photo.id, citizen_id=citizen.id)

    assert result.content_type == 'image/jpeg'
    assert result.path.read_bytes() == jpeg_bytes()


# 他の市民の報告の写真は None
def test_get_photo_file_none_for_other_citizen(citizen, make_report):
    report, photo = _other_citizen_report(make_report)

    assert report_service.get_photo_file(report.id, photo.id, citizen_id=citizen.id) is None


# 写真が別の報告に属しているときは None（report_id と photo_id の組み合わせ違い）
def test_get_photo_file_none_when_photo_belongs_to_other_report(citizen, make_report):
    mine = make_report()
    another = make_report()
    photo = _add_photo(another)

    assert report_service.get_photo_file(mine.id, photo.id, citizen_id=citizen.id) is None
    assert report_service.get_photo_file(mine.id, photo.id) is None


# 職員（citizen_id なし）は誰の報告の写真も取得できる
def test_get_photo_file_staff_can_get_any_photo(make_report):
    report, photo = _other_citizen_report(make_report)

    result = report_service.get_photo_file(report.id, photo.id)

    assert result is not None and result.path.is_file()


# DB にはあるがディスク上のファイルがない：None
def test_get_photo_file_none_when_file_missing(citizen, make_report):
    report = make_report()
    photo = _add_photo(report, write_file=False)

    assert report_service.get_photo_file(report.id, photo.id, citizen_id=citizen.id) is None


# 範囲外の ID（0・負数・bigint 超過）は None
def test_get_photo_file_none_for_out_of_range_ids(citizen, make_report):
    report = make_report()
    photo = _add_photo(report)

    for bad in (0, -1, BIGINT_OVER):
        assert report_service.get_photo_file(report.id, bad, citizen_id=citizen.id) is None
        assert report_service.get_photo_file(bad, photo.id, citizen_id=citizen.id) is None


# ---------- ルート ----------

# 市民：200 とセキュリティ関連ヘッダー
def test_citizen_photo_route_serves_file_with_headers(client, login, citizen, make_report):
    report = make_report()
    photo = _add_photo(report)
    login(client, User.ROLE_CITIZEN, citizen.login_id)

    response = client.get(f'/citizen/reports/{report.id}/photos/{photo.id}')

    assert response.status_code == 200
    assert response.headers['Content-Type'].startswith('image/jpeg')
    cache = response.headers['Cache-Control']
    assert 'private' in cache and 'no-cache' in cache and 'no-store' in cache
    assert response.headers['X-Content-Type-Options'] == 'nosniff'
    assert response.data == jpeg_bytes()
    response.close()


# 他人の報告の写真は 404
def test_citizen_photo_route_404_for_other_users_photo(client, login, citizen, make_report):
    report, photo = _other_citizen_report(make_report)
    login(client, User.ROLE_CITIZEN, citizen.login_id)

    assert client.get(f'/citizen/reports/{report.id}/photos/{photo.id}').status_code == 404


# 未ログインはログイン画面へ
def test_photo_routes_redirect_anonymous_to_login(client, citizen, make_report):
    report = make_report()
    photo = _add_photo(report)

    for url in (f'/citizen/reports/{report.id}/photos/{photo.id}',
                f'/staff/reports/{report.id}/photos/{photo.id}'):
        response = client.get(url)
        assert response.status_code == 302 and '/login' in response.headers['Location']


# 取り消した報告でも写真は配信され、詳細画面にも写真が出る
def test_cancelled_report_still_shows_and_serves_photo(client, login, citizen, make_report):
    report = make_report(status='cancelled')
    photo = _add_photo(report)
    login(client, User.ROLE_CITIZEN, citizen.login_id)

    served = client.get(f'/citizen/reports/{report.id}/photos/{photo.id}')
    page = client.get(f'/citizen/reports/{report.id}').get_data(as_text=True)

    assert served.status_code == 200
    assert f'/citizen/reports/{report.id}/photos/{photo.id}' in page
    served.close()


# 職員：どの報告の写真も 200
def test_staff_photo_route_serves_any_report_photo(client, login, staff, make_report):
    report, photo = _other_citizen_report(make_report)
    login(client, User.ROLE_STAFF, staff.login_id)

    response = client.get(f'/staff/reports/{report.id}/photos/{photo.id}')

    assert response.status_code == 200 and response.headers['Content-Type'].startswith('image/jpeg')
    assert 'no-store' in response.headers['Cache-Control']
    response.close()


# file_path がアップロードフォルダの外を指している（改ざん）：service は None、市民・職員ルートとも 404
def test_photo_with_path_traversal_file_path_returns_none_and_404(client, login, citizen, staff, make_report):
    report = make_report()
    photo = ReportPhoto(report_id=report.id, file_path='../x.jpg', content_type='image/jpeg')
    db.session.add(photo)
    db.session.commit()

    assert report_service.get_photo_file(report.id, photo.id, citizen_id=citizen.id) is None
    login(client, User.ROLE_CITIZEN, citizen.login_id)
    assert client.get(f'/citizen/reports/{report.id}/photos/{photo.id}').status_code == 404
    client.get('/logout')
    from flask import g
    g.pop('_login_user', None)
    login(client, User.ROLE_STAFF, staff.login_id)
    assert client.get(f'/staff/reports/{report.id}/photos/{photo.id}').status_code == 404


# 市民は職員用ルートを使えない（403）
def test_citizen_blocked_from_staff_photo_route(client, login, citizen, make_report):
    report = make_report()
    photo = _add_photo(report)
    login(client, User.ROLE_CITIZEN, citizen.login_id)

    assert client.get(f'/staff/reports/{report.id}/photos/{photo.id}').status_code == 403


# 職員の報告詳細：bigint 超過の ID は 404
def test_staff_report_detail_404_for_over_bigint_id(client, login, staff):
    login(client, User.ROLE_STAFF, staff.login_id)

    assert client.get(f'/staff/reports/{BIGINT_OVER}').status_code == 404


# 詳細画面：写真があれば <img>（権限付き URL）、なければ「添付写真はありません」（市民・職員とも）
def test_detail_pages_show_photo_img_or_empty_message(client, login, citizen, staff, make_report):
    with_photo = make_report()
    photo = _add_photo(with_photo)
    without = make_report()
    login(client, User.ROLE_CITIZEN, citizen.login_id)

    citizen_page = client.get(f'/citizen/reports/{with_photo.id}').get_data(as_text=True)
    citizen_empty = client.get(f'/citizen/reports/{without.id}').get_data(as_text=True)
    client.get('/logout')
    from flask import g
    g.pop('_login_user', None)
    login(client, User.ROLE_STAFF, staff.login_id)
    staff_page = client.get(f'/staff/reports/{with_photo.id}').get_data(as_text=True)
    staff_empty = client.get(f'/staff/reports/{without.id}').get_data(as_text=True)

    assert '<img' in citizen_page and f'/citizen/reports/{with_photo.id}/photos/{photo.id}' in citizen_page
    assert '<img' in staff_page and f'/staff/reports/{with_photo.id}/photos/{photo.id}' in staff_page
    assert '添付写真はありません' in citizen_empty and '添付写真はありません' in staff_empty
