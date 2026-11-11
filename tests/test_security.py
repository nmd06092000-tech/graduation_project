# GĐ1 セキュリティ基盤のテスト（CSRF 対策・ログアウトの POST 化・Cookie 属性・seed）
import io
import re
from pathlib import Path

import pytest
from flask import g
from flask_wtf.csrf import generate_csrf
from itsdangerous import URLSafeTimedSerializer

from app import _previous_page_path, create_app
from app.constants import MSG_CSRF_EXPIRED, MSG_UPLOAD_TOO_LARGE, ReportStatus
from app.extensions import db
from app.models import Category, Department, Report, ReportStatusHistory, User
from config import Config, TestingConfig, _env_bool
from scripts import seed as seed_script
from tests.conftest import get_csrf_token, jpeg_bytes

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / 'app' / 'templates'
JS_DIR = Path(__file__).resolve().parent.parent / 'app' / 'static' / 'js'


def _flashes(client):
    """セッションに積まれた flash を (カテゴリ, メッセージ) のリストで返す（画面を表示する前に読む）"""
    with client.session_transaction() as session:
        return [tuple(item) for item in session.get('_flashes', [])]


def _status_url(report):
    return f'/staff/reports/{report.id}/status'


def _history_count(report_id):
    return ReportStatusHistory.query.filter_by(report_id=report_id).count()


@pytest.fixture
def staff_setup(csrf_client, csrf_login, staff, citizen, make_report):
    """職員でログイン済みのクライアントと、未対応の報告（CSRF 有効）"""
    report = make_report()
    csrf_login(csrf_client, User.ROLE_STAFF, staff.login_id)
    return csrf_client, report


# ---------------------------------------------------------------------------
# 基準 1: すべての POST に CSRF トークンが必要
# ---------------------------------------------------------------------------

def test_post_without_token_redirects_with_flash_and_keeps_data(staff_setup):
    # Arrange
    client, report = staff_setup
    detail_url = f'/staff/reports/{report.id}'

    # Act: トークン無しでステータス更新を送る
    response = client.post(_status_url(report), data={'status': ReportStatus.IN_PROGRESS.value, 'comment': '対応します。'},
                           headers={'Referer': f'http://localhost{detail_url}'})

    # Assert: 400 ではなく 302 で直前の画面へ戻り、警告が積まれ、データは変わらない
    assert response.status_code == 302
    assert response.headers['Location'] == detail_url
    assert (('warning', MSG_CSRF_EXPIRED)) in _flashes(client)
    db.session.refresh(report)
    assert report.status == ReportStatus.RECEIVED.value
    assert _history_count(report.id) == 0

    # Assert: 戻った画面にメッセージが表示される
    assert MSG_CSRF_EXPIRED in client.get(detail_url).get_data(as_text=True)


def test_post_with_wrong_token_is_rejected_like_missing_token(staff_setup):
    # Arrange
    client, report = staff_setup

    # Act
    response = client.post(_status_url(report), data={
        'status': ReportStatus.IN_PROGRESS.value, 'comment': '対応します。', 'csrf_token': 'invalid-token'},
        headers={'Referer': f'/staff/reports/{report.id}'})

    # Assert
    assert response.status_code == 302
    assert ('warning', MSG_CSRF_EXPIRED) in _flashes(client)
    db.session.refresh(report)
    assert report.status == ReportStatus.RECEIVED.value


def test_post_with_token_from_another_session_is_rejected(app, staff_setup):
    # Arrange: 別セッションで発行されたトークンを作る
    # （テストでは app コンテキストが全リクエストで共有され g.csrf_token が使い回されるため、g を空にして発行する）
    client, report = staff_setup
    with app.test_request_context('/'):
        g.pop('csrf_token', None)
        foreign_token = generate_csrf()
        g.pop('csrf_token', None)

    # Act
    response = client.post(_status_url(report), data={
        'status': ReportStatus.IN_PROGRESS.value, 'comment': '対応します。', 'csrf_token': foreign_token})

    # Assert
    assert response.status_code == 302
    assert ('warning', MSG_CSRF_EXPIRED) in _flashes(client)
    db.session.refresh(report)
    assert report.status == ReportStatus.RECEIVED.value


