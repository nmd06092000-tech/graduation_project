# 主要ルートの煙テスト（登録→ログイン→報告→職員対応→市民確認、および権限制御）
import io

import pytest

from app.constants import MSG_COMMENT_REQUIRED, MSG_INVALID_STATUS, MSG_STATUS_UPDATED, ReportStatus
from app.extensions import db
from app.models import AiAnalysis, Report, ReportStatusHistory, User
from tests.conftest import jpeg_bytes


def _post_report(client, title='道路の大きな穴'):
    """写真1枚・座標付きで新規報告を送信する（GEOCODER_URL は空なので座標はフォームから渡す）"""
    return client.post('/citizen/report', data={
        'title': title,
        'description': '車道に穴が開いています。',
        'address': 'さいたま市大宮区桜木町1丁目',
        'latitude': '35.906',
        'longitude': '139.624',
        'photos': (io.BytesIO(jpeg_bytes()), 'photo.jpg', 'image/jpeg'),
    }, content_type='multipart/form-data')


def _history_count(report_id):
    return ReportStatusHistory.query.filter_by(report_id=report_id).count()


def test_register_then_login_citizen(client, login):
    # Arrange: 大文字のメールアドレスとハイフンなしの郵便番号で登録する
    response = client.post('/register', data={
        'name': '佐藤 花子', 'email': 'Hanako@Example.COM', 'postal_code': '3309301',
        'address': 'さいたま市浦和区高砂3-15-1', 'password': 'password123', 'confirm_password': 'password123'})

    # Assert: 登録完了へ遷移し、メールは小文字・郵便番号はハイフン付きで保存される
    assert response.status_code == 302
    assert response.headers['Location'].endswith('/register/complete')
    assert client.get(response.headers['Location']).status_code == 200
    user =User.query.filter_by(login_id='hanako@example.com').one()
    assert user.role == User.ROLE_CITIZEN
    assert user.postal_code == '330-9301'

    # Assert: ログイン画面が表示される
    assert client.get(f'/login/{User.ROLE_CITIZEN}').status_code == 200

    # Act: 大文字のままのメールアドレスでログインする
    response = login(client, User.ROLE_CITIZEN, 'Hanako@Example.COM')

    # Assert: マイページへ遷移する
    assert response.status_code == 302
    assert response.headers['Location'].endswith('/citizen/mypage')
    assert client.get('/citizen/mypage').status_code == 200


def test_citizen_submits_report_appears_in_history(client, login, citizen, road):
    # Arrange
    login(client, User.ROLE_CITIZEN, citizen.login_id)

    # Act
    response = _post_report(client)

    # Assert: 完了画面へリダイレクトし、報告・写真・履歴・AI 結果が保存される
    report = Report.query.one()
    assert response.status_code == 302
    assert response.headers['Location'].endswith(f'/citizen/reports/{report.id}/complete')
    assert client.get(response.headers['Location']).status_code == 200
    assert report.status == ReportStatus.RECEIVED.value
    assert len(report.photos) == 1
    assert _history_count(report.id) == 1
    analyses = AiAnalysis.query.filter_by(report_id=report.id).all()
    assert len(analyses) == 1
    assert analyses[0].model_name == 'rule_based'

    # Assert: 報告一覧（履歴）にタイトルと「未対応」が表示される
    html = client.get('/citizen/reports').get_data(as_text=True)
    assert '道路の大きな穴' in html
    assert ReportStatus.RECEIVED.label in html


def test_staff_sees_report_and_updates_status_citizen_sees_it(app, client, login, citizen, staff, make_report):
    # Arrange
    report = make_report()
    staff_client = app.test_client()
    login(staff_client, User.ROLE_STAFF, staff.login_id)

    # Act / Assert: 一覧と詳細が表示される
    list_response = staff_client.get('/staff/reports')
    assert list_response.status_code == 200
    assert report.title in list_response.get_data(as_text=True)
    detail_response = staff_client.get(f'/staff/reports/{report.id}')
    assert detail_response.status_code == 200
    assert report.title in detail_response.get_data(as_text=True)

    # Act: 対応中へ更新する
    before = _history_count(report.id)
    response = staff_client.post(f'/staff/reports/{report.id}/status', data={
        'status': ReportStatus.IN_PROGRESS.value, 'comment': '現地確認に向かいます。'})

    # Assert: 詳細へ戻り、成功メッセージが表示され、履歴が1行増える
    assert response.status_code == 302
    assert response.headers['Location'].endswith(f'/staff/reports/{report.id}')
    html = staff_client.get(response.headers['Location']).get_data(as_text=True)
    assert MSG_STATUS_UPDATED.format(report_id=report.id, status=ReportStatus.IN_PROGRESS.label) in html
    db.session.expire_all()
    assert db.session.get(Report, report.id).status == ReportStatus.IN_PROGRESS.value
    assert _history_count(report.id) == before + 1

    # Assert: 市民には「対応中」と表示される
    login(client, User.ROLE_CITIZEN, citizen.login_id)
    assert ReportStatus.IN_PROGRESS.label in client.get('/citizen/reports').get_data(as_text=True)


