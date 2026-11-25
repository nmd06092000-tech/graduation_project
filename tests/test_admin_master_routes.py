# GĐ3b のルート（結合）テスト：/admin/departments・/admin/categories（権限・PRG・flash・入力保持・非表示も表示）とポータル。
# テスト DB が必要
import html as html_lib
from datetime import datetime, timezone

import pytest
from flask import g

from app.constants import (
    MSG_CATEGORY_CREATED, MSG_CATEGORY_HIDDEN, MSG_CATEGORY_NAME_DEPARTMENT_REQUIRED, MSG_DEPARTMENT_CREATED,
    MSG_DEPARTMENT_HIDDEN, MSG_DEPARTMENT_IN_USE_BY_CATEGORY, MSG_DEPARTMENT_NAME_REQUIRED, MSG_MASTER_NAME_TAKEN,
)
from app.extensions import db
from app.models import Category, Department, User

DEPT_URL = '/admin/departments'
CAT_URL = '/admin/categories'


def _req(client, method, path, **kwargs):
    """リクエストを送る。fixture の app_context に残った g のキャッシュ（ログインユーザー・CSRF）は毎回捨てる"""
    g.pop('_login_user', None)
    g.pop('csrf_token', None)
    return getattr(client, method)(path, **kwargs)


def _post(client, path, **data):
    return _req(client, 'post', path, data=data)


def _get(client, path):
    return _req(client, 'get', path)


def _text(response):
    return html_lib.unescape(response.get_data(as_text=True))


def _flashes(client):
    with client.session_transaction() as session:
        return [tuple(item) for item in session.get('_flashes', [])]


def _admin_client(client, login, admin):
    login(client, User.ROLE_ADMIN, admin.login_id)
    return client


def _dept(name='環境課', hidden_by=None):
    row = Department(name=name)
    if hidden_by:
        row.del_flag, row.deleted_at, row.deleted_by_id = True, datetime.now(timezone.utc), hidden_by.id
    db.session.add(row)
    db.session.commit()
    return row


def _cat(name, dept, code=None, hidden_by=None):
    row = Category(code=code or f'c_{name}', name=name, default_department_id=dept.id)
    if hidden_by:
        row.del_flag, row.deleted_at, row.deleted_by_id = True, datetime.now(timezone.utc), hidden_by.id
    db.session.add(row)
    db.session.commit()
    return row


# ---------------------------------------------------------------- 権限

def _assert_blocked(client, status):
    dept = _dept()
    cat = _cat('騒音', dept)
    calls = [('get', DEPT_URL, {}), ('get', CAT_URL, {}), ('post', DEPT_URL, {'name': '新課'}),
             ('post', f'{DEPT_URL}/{dept.id}/update', {'name': '改名'}), ('post', f'{DEPT_URL}/{dept.id}/restore', {}),
             ('post', CAT_URL, {'name': '新', 'default_department_id': str(dept.id)}),
             ('post', f'{CAT_URL}/{cat.id}/hide', {}), ('post', f'{CAT_URL}/{cat.id}/update', {'name': '改名'})]
    for method, path, data in calls:
        response = _req(client, method, path, **({'data': data} if method == 'post' else {}))
        assert response.status_code == status, (method, path)
    db.session.expire_all()
    assert [d.name for d in Department.query.all()] == ['環境課']
    assert db.session.get(Category, cat.id).name == '騒音' and db.session.get(Category, cat.id).del_flag is False


def test_guest_is_redirected_to_login_on_every_master_route(client):
    _assert_blocked(client, 302)


@pytest.mark.parametrize('role, login_id', [(User.ROLE_STAFF, 'staff01'), (User.ROLE_CITIZEN, 'taro@example.com')])
def test_staff_and_citizen_get_403_on_every_master_route(client, login, staff, citizen, role, login_id):
    # Arrange
    login(client, role, login_id)

    # Act / Assert
    _assert_blocked(client, 403)


# ---------------------------------------------------------------- 部署（PRG・flash・入力保持）

def test_create_department_redirects_to_list_with_success_flash(client, login, admin):
    # Arrange
    _admin_client(client, login, admin)

    # Act
    response = _post(client, DEPT_URL, name=' 環境課 ')

    # Assert
    assert response.status_code == 302 and response.headers['Location'].endswith(DEPT_URL)
    assert _flashes(client) == [('success', MSG_DEPARTMENT_CREATED.format(name='環境課'))]
    assert [d.name for d in Department.query.all()] == ['環境課']
    assert '環境課' in _text(_get(client, DEPT_URL))