def test_post_with_token_signed_by_another_secret_is_rejected(app, staff_setup):
    # Arrange: SECRET_KEY が違う（偽造された）トークン
    client, report = staff_setup
    forged = URLSafeTimedSerializer('another-secret', salt='wtf-csrf-token').dumps('raw-token')

    # Act
    response = client.post(_status_url(report), data={
        'status': ReportStatus.IN_PROGRESS.value, 'comment': '対応します。', 'csrf_token': forged})

    # Assert
    assert response.status_code == 302
    assert ('warning', MSG_CSRF_EXPIRED) in _flashes(client)
    db.session.refresh(report)
    assert report.status == ReportStatus.RECEIVED.value


def test_post_with_valid_form_token_succeeds(staff_setup):
    # Arrange
    client, report = staff_setup
    token = get_csrf_token(client, f'/staff/reports/{report.id}')

    # Act
    response = client.post(_status_url(report), data={
        'status': ReportStatus.IN_PROGRESS.value, 'comment': '対応します。', 'csrf_token': token})

    # Assert: 通常どおり更新され、履歴が 1 件できる
    assert response.status_code == 302
    assert response.headers['Location'] == f'/staff/reports/{report.id}'
    assert ('warning', MSG_CSRF_EXPIRED) not in _flashes(client)
    db.session.refresh(report)
    assert report.status == ReportStatus.IN_PROGRESS.value
    assert _history_count(report.id) == 1


def test_post_with_valid_header_token_succeeds(staff_setup):
    # Arrange: fetch 用の X-CSRFToken ヘッダ
    client, report = staff_setup
    token = get_csrf_token(client, f'/staff/reports/{report.id}')

    # Act
    response = client.post(_status_url(report), data={'status': ReportStatus.IN_PROGRESS.value, 'comment': '対応します。'},
                           headers={'X-CSRFToken': token})

    # Assert
    assert response.status_code == 302
    db.session.refresh(report)
    assert report.status == ReportStatus.IN_PROGRESS.value


def test_replace_history_post_without_token_returns_json_and_flash_shows_on_destination(staff_setup):
    # Arrange
    client, report = staff_setup
    detail_url = f'/staff/reports/{report.id}'

    # Act: data-nav="replace" の form は fetch で X-Replace-History 付きで送られる
    response = client.post(_status_url(report), data={'status': ReportStatus.IN_PROGRESS.value, 'comment': '対応します。'},
                           headers={'X-Replace-History': '1', 'Referer': f'http://localhost{detail_url}'})

    # Assert: リダイレクトではなく移動先 URL の JSON を返し、データは変わらない
    assert response.status_code == 200
    assert response.get_json() == {'redirect': detail_url}
    db.session.refresh(report)
    assert report.status == ReportStatus.RECEIVED.value

    # Assert: 移動先の画面で flash が表示される
    assert MSG_CSRF_EXPIRED in client.get(response.get_json()['redirect']).get_data(as_text=True)


def test_replace_history_post_with_valid_token_returns_json_redirect(staff_setup):
    # Arrange
    client, report = staff_setup
    token = get_csrf_token(client, f'/staff/reports/{report.id}')

    # Act
    response = client.post(_status_url(report), data={'status': ReportStatus.IN_PROGRESS.value, 'comment': '対応します。'},
                           headers={'X-Replace-History': '1', 'X-CSRFToken': token})

    # Assert
    assert response.status_code == 200
    assert response.get_json() == {'redirect': f'/staff/reports/{report.id}'}
    db.session.refresh(report)
    assert report.status == ReportStatus.IN_PROGRESS.value


def test_csrf_error_does_not_return_400_for_other_post_routes(csrf_client, csrf_login, citizen):
    # Arrange
    csrf_login(csrf_client, User.ROLE_CITIZEN, citizen.login_id)
    original_name, original_email = citizen.name, citizen.login_id

    # Act: プロフィール更新・アイコン削除をトークン無しで送る
    responses = [
        csrf_client.post('/citizen/profile', data={'name': '改ざん', 'email': 'x@example.com'}),
        csrf_client.post('/citizen/profile/icon/delete'),
    ]

    # Assert: 400 ではなく 302、警告が積まれ、データは変わらない
    assert [r.status_code for r in responses] == [302, 302]
    assert ('warning', MSG_CSRF_EXPIRED) in _flashes(csrf_client)
    db.session.refresh(citizen)
    assert citizen.name == original_name
    assert citizen.login_id == original_email