@pytest.mark.parametrize('status, comment, expected', [
    # 未対応 → 完了確認中 は遷移表にない
    (ReportStatus.CONFIRMING.value, '確認します。',
     MSG_INVALID_STATUS.format(current=ReportStatus.RECEIVED.label, status=ReportStatus.CONFIRMING.label)),
    # コメント未入力
    (ReportStatus.IN_PROGRESS.value, '', MSG_COMMENT_REQUIRED),
])
def test_status_update_invalid_transition_rejected(client, login, staff, make_report, status, comment, expected):
    # Arrange
    report = make_report()
    login(client, User.ROLE_STAFF, staff.login_id)
    before = _history_count(report.id)

    # Act
    response = client.post(f'/staff/reports/{report.id}/status', data={'status': status, 'comment': comment})

    # Assert: 詳細へ戻りエラー文が出る。ステータスも履歴も変わらない
    assert response.status_code == 302
    html = client.get(response.headers['Location']).get_data(as_text=True)
    assert expected in html
    db.session.expire_all()
    assert db.session.get(Report, report.id).status == ReportStatus.RECEIVED.value
    assert _history_count(report.id) == before


@pytest.mark.parametrize('path', [
    '/staff/dashboard', '/staff/reports', '/staff/reports/1', '/staff/map', '/staff/ai-analysis',
    '/staff/duplicates', '/staff/stats',
])
def test_citizen_forbidden_on_staff_pages(client, login, citizen, path):
    # Arrange
    login(client, User.ROLE_CITIZEN, citizen.login_id)

    # Act / Assert
    assert client.get(path).status_code == 403


def test_citizen_forbidden_on_staff_status_update(client, login, citizen, make_report):
    # Arrange
    report = make_report()
    login(client, User.ROLE_CITIZEN, citizen.login_id)

    # Act
    response = client.post(f'/staff/reports/{report.id}/status', data={
        'status': ReportStatus.RESOLVED.value, 'comment': 'メモ'})

    # Assert: 403 で、状態は変わらない
    assert response.status_code == 403
    db.session.expire_all()
    assert db.session.get(Report, report.id).status == ReportStatus.RECEIVED.value


@pytest.mark.parametrize('path, role', [
    ('/citizen/mypage', User.ROLE_CITIZEN),
    ('/citizen/reports', User.ROLE_CITIZEN),
    ('/citizen/report', User.ROLE_CITIZEN),
    ('/staff/dashboard', User.ROLE_STAFF),
    ('/staff/reports', User.ROLE_STAFF),
    ('/admin/portal', User.ROLE_ADMIN),
])
def test_anonymous_redirected_to_login(client, path, role):
    # Act
    response = client.get(path)

    # Assert: 該当ロールのログイン画面へ遷移する
    assert response.status_code == 302
    assert response.headers['Location'].endswith(f'/login/{role}')


def test_report_complete_of_other_citizen_is_404(app, client, login, citizen, make_report):
    # Arrange: 別の市民の報告
    other = User(login_id='hanako@example.com', name='佐藤 花子', role=User.ROLE_CITIZEN)
    other.set_password('password123')
    db.session.add(other)
    db.session.commit()
    others_report = make_report(citizen_id=other.id)
    own_report = make_report()
    login(client, User.ROLE_CITIZEN, citizen.login_id)

    # Act / Assert: 他人の報告は 404、自分の報告は 200（対照）
    assert client.get(f'/citizen/reports/{others_report.id}/complete').status_code == 404
    assert client.get(f'/citizen/reports/{own_report.id}/complete').status_code == 200


@pytest.mark.parametrize('path', ['/citizen/mypage', '/citizen/reports', '/citizen/report'])
def test_staff_cannot_use_citizen_pages(client, login, staff, path):
    # Arrange
    login(client, User.ROLE_STAFF, staff.login_id)

    # Act / Assert
    assert client.get(path).status_code == 403