def test_create_department_empty_flashes_danger_and_still_redirects(client, login, admin):
    # Arrange
    _admin_client(client, login, admin)

    # Act
    response = _post(client, DEPT_URL, name='   ')

    # Assert
    assert response.status_code == 302 and response.headers['Location'].endswith(DEPT_URL)
    assert _flashes(client) == [('danger', MSG_DEPARTMENT_NAME_REQUIRED)]
    assert Department.query.count() == 0


def test_create_department_duplicate_warns_and_keeps_typed_value_once(client, login, admin):
    # Arrange
    _dept('道路課')
    _admin_client(client, login, admin)

    # Act
    response = _post(client, DEPT_URL, name='道路課')
    page = _text(_get(client, DEPT_URL))
    second = _text(_get(client, DEPT_URL))

    # Assert
    assert response.status_code == 302
    assert MSG_MASTER_NAME_TAKEN.format(label='部署名', item='部署') in page
    assert 'value="道路課"' in page      # 追加フォームに入力値が残る
    assert 'value="道路課"' not in second   # 一度だけ（セッションから消える）


def test_hide_department_blocked_by_category_flashes_danger_and_keeps_show_deleted(client, login, admin):
    # Arrange
    dept = _dept()
    _cat('騒音', dept)
    _admin_client(client, login, admin)

    # Act
    response = _post(client, f'{DEPT_URL}/{dept.id}/hide', show_deleted='1')

    # Assert
    assert response.status_code == 302 and response.headers['Location'].endswith(f'{DEPT_URL}?show_deleted=1')
    assert _flashes(client) == [('danger', MSG_DEPARTMENT_IN_USE_BY_CATEGORY.format(names='騒音'))]
    assert db.session.get(Department, dept.id).del_flag is False


def test_hide_department_success_then_listed_only_with_show_deleted(client, login, admin):
    # Arrange
    dept = _dept('旧課')
    _admin_client(client, login, admin)

    # Act
    response = _post(client, f'{DEPT_URL}/{dept.id}/hide')
    flashes = _flashes(client)
    default_page = _text(_get(client, DEPT_URL))
    shown_page = _text(_get(client, f'{DEPT_URL}?show_deleted=1'))

    # Assert
    assert response.headers['Location'].endswith(DEPT_URL)
    assert flashes == [('success', MSG_DEPARTMENT_HIDDEN.format(name='旧課'))]
    assert 'value="旧課"' not in default_page and '旧課' in shown_page


# ---------------------------------------------------------------- カテゴリ

def test_create_category_success_and_both_empty_danger(client, login, admin):
    # Arrange
    dept = _dept()
    _admin_client(client, login, admin)

    # Act
    ok = _post(client, CAT_URL, name='騒音', default_department_id=str(dept.id))
    ok_flashes = _flashes(client)
    _get(client, CAT_URL)   # flash を消費する
    bad = _post(client, CAT_URL, name='', default_department_id='')

    # Assert
    assert ok.status_code == 302 and bad.status_code == 302
    assert ok_flashes == [('success', MSG_CATEGORY_CREATED.format(name='騒音'))]
    assert _flashes(client) == [('danger', MSG_CATEGORY_NAME_DEPARTMENT_REQUIRED)]
    assert Category.query.one().default_department_id == dept.id


def test_hide_category_keeps_show_deleted_and_records_actor(client, login, admin):
    # Arrange
    cat = _cat('騒音', _dept())
    _admin_client(client, login, admin)

    # Act
    response = _post(client, f'{CAT_URL}/{cat.id}/hide', show_deleted='1')

    # Assert
    assert response.headers['Location'].endswith(f'{CAT_URL}?show_deleted=1')
    assert _flashes(client) == [('success', MSG_CATEGORY_HIDDEN.format(name='騒音'))]
    db.session.expire_all()
    assert db.session.get(Category, cat.id).deleted_by_id == admin.id


def test_category_form_only_offers_visible_departments(client, login, admin):
    # Arrange
    _dept('表示課')
    _dept('隠し課', hidden_by=admin)
    _admin_client(client, login, admin)

    # Act
    page = _text(_get(client, CAT_URL))

    # Assert
    assert '表示課' in page and '隠し課' not in page


# ---------------------------------------------------------------- ポータル

def test_portal_shows_real_category_to_department_mapping_without_hidden(client, login, admin):
    # Arrange
    dept = _dept('環境課')
    _cat('騒音', dept)
    _cat('隠しカテゴリ', dept, hidden_by=admin)
    _admin_client(client, login, admin)

    # Act
    page = _text(_get(client, '/admin/portal'))

    # Assert
    assert '騒音' in page and '環境課' in page and '全1件' in page
    assert '隠しカテゴリ' not in page