# ---------------------------------------------------------------------------
# 基準 2: リダイレクト先は Referer の path+query だけ（外部ホストへは行かない）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('referer, expected', [
    ('https://evil.example/', '/'),
    ('//evil.example', '/'),
    ('http://localhost.evil.com', '/'),
    ('/\\evil.example', '/'),
    ('http://localhost/\\evil.example', '/'),
    ('javascript:alert(1)', '/'),
    ('evil.example/path', '/'),
])
def test_csrf_redirect_ignores_unsafe_referer(csrf_client, referer, expected):
    # Act
    response = csrf_client.post('/login/citizen', data={'email': 'a@example.com', 'password': 'password123'},
                                headers={'Referer': referer})

    # Assert: public.index に戻り、外部ホストは Location に現れない
    assert response.status_code == 302
    assert response.headers['Location'] == expected
    assert 'evil' not in response.headers['Location']


@pytest.mark.parametrize('referer, expected', [
    ('http://localhost/login/staff', '/login/staff'),
    ('http://localhost/citizen/reports?status=received&page=2', '/citizen/reports?status=received&page=2'),
    ('http://h/citizen/profile?x=1', '/citizen/profile?x=1'),
    # ホストが外部でも使うのはパスだけ（自サイト内のパスへ変換される）
    ('https://evil.example/login/admin?x=1', '/login/admin?x=1'),
    # userinfo 風のホストでも使うのはパスだけ
    ('http://localhost@evil.com/x', '/x'),
    # 既に %xx の部分は二重にエンコードしない
    ('http://h/a%20b/c?q=%E3%81%82&z=1', '/a%20b/c?q=%E3%81%82&z=1'),
    # 非 ASCII は UTF-8 の %xx に再エンコードされる
    ('http://h/ü?é=1', '/%C3%BC?%C3%A9=1'),
])
def test_csrf_redirect_uses_only_path_and_query_of_referer(csrf_client, referer, expected):
    # Act
    response = csrf_client.post('/login/citizen', data={}, headers={'Referer': referer})

    # Assert
    assert response.status_code == 302
    assert response.headers['Location'] == expected


@pytest.mark.parametrize('referer, expected', [
    ('http://h//evil', '/'),
    ('https://evil.example//x', '/'),
    ('', '/'),
    ('http://h/citizen/profile?x=1', '/citizen/profile?x=1'),
    ('http://h/a%20b/c?q=%E3%81%82&z=1', '/a%20b/c?q=%E3%81%82&z=1'),
    ('http://h/ü?é=1', '/%C3%BC?%C3%A9=1'),
    # 制御文字・空白は Location に生で入らない（Werkzeug のクライアントでは送れないので関数を直接呼ぶ）
    ('http://h/a b/\x01c?q=\x00 x', '/a%20b/%01c?q=%00%20x'),
])
def test_previous_page_path_reencodes_referer(app, referer, expected):
    # Act
    with app.test_request_context('/', headers={'Referer': referer}):
        result = _previous_page_path()

    # Assert: 期待どおりで、制御文字・空白を含まない
    assert result == expected
    assert not re.search(r'[\x00-\x20\x7f]', result)


def test_csrf_redirect_without_referer_goes_to_public_index(csrf_client):
    # Act
    response = csrf_client.post('/login/citizen', data={})

    # Assert
    assert response.status_code == 302
    assert response.headers['Location'] == '/'
    assert ('warning', MSG_CSRF_EXPIRED) in _flashes(csrf_client)


# ---------------------------------------------------------------------------
# ログイン・登録・報告（multipart）
# ---------------------------------------------------------------------------

def test_login_without_token_is_rejected_and_not_logged_in(csrf_client, citizen):
    # Act
    response = csrf_client.post('/login/citizen', data={'email': citizen.login_id, 'password': 'password123'},
                                headers={'Referer': 'http://localhost/login/citizen'})

    # Assert: ログイン画面へ戻され、未ログインのまま
    assert response.status_code == 302
    assert response.headers['Location'] == '/login/citizen'
    assert csrf_client.get('/citizen/mypage').status_code == 302


def test_login_with_token_succeeds(csrf_client, csrf_login, citizen):
    # Act
    response = csrf_login(csrf_client, User.ROLE_CITIZEN, citizen.login_id)

    # Assert
    assert response.status_code == 302
    assert response.headers['Location'].endswith('/citizen/mypage')
    assert csrf_client.get('/citizen/mypage').status_code == 200


