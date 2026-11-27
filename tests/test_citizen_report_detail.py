# 市民の対応状況画面（GĐ4）：対応状況の表示・報告の取り消し・詳細へのリンク・行ロック
import html
from datetime import datetime, timezone

import pytest
from sqlalchemy import event

from app.constants import MSG_REPORT_CANCELLED, MSG_REPORT_CANCEL_NOT_ALLOWED, ReportStatus
from app.extensions import db
from app.models import CitizenNotification, Report, ReportStatusHistory, User
from app.services import ServiceError, report_service, status_service
from tests.conftest import get_csrf_token, make_account

BIGINT_OVER = 2 ** 63


def _histories(report):
    return db.session.query(ReportStatusHistory).filter_by(report_id=report.id).order_by(ReportStatusHistory.id).all()


def _add_history(report, from_status, to_status, minute, **values):
    row = ReportStatusHistory(report_id=report.id, from_status=from_status, to_status=to_status,
                              created_at=datetime(2026, 1, 1, 9, minute, tzinfo=timezone.utc), **values)
    db.session.add(row)
    db.session.commit()
    return row


def _page(client, report_id):
    return html.unescape(client.get(f'/citizen/reports/{report_id}').get_data(as_text=True))


def _login_citizen(client, login, citizen):
    login(client, User.ROLE_CITIZEN, citizen.login_id)


# ---------- status_service.cancel_by_citizen ----------

# 未対応の報告を取り消す：状態・履歴1行（市民）・お知らせは作らない
def test_cancel_by_citizen_succeeds_without_notification(citizen, make_report):
    report = make_report()

    result = status_service.cancel_by_citizen(report.id, citizen_id=citizen.id)

    assert result.status == 'cancelled'
    rows = _histories(report)
    assert len(rows) == 1 and rows[0].changed_by_id == citizen.id
    assert (rows[0].from_status, rows[0].to_status) == ('received', 'cancelled')
    assert db.session.query(CitizenNotification).count() == 0


# 未対応以外は取り消せない：ServiceError、DB は変わらない
@pytest.mark.parametrize('current', ['in_progress', 'resolved', 'not_applicable'])
def test_cancel_rejected_when_not_received(citizen, make_report, current):
    report = make_report(status=current)

    with pytest.raises(ServiceError) as error:
        status_service.cancel_by_citizen(report.id, citizen_id=citizen.id)

    assert str(error.value) == MSG_REPORT_CANCEL_NOT_ALLOWED.format(
        report_id=report.id, status=ReportStatus.label_of(current))
    db.session.refresh(report)
    assert report.status == current and _histories(report) == []


# 取り消し済みの再送：履歴を増やさず報告を返す
def test_cancel_twice_does_not_add_history(citizen, make_report):
    report = make_report()
    status_service.cancel_by_citizen(report.id, citizen_id=citizen.id)

    again = status_service.cancel_by_citizen(report.id, citizen_id=citizen.id)

    assert again.status == 'cancelled' and len(_histories(report)) == 1


# 他人の報告・範囲外の ID は None で、他人の報告は変わらない
@pytest.mark.parametrize('target', ['other_user', 'missing', 'over_bigint'])
def test_cancel_returns_none_for_unavailable_report(citizen, make_report, target):
    other = make_account('other@example.com', User.ROLE_CITIZEN)
    mine_of_other = make_report(citizen_id=other.id)
    report_id = {'other_user': mine_of_other.id, 'missing': 999999, 'over_bigint': BIGINT_OVER}[target]

    assert status_service.cancel_by_citizen(report_id, citizen_id=citizen.id) is None

    db.session.refresh(mine_of_other)
    assert mine_of_other.status == 'received' and _histories(mine_of_other) == []


# ---------- report_service.get_for_citizen ----------

# 全項目が揃っている報告
def test_get_for_citizen_returns_all_fields(citizen, make_report, road):
    report = make_report(category_id=road.id, department_id=road.default_department.id)

    data = report_service.get_for_citizen(report.id, citizen.id)

    assert {'id', 'title', 'description', 'location', 'date', 'status_code', 'status', 'category',
            'urgency', 'department', 'can_cancel', 'histories'} <= set(data)
    assert (data['id'], data['title'], data['location']) == (report.id, '道路の大きな穴', 'さいたま市大宮区桜木町1丁目')
    assert (data['status_code'], data['status'], data['category'], data['department']) == (
        'received', '未対応', '道路', '道路管理課')