def test_register_without_token_does_not_create_user(csrf_client):
    # Act
    response = csrf_client.post('/register', data={
        'name': '佐藤 花子', 'email': 'hanako@example.com', 'postal_code': '330-9301',
        'address': 'さいたま市浦和区高砂3-15-1', 'password': 'password123', 'confirm_password': 'password123'},
        headers={'Referer': 'http://localhost/register'})

    # Assert
    assert response.status_code == 302
    assert response.headers['Location'] == '/register'
    assert ('warning', MSG_CSRF_EXPIRED) in _flashes(csrf_client)
    assert User.query.filter_by(login_id='hanako@example.com').first() is None


def test_register_with_token_creates_user(csrf_client):
    # Arrange
    token = get_csrf_token(csrf_client, '/register')

    # Act
    response = csrf_client.post('/register', data={
        'name': '佐藤 花子', 'email': 'hanako@example.com', 'postal_code': '330-9301',
        'address': 'さいたま市浦和区高砂3-15-1', 'password': 'password123', 'confirm_password': 'password123',
        'csrf_token': token})

    # Assert
    assert response.status_code == 302
    assert response.headers['Location'].endswith('/register/complete')
    assert User.query.filter_by(login_id='hanako@example.com').one().role == User.ROLE_CITIZEN


def _report_form(token=None):
    data = {
        'title': '道路の大きな穴', 'description': '車道に穴が開いています。',
        'address': 'さいたま市大宮区桜木町1丁目', 'latitude': '35.906', 'longitude': '139.624',
        'photos': (io.BytesIO(jpeg_bytes()), 'photo.jpg', 'image/jpeg'),
    }
    if token:
        data['csrf_token'] = token
    return data


def test_multipart_report_with_token_is_saved(csrf_client, csrf_login, citizen, road):
    # Arrange
    csrf_login(csrf_client, User.ROLE_CITIZEN, citizen.login_id)
    token = get_csrf_token(csrf_client, '/citizen/report')

    # Act
    response = csrf_client.post('/citizen/report', data=_report_form(token), content_type='multipart/form-data')

    # Assert
    report = Report.query.one()
    assert response.status_code == 302
    assert response.headers['Location'].endswith(f'/citizen/reports/{report.id}/complete')


def test_multipart_report_without_token_is_not_saved(csrf_client, csrf_login, citizen, road):
    # Arrange
    csrf_login(csrf_client, User.ROLE_CITIZEN, citizen.login_id)

    # Act
    response = csrf_client.post('/citizen/report', data=_report_form(), content_type='multipart/form-data',
                                headers={'Referer': 'http://localhost/citizen/report'})

    # Assert: 報告も写真も保存されず、報告画面へ戻って警告が出る
    assert response.status_code == 302
    assert response.headers['Location'] == '/citizen/report'
    assert ('warning', MSG_CSRF_EXPIRED) in _flashes(csrf_client)
    assert Report.query.count() == 0


@pytest.mark.parametrize('with_csrf', [False, True])
def test_upload_too_large_still_shows_upload_message(app, csrf_login, citizen, road, with_csrf):
    # Arrange: CSRF の有効・無効の両方で、上限を 1KB にして 5KB の写真を送る
    app.config['WTF_CSRF_ENABLED'] = with_csrf
    client = app.test_client()
    csrf_login(client, User.ROLE_CITIZEN, citizen.login_id)
    app.config['MAX_CONTENT_LENGTH'] = 1024
    data = {'photos': (io.BytesIO(b'x' * 5000), 'big.jpg', 'image/jpeg')}
    if with_csrf:
        data['csrf_token'] = get_csrf_token(client, '/citizen/report')

    # Act
    response = client.post('/citizen/report', data=data, content_type='multipart/form-data')

    # Assert: CSRF のメッセージではなく「合計サイズ超過」のメッセージで同じ画面へ戻る
    assert response.status_code == 302
    assert response.headers['Location'].endswith('/citizen/report')
    flashes = _flashes(client)
    assert ('warning', MSG_UPLOAD_TOO_LARGE) in flashes
    assert ('warning', MSG_CSRF_EXPIRED) not in flashes
    assert Report.query.count() == 0


# ---------------------------------------------------------------------------
# 基準 3: ログアウトは POST のみ
# ---------------------------------------------------------------------------

def test_logout_get_returns_405(csrf_client, csrf_login, citizen):
    # Arrange
    csrf_login(csrf_client, User.ROLE_CITIZEN, citizen.login_id)

    # Act / Assert: GET では何も起きず、ログイン状態も保たれる
    assert csrf_client.get('/logout').status_code == 405
    assert csrf_client.get('/citizen/mypage').status_code == 200


def test_logout_post_with_token_logs_out(csrf_client, csrf_login, citizen):
    # Arrange
    csrf_login(csrf_client, User.ROLE_CITIZEN, citizen.login_id)
    token = get_csrf_token(csrf_client, '/citizen/mypage')

    # Act
    response = csrf_client.post('/logout', data={'csrf_token': token})

    # Assert: トップへ戻り、マイページはログイン画面へ飛ばされる
    assert response.status_code == 302
    assert response.headers['Location'] == '/'
    assert csrf_client.get('/citizen/mypage').status_code == 302


@pytest.mark.parametrize('role', [User.ROLE_CITIZEN, User.ROLE_STAFF, User.ROLE_ADMIN])
def test_logout_post_with_token_logs_out_every_role(csrf_client, csrf_login, citizen, staff, role):
    # Arrange
    if role == User.ROLE_ADMIN:
        admin = User(login_id='admin01', name='システム管理者', role=User.ROLE_ADMIN)
        admin.set_password('password123')
        db.session.add(admin)
        db.session.commit()
    login_id = {User.ROLE_CITIZEN: citizen.login_id, User.ROLE_STAFF: staff.login_id}.get(role, 'admin01')
    home = {User.ROLE_CITIZEN: '/citizen/mypage', User.ROLE_STAFF: '/staff/dashboard', User.ROLE_ADMIN: '/admin/portal'}[role]
    csrf_login(csrf_client, role, login_id)
    token = get_csrf_token(csrf_client, home)
    assert csrf_client.get(home).status_code == 200

    # Act
    response = csrf_client.post('/logout', data={'csrf_token': token})

    # Assert: ログアウトされ、保護された画面はログイン画面へ飛ばされる
    assert response.status_code == 302
    assert response.headers['Location'] == '/'
    assert csrf_client.get(home).status_code == 302


def test_logout_post_with_stale_token_after_logout_does_not_crash(csrf_client, csrf_login, citizen):
    # Arrange: ログアウト済み（セッションが空になり、古いトークンは無効）
    csrf_login(csrf_client, User.ROLE_CITIZEN, citizen.login_id)
    token = get_csrf_token(csrf_client, '/citizen/mypage')
    assert csrf_client.post('/logout', data={'csrf_token': token}).status_code == 302

    # Act: 別タブなどで古いトークンのまま再度ログアウトを送る
    response = csrf_client.post('/logout', data={'csrf_token': token})

    # Assert: 500/400 にならず 302 で戻る（flash が 2 件積まれても許容）
    assert response.status_code == 302
    assert csrf_client.get('/citizen/mypage').status_code == 302


def test_logout_post_without_token_keeps_session(csrf_client, csrf_login, citizen):
    # Arrange
    csrf_login(csrf_client, User.ROLE_CITIZEN, citizen.login_id)

    # Act
    response = csrf_client.post('/logout', headers={'Referer': 'http://localhost/citizen/mypage'})

    # Assert: ログアウトされず、元の画面へ戻って警告が出る
    assert response.status_code == 302
    assert response.headers['Location'] == '/citizen/mypage'
    assert ('warning', MSG_CSRF_EXPIRED) in _flashes(csrf_client)
    assert csrf_client.get('/citizen/mypage').status_code == 200


# ---------------------------------------------------------------------------
# すべての POST form に csrf_token が埋め込まれている
# ---------------------------------------------------------------------------

def _post_forms(html):
    """HTML 中の method=post の form を (開始タグ, 中身) のリストで返す"""
    forms = []
    for match in re.finditer(r'(<form\b[^>]*>)(.*?)</form>', html, flags=re.DOTALL | re.IGNORECASE):
        if re.search(r'method\s*=\s*["\']post["\']', match.group(1), flags=re.IGNORECASE):
            forms.append(match.groups())
    return forms