# カテゴリ・部署・緊急度が未設定のときの代替表示
def test_get_for_citizen_fallbacks_when_unset(citizen, make_report):
    report = make_report(urgency=None)

    data = report_service.get_for_citizen(report.id, citizen.id)

    assert data['category'] == '-' and data['urgency'] == '判定失敗' and data['department'] is None


# 履歴は古い順。メモ・理由・職員名・緊急度だけの変更は出さず、公開メッセージは出す
def test_get_for_citizen_timeline_hides_internal_info(citizen, staff, make_report):
    report = make_report(status='in_progress')
    _add_history(report, None, 'received', 0, changed_by_id=citizen.id)
    _add_history(report, 'received', 'in_progress', 1, changed_by_id=staff.id,
                 comment='内部メモ秘密', public_message='現地を確認します')
    _add_history(report, 'in_progress', 'in_progress', 2, changed_by_id=staff.id, comment='メモだけ秘密')
    _add_history(report, 'in_progress', 'in_progress', 3, changed_by_id=staff.id, from_urgency='high',
                 to_urgency='low', urgency_reason='理由秘密')
    _add_history(report, 'in_progress', 'in_progress', 4, changed_by_id=staff.id, public_message='続報です')

    data = report_service.get_for_citizen(report.id, citizen.id)

    assert [h['text'] for h in data['histories']] == [
        '報告を受け付けました',
        '対応状況が「未対応」から「対応中」に変更されました',
        '職員からのメッセージ']
    assert [h['message'] for h in data['histories']] == ['', '現地を確認します', '続報です']
    dumped = str(data)
    assert not any(secret in dumped for secret in ('秘密', staff.name, 'high', 'low'))


# 取り消し可否は 未対応 のときだけ
def test_get_for_citizen_can_cancel_only_when_received(citizen, make_report):
    received = make_report()
    working = make_report(status='in_progress')

    assert report_service.get_for_citizen(received.id, citizen.id)['can_cancel'] is True
    assert report_service.get_for_citizen(working.id, citizen.id)['can_cancel'] is False


# ---------- 行ロック（FOR UPDATE） ----------