LOGOUT_FORM_TAG = "action=\"{{ url_for('auth.logout') }}\""


def test_every_post_form_in_templates_has_csrf_token():
    # Arrange / Act: 全テンプレートの POST form を走査する
    missing, total, logout_forms = [], 0, 0
    for path in sorted(TEMPLATE_DIR.rglob('*.html')):
        for tag, body in _post_forms(path.read_text(encoding='utf-8')):
            total += 1
            if LOGOUT_FORM_TAG in tag:
                logout_forms += 1
            if 'name="csrf_token"' not in body or '{{ csrf_token() }}' not in body:
                missing.append(f'{path.relative_to(TEMPLATE_DIR)}: {tag}')

    # Assert: 走査対象が減っていない（form の取りこぼし・テンプレート削除を検知する）
    assert total >= 32
    assert logout_forms == 13
    assert missing == []


def test_logout_is_never_linked_outside_post_form():
    # Arrange: テンプレートと JS から「/logout への href」「url_for('auth.logout')」を探す
    sources = list(TEMPLATE_DIR.rglob('*.html')) + list(JS_DIR.rglob('*.js'))
    offenders = []
    for path in sources:
        text = path.read_text(encoding='utf-8')
        # POST form の中身を取り除いた残りに logout が残っていないこと（form の開始タグ自体は残す）
        stripped = re.sub(r'<form\b[^>]*method\s*=\s*["\']post["\'][^>]*>.*?</form>', '', text,
                          flags=re.DOTALL | re.IGNORECASE)
        if re.search(r'href\s*=\s*["\'][^"\']*/logout', stripped, flags=re.IGNORECASE) \
                or "url_for('auth.logout')" in stripped or 'url_for("auth.logout")' in stripped:
            offenders.append(str(path.relative_to(TEMPLATE_DIR.parent)))

    # Assert
    assert len(sources) > 0
    assert offenders == []


# ログアウトのモーダル（POST /logout の form）を持つ画面
LOGOUT_MODAL_PATHS = {
    '/citizen/mypage', '/citizen/reports', '/citizen/profile', '/citizen/profile/icon',
    '/staff/dashboard', '/staff/reports', '/staff/reports/{report_id}', '/staff/map',
    '/staff/ai-analysis', '/staff/duplicates', '/staff/stats', '/admin/portal',
}


@pytest.mark.parametrize('role, path', [
    (None, '/login/citizen'), (None, '/login/staff'), (None, '/login/admin'),
    (None, '/register'), (None, '/reset-password'),
    (None, '/login/staff/reset-password'), (None, '/login/admin/reset-password'),
    (User.ROLE_CITIZEN, '/citizen/mypage'), (User.ROLE_CITIZEN, '/citizen/report'),
    (User.ROLE_CITIZEN, '/citizen/report/camera'), (User.ROLE_CITIZEN, '/citizen/reports'),
    (User.ROLE_CITIZEN, '/citizen/profile'), (User.ROLE_CITIZEN, '/citizen/profile/icon'),
    (User.ROLE_CITIZEN, '/citizen/profile/icon/camera'),
    (User.ROLE_STAFF, '/staff/dashboard'), (User.ROLE_STAFF, '/staff/reports'),
    (User.ROLE_STAFF, '/staff/reports/{report_id}'), (User.ROLE_STAFF, '/staff/map'),
    (User.ROLE_STAFF, '/staff/ai-analysis'), (User.ROLE_STAFF, '/staff/duplicates'),
    (User.ROLE_STAFF, '/staff/stats'),
    (User.ROLE_ADMIN, '/admin/portal'), (User.ROLE_ADMIN, '/admin/users/register'),
])
def test_rendered_post_forms_embed_csrf_token(csrf_client, csrf_login, citizen, staff, make_report,
                                              role, path):
    # Arrange
    report = make_report()
    if role == User.ROLE_ADMIN:
        admin = User(login_id='admin01', name='システム管理者', role=User.ROLE_ADMIN)
        admin.set_password('password123')
        db.session.add(admin)
        db.session.commit()
        login_id = admin.login_id
    else:
        login_id = {User.ROLE_CITIZEN: citizen, User.ROLE_STAFF: staff}.get(role, None)
        login_id = login_id.login_id if login_id else None
    if role:
        csrf_login(csrf_client, role, login_id)

    # Act
    response = csrf_client.get(path.format(report_id=report.id))
    html = response.get_data(as_text=True)

    # Assert: 描画された画面の POST form すべてにトークンが入り、値は空でない
    assert response.status_code == 200
    forms = _post_forms(html)
    assert forms, f'{path} に POST form が 1 つも無い（走査が空振りしている）'
    for tag, body in forms:
        match = re.search(r'name="csrf_token"\s+value="([^"]+)"', body)
        assert match, f'csrf_token が無い form: {tag}'

    # Assert: ログアウトのモーダルがある画面では、POST /logout の form にもトークンが入っている
    if path in LOGOUT_MODAL_PATHS:
        logout_forms = [(tag, body) for tag, body in forms if 'action="/logout"' in tag]
        assert logout_forms, f'{path} に /logout の POST form が無い'
        assert all(re.search(r'name="csrf_token"\s+value="[^"]+"', body) for _, body in logout_forms)


# ---------------------------------------------------------------------------
# 基準 4: Cookie の属性
# ---------------------------------------------------------------------------

def _set_cookies(response):
    """Set-Cookie ヘッダを {Cookie 名: 属性を含む文字列} にする"""
    return {value.split('=', 1)[0]: value for value in response.headers.getlist('Set-Cookie')}


def test_session_and_remember_cookies_are_httponly_samesite_lax_without_secure(app, client, citizen):
    # Act: 「ログイン状態を保持する」付きでログイン
    response = client.post('/login/citizen', data={
        'email': citizen.login_id, 'password': 'password123', 'remember': 'on'})

    # Assert: 既定（開発・テスト）では Secure は付かない
    cookies = _set_cookies(response)
    assert {'session', 'remember_token'} <= set(cookies)
    for name in ('session', 'remember_token'):
        assert 'HttpOnly' in cookies[name]
        assert 'SameSite=Lax' in cookies[name]
        assert 'Secure' not in cookies[name]


def test_cookies_get_secure_flag_when_config_enables_it(app, citizen):
    # Arrange: SESSION_COOKIE_SECURE=true 相当の設定でアプリを作る（.env は触らない）
    class SecureConfig(TestingConfig):
        SESSION_COOKIE_SECURE = True
        REMEMBER_COOKIE_SECURE = True

    secure_app = create_app(SecureConfig)
    client = secure_app.test_client()

    # Act
    response = client.post('/login/citizen', base_url='https://localhost', data={
        'email': citizen.login_id, 'password': 'password123', 'remember': 'on'})

    # Assert
    cookies = _set_cookies(response)
    for name in ('session', 'remember_token'):
        assert 'Secure' in cookies[name]
        assert 'HttpOnly' in cookies[name]
        assert 'SameSite=Lax' in cookies[name]


@pytest.mark.parametrize('value, expected', [
    ('true', True), ('TRUE', True), ('1', True), ('yes', True), ('on', True), ('  On  ', True),
    ('false', False), ('FALSE', False), ('0', False), ('no', False), ('off', False), (' Off ', False),
])
def test_env_bool_parses_values(monkeypatch, value, expected):
    # Arrange
    monkeypatch.setenv('X_T', value)

    # Act / Assert: どちらの default でも値が優先される
    assert _env_bool('X_T', not expected) is expected
    assert _env_bool('X_T', expected) is expected


@pytest.mark.parametrize('value', ['abc', 'ture', '2', 'y'])
def test_env_bool_raises_value_error_for_invalid_value(monkeypatch, value):
    # Arrange
    monkeypatch.setenv('X_T', value)

    # Act / Assert: 打ち間違いは黙って False にせず、変数名と元の値（repr）を含む ValueError にする
    with pytest.raises(ValueError) as excinfo:
        _env_bool('X_T', False)
    message = str(excinfo.value)
    assert 'X_T' in message
    assert repr(value) in message
    assert '不正です' in message


def test_env_bool_invalid_message_keeps_original_case(monkeypatch):
    # Arrange
    monkeypatch.setenv('X_T', 'Ture')

    # Act / Assert
    with pytest.raises(ValueError, match="X_T の値 'Ture' は不正です"):
        _env_bool('X_T', True)