def _run_collecting_sql(action):
    statements = []

    def collect(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(db.engine, 'before_cursor_execute', collect)
    try:
        action()
    finally:
        event.remove(db.engine, 'before_cursor_execute', collect)
    return statements


@pytest.mark.parametrize('function', ['cancel_by_citizen', 'update_report_status', 'change_group_status'])
def test_status_changes_lock_rows_with_for_update(citizen, staff, make_report, function):
    from app.models import DuplicateGroup
    group = DuplicateGroup()
    db.session.add(group)
    report = make_report(duplicate_group=group)
    calls = {
        'cancel_by_citizen': lambda: status_service.cancel_by_citizen(report.id, citizen_id=citizen.id),
        'update_report_status': lambda: status_service.update_report_status(
            report.id, 'in_progress', actor_id=staff.id, comment='対応開始'),
        'change_group_status': lambda: status_service.change_group_status(
            group.id, 'in_progress', actor_id=staff.id),
    }

    statements = _run_collecting_sql(calls[function])

    locking = [s for s in statements if 'FROM reports' in s and 'FOR UPDATE' in s]
    assert locking, f'{function} が reports を FOR UPDATE で読んでいません: {statements}'


# ---------- ルート /citizen/reports/<id> ----------

# 本人の報告：各ラベルと値、取り消しボタン、時系列
def test_detail_page_shows_fields_and_cancel_button(client, login, citizen, make_report, road):
    report = make_report(category_id=road.id, department_id=road.default_department.id)
    _add_history(report, None, 'received', 0, changed_by_id=citizen.id)
    _login_citizen(client, login, citizen)

    response = client.get(f'/citizen/reports/{report.id}')
    page = html.unescape(response.get_data(as_text=True))

    assert response.status_code == 200
    for expected in ('道路の大きな穴', f'#{report.id}', '送信日時', '未対応', '道路', '道路管理課',
                     'さいたま市大宮区桜木町1丁目', '車道に穴が開いています。', '対応状況', '報告を受け付けました',
                     'この報告を取り消す', f'/citizen/reports/{report.id}/cancel'):
        assert expected in page
    for label in ('カテゴリ', '緊急度', '担当部署', '発生場所', '内容'):
        assert label in page


# 他人の報告・存在しない報告・bigint 範囲外は 404（取り消せない状態ではボタンも出ない）
@pytest.mark.parametrize('target', ['other_user', 'missing', 'over_bigint'])
def test_detail_page_404_for_unavailable_report(client, login, citizen, make_report, target):
    other = make_account('other@example.com', User.ROLE_CITIZEN)
    theirs = make_report(citizen_id=other.id)
    report_id = {'other_user': theirs.id, 'missing': 999999, 'over_bigint': BIGINT_OVER}[target]
    _login_citizen(client, login, citizen)

    assert client.get(f'/citizen/reports/{report_id}').status_code == 404


# 職員は 403、未ログインはログイン画面へ
def test_detail_page_blocks_staff_and_anonymous(client, login, staff, citizen, make_report):
    report = make_report()
    anonymous = client.get(f'/citizen/reports/{report.id}')
    login(client, User.ROLE_STAFF, staff.login_id)

    assert anonymous.status_code == 302 and '/login' in anonymous.headers['Location']
    assert client.get(f'/citizen/reports/{report.id}').status_code == 403
    assert client.post(f'/citizen/reports/{report.id}/cancel').status_code == 403


# POST 取り消し：詳細へ redirect、flash success、DB 更新、お知らせなし
def test_cancel_route_redirects_with_success_flash(client, login, citizen, make_report):
    report = make_report()
    _login_citizen(client, login, citizen)

    response = client.post(f'/citizen/reports/{report.id}/cancel')

    assert response.status_code == 302 and response.headers['Location'].endswith(f'/citizen/reports/{report.id}')
    page = html.unescape(client.get(response.headers['Location']).get_data(as_text=True))
    assert MSG_REPORT_CANCELLED.format(report_id=report.id, title=report.title) in page
    assert 'この報告を取り消す' not in page
    db.session.refresh(report)
    assert report.status == 'cancelled' and len(_histories(report)) == 1
    assert db.session.query(CitizenNotification).count() == 0


# 対応中は取り消せない：flash danger、DB 変更なし
def test_cancel_route_rejected_when_in_progress(client, login, citizen, make_report):
    report = make_report(status='in_progress')
    _login_citizen(client, login, citizen)

    response = client.post(f'/citizen/reports/{report.id}/cancel')

    assert response.status_code == 302 and response.headers['Location'].endswith(f'/citizen/reports/{report.id}')
    with client.session_transaction() as session:
        assert ('danger', MSG_REPORT_CANCEL_NOT_ALLOWED.format(report_id=report.id, status='対応中')) in \
            session['_flashes']
    db.session.refresh(report)
    assert report.status == 'in_progress' and _histories(report) == []


# 他人の報告の取り消しは 404 で、状態は変わらない
def test_cancel_route_404_for_other_users_report(client, login, citizen, make_report):
    other = make_account('other@example.com', User.ROLE_CITIZEN)
    theirs = make_report(citizen_id=other.id)
    _login_citizen(client, login, citizen)

    assert client.post(f'/citizen/reports/{theirs.id}/cancel').status_code == 404
    db.session.refresh(theirs)
    assert theirs.status == 'received'
    # GET では取り消せない（405）
    assert client.get(f'/citizen/reports/{theirs.id}/cancel').status_code == 405


# CSRF：トークンなしは拒否（302）、ありは取り消せる
def test_cancel_route_requires_csrf_token(csrf_client, csrf_login, citizen, make_report):
    report = make_report()
    csrf_login(csrf_client, User.ROLE_CITIZEN, citizen.login_id)

    csrf_client.post(f'/citizen/reports/{report.id}/cancel')
    db.session.refresh(report)
    assert report.status == 'received'

    token = get_csrf_token(csrf_client, f'/citizen/reports/{report.id}')
    response = csrf_client.post(f'/citizen/reports/{report.id}/cancel', data={'csrf_token': token})
    assert response.status_code == 302
    db.session.refresh(report)
    assert report.status == 'cancelled'


# ---------- 詳細へのリンク ----------

# 報告一覧とマイページの最近の報告から詳細へ
def test_report_list_and_mypage_link_to_detail(client, login, citizen, make_report):
    report = make_report()
    _login_citizen(client, login, citizen)
    link = f'/citizen/reports/{report.id}'

    assert link in client.get('/citizen/reports').get_data(as_text=True)
    assert link in client.get('/citizen/mypage').get_data(as_text=True)


# お知らせから報告の詳細へのリンク（report_id は NOT NULL のため、リンクなしの経路は DB 上は作れない）
def test_notification_links_to_report_detail(client, login, citizen, make_report, make_citizen_notification):
    report = make_report()
    make_citizen_notification(report)
    _login_citizen(client, login, citizen)

    page = client.get('/citizen/notifications').get_data(as_text=True)

    assert f'/citizen/reports/{report.id}' in page and '報告の詳細を見る' in page