@pytest.mark.parametrize('default', [True, False])
def test_env_bool_returns_default_when_unset_or_blank(monkeypatch, default):
    # Arrange / Act / Assert
    monkeypatch.delenv('SESSION_COOKIE_SECURE', raising=False)
    assert _env_bool('SESSION_COOKIE_SECURE', default) is default
    monkeypatch.setenv('SESSION_COOKIE_SECURE', '  ')
    assert _env_bool('SESSION_COOKIE_SECURE', default) is default


def test_csrf_ssl_strict_defaults_to_true_when_env_unset(monkeypatch):
    # Arrange: 設定クラスは import 時に評価済みなので、Config の値と、未設定時に使われる _env_bool の両方を確かめる
    monkeypatch.delenv('WTF_CSRF_SSL_STRICT', raising=False)

    # Act / Assert: 既定は有効（HTTPS のとき Referer の同一オリジンを検査する）
    assert _env_bool('WTF_CSRF_SSL_STRICT', True) is True
    monkeypatch.setenv('WTF_CSRF_SSL_STRICT', 'false')
    assert _env_bool('WTF_CSRF_SSL_STRICT', True) is False
    assert Config.WTF_CSRF_SSL_STRICT is True


def test_config_cookie_and_csrf_settings():
    # Assert: HttpOnly / SameSite は固定、remember の Secure は session と同じ値、CSRF トークンは期限なし
    assert Config.SESSION_COOKIE_HTTPONLY is True and Config.REMEMBER_COOKIE_HTTPONLY is True
    assert Config.SESSION_COOKIE_SAMESITE == 'Lax' and Config.REMEMBER_COOKIE_SAMESITE == 'Lax'
    assert Config.REMEMBER_COOKIE_SECURE == Config.SESSION_COOKIE_SECURE
    assert Config.WTF_CSRF_TIME_LIMIT is None
    assert TestingConfig.WTF_CSRF_ENABLED is False
    assert TestingConfig.SESSION_COOKIE_SECURE is False and TestingConfig.REMEMBER_COOKIE_SECURE is False


# ---------------------------------------------------------------------------
# 基準 5: seed（テストアカウントは明示したときだけ）
# ---------------------------------------------------------------------------

def test_seed_default_creates_master_only(app):
    # Act
    seed_script.seed()

    # Assert
    assert Department.query.count() == len(seed_script.DEPARTMENTS)
    assert Category.query.count() == len(seed_script.CATEGORIES)
    assert User.query.count() == 0


def test_seed_with_test_users_creates_three_accounts(app):
    # Act
    seed_script.seed(with_test_users=True)

    # Assert
    users = {u.login_id: u for u in User.query.all()}
    assert set(users) == {'taro@example.com', 'staff01', 'admin01'}
    assert users['taro@example.com'].role == User.ROLE_CITIZEN
    assert users['staff01'].role == User.ROLE_STAFF
    assert users['admin01'].role == User.ROLE_ADMIN
    assert all(u.check_password('password123') for u in users.values())


@pytest.mark.parametrize('with_test_users, user_count', [(False, 0), (True, 3)])
def test_seed_is_idempotent(app, with_test_users, user_count):
    # Act: 2 回実行する
    seed_script.seed(with_test_users=with_test_users)
    seed_script.seed(with_test_users=with_test_users)

    # Assert: 重複して増えない
    assert Department.query.count() == len(seed_script.DEPARTMENTS)
    assert Category.query.count() == len(seed_script.CATEGORIES)
    assert User.query.count() == user_count


def test_seed_keeps_existing_users_and_does_not_reset_password(app, staff):
    # Arrange: 既存の staff01（パスワードを変更済みとする）
    staff.set_password('changed-password')
    db.session.commit()

    # Act
    seed_script.seed(with_test_users=True)

    # Assert: 既存アカウントは消えず、パスワードも上書きされない
    kept = User.query.filter_by(login_id='staff01').one()
    assert kept.id == staff.id
    assert kept.check_password('changed-password')
    assert User.query.count() == 3


@pytest.mark.parametrize('argv, user_count', [([], 0), (['--with-test-users'], 3)])
def test_seed_main_flag_controls_test_users(app, monkeypatch, argv, user_count):
    # Arrange: main() が開発 DB のアプリを作らないよう、テスト用アプリを返させる
    monkeypatch.setattr(seed_script, 'create_app', lambda: app)

    # Act
    seed_script.main(argv)

    # Assert
    assert User.query.count() == user_count
    assert Category.query.count() == len(seed_script.CATEGORIES)
