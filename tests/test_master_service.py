# GĐ3b マスタ管理サービス（master_service）と、非表示の部署・カテゴリを外す各所の絞り込みのテスト。テスト DB が必要
from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.constants import (
    MSG_CATEGORY_DEFAULT_DEPARTMENT_INVALID, MSG_CATEGORY_NAME_DEPARTMENT_REQUIRED, MSG_CATEGORY_OTHER_PROTECTED,
    MSG_CATEGORY_RESTORE_DEPARTMENT_HIDDEN, MSG_DEPARTMENT_IN_USE_BY_CATEGORY, MSG_DEPARTMENT_INVALID,
    MSG_DEPARTMENT_NAME_REQUIRED, MSG_MASTER_NAME_INVALID, MSG_MASTER_NAME_TAKEN, MSG_MASTER_NAME_TAKEN_HIDDEN,
    MSG_MASTER_NAME_TOO_LONG, MSG_MASTER_TARGET_NOT_FOUND, ReportStatus,
)
from app.extensions import db
from app.models import Category, Department, Report, User
from app.services import ServiceError, ValidationError, master_service, stats_service, user_service
from app.services.report_service import _classification_choices
from tests.conftest import make_account


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


# ---------------------------------------------------------------- 部署の追加・名前の検証

def test_create_department_trims_and_allows_fullwidth_space_inside(app):
    # Act
    row = master_service.create_department('  環境　推進課 ')

    # Assert
    assert row.name == '環境　推進課'
    assert row.del_flag is False and row.deleted_at is None and row.deleted_by_id is None


def test_create_department_empty_is_danger(app):
    # Act / Assert（全角スペースだけも未入力）
    with pytest.raises(ValidationError) as e:
        master_service.create_department(' 　 ')
    assert e.value.messages == [('danger', MSG_DEPARTMENT_NAME_REQUIRED)]


def test_department_name_length_50_ok_and_51_rejected(app):
    # Act
    master_service.create_department('あ' * 50)
    with pytest.raises(ValidationError) as e:
        master_service.create_department('い' * 51)

    # Assert
    assert e.value.messages == [('warning', MSG_MASTER_NAME_TOO_LONG.format(label='部署名'))]
    assert Department.query.count() == 1


@pytest.mark.parametrize('name', ['a\nb', 'a​b'])
def test_department_name_with_newline_or_zero_width_rejected(app, name):
    with pytest.raises(ValidationError) as e:
        master_service.create_department(name)
    assert e.value.messages == [('warning', MSG_MASTER_NAME_INVALID.format(label='部署名'))]


def test_department_name_duplicate_and_hidden_duplicate_use_different_messages(app, admin):
    # Arrange
    _dept('道路課')
    _dept('隠し課', hidden_by=admin)

    # Act / Assert
    with pytest.raises(ValidationError) as e1:
        master_service.create_department('道路課')
    with pytest.raises(ValidationError) as e2:
        master_service.create_department('隠し課')
    assert e1.value.messages == [('warning', MSG_MASTER_NAME_TAKEN.format(label='部署名', item='部署'))]
    assert e2.value.messages == [('warning', MSG_MASTER_NAME_TAKEN_HIDDEN.format(label='部署名', item='部署'))]


def test_rename_department_to_own_name_succeeds(app):
    # Arrange
    row = _dept('道路課')

    # Act
    result = master_service.rename_department(row.id, '道路課')

    # Assert
    assert result.name == '道路課'


def test_not_found_ids_raise_service_error(app, admin):
    # Act / Assert（存在しない ID・bigint を超える ID でも 500 にならない）
    for call, item in [(lambda: master_service.rename_department(999999, 'x'), '部署'),
                       (lambda: master_service.hide_department(10 ** 30, actor_id=admin.id), '部署'),
                       (lambda: master_service.hide_category(999999, actor_id=admin.id), 'カテゴリ'),
                       (lambda: master_service.restore_category(999999), 'カテゴリ')]:
        with pytest.raises(ServiceError) as e:
            call()
        assert str(e.value) == MSG_MASTER_TARGET_NOT_FOUND.format(item=item)


# ---------------------------------------------------------------- カテゴリの追加・検証

def test_create_category_generates_code_and_keeps_department(app):
    # Arrange
    dept = _dept()

    # Act
    row = master_service.create_category(' 騒音 ', str(dept.id))

    # Assert
    assert row.name == '騒音' and row.default_department_id == dept.id
    assert row.code.startswith('cat_') and len(row.code) == 12
    assert int(row.code[4:], 16) >= 0


def test_create_category_both_empty_is_single_danger(app):
    with pytest.raises(ValidationError) as e:
        master_service.create_category('', '')
    assert e.value.messages == [('danger', MSG_CATEGORY_NAME_DEPARTMENT_REQUIRED)]


def test_create_category_rejects_hidden_department(app, admin):
    # Arrange
    hidden = _dept('隠し課', hidden_by=admin)

    # Act / Assert
    with pytest.raises(ValidationError) as e:
        master_service.create_category('騒音', hidden.id)
    assert e.value.messages == [('warning', MSG_CATEGORY_DEFAULT_DEPARTMENT_INVALID)]
    assert Category.query.count() == 0


def test_update_category_hidden_department_rejected_for_visible_category(app, admin):
    # Arrange
    dept, hidden = _dept('A課'), _dept('隠し課', hidden_by=admin)
    cat = _cat('騒音', dept)

    # Act / Assert
    with pytest.raises(ValidationError) as e:
        master_service.update_category(cat.id, '騒音', hidden.id)
    assert e.value.messages == [('warning', MSG_CATEGORY_DEFAULT_DEPARTMENT_INVALID)]


def test_changing_default_department_does_not_change_existing_reports(app, make_report):
    # Arrange
    old, new = _dept('旧課'), _dept('新課')
    cat = _cat('騒音', old)
    report = make_report(category_id=cat.id, department_id=old.id)

    # Act
    master_service.update_category(cat.id, '騒音', new.id)

    # Assert
    db.session.expire_all()
    assert db.session.get(Category, cat.id).default_department_id == new.id
    assert db.session.get(Report, report.id).department_id == old.id


# ---------------------------------------------------------------- 非表示・再表示の規則

def test_hide_department_blocked_while_visible_category_uses_it(app, admin):
    # Arrange
    dept = _dept()
    _cat('騒音', dept)

    # Act / Assert
    with pytest.raises(ServiceError) as e:
        master_service.hide_department(dept.id, actor_id=admin.id)
    assert str(e.value) == MSG_DEPARTMENT_IN_USE_BY_CATEGORY.format(names='騒音')
    assert db.session.get(Department, dept.id).del_flag is False


def test_hide_department_with_staff_succeeds_and_records_actor(app, admin):
    # Arrange
    dept = _dept()
    staff = make_account('st1', User.ROLE_STAFF, department=dept)

    # Act
    master_service.hide_department(dept.id, actor_id=admin.id)

    # Assert
    row = db.session.get(Department, dept.id)
    assert row.del_flag is True and row.deleted_at is not None and row.deleted_by_id == admin.id
    assert db.session.get(User, staff.id).department_id == dept.id


def test_hide_category_other_is_protected(app, admin):
    # Arrange
    other = _cat('その他', _dept(), code='other')

    # Act / Assert
    with pytest.raises(ServiceError) as e:
        master_service.hide_category(other.id, actor_id=admin.id)
    assert str(e.value) == MSG_CATEGORY_OTHER_PROTECTED.format(name='その他')
    assert db.session.get(Category, other.id).del_flag is False


def test_hide_category_sets_hidden_fields_and_is_idempotent(app, admin):
    # Arrange
    cat = _cat('騒音', _dept())

    # Act
    master_service.hide_category(cat.id, actor_id=admin.id)
    first_at = db.session.get(Category, cat.id).deleted_at
    master_service.hide_category(cat.id, actor_id=admin.id)

    # Assert
    row = db.session.get(Category, cat.id)
    assert row.del_flag is True and row.deleted_by_id == admin.id and row.deleted_at == first_at


def test_restore_category_blocked_while_department_hidden(app, admin):
    # Arrange
    dept = _dept()
    cat = _cat('騒音', dept, hidden_by=admin)
    master_service.hide_department(dept.id, actor_id=admin.id)

    # Act / Assert
    with pytest.raises(ServiceError) as e:
        master_service.restore_category(cat.id)
    assert str(e.value) == MSG_CATEGORY_RESTORE_DEPARTMENT_HIDDEN.format(department='環境課')
    assert db.session.get(Category, cat.id).del_flag is True


def test_restore_department_then_category_clears_hidden_fields(app, admin):
    # Arrange
    dept = _dept(hidden_by=admin)
    cat = _cat('騒音', dept, hidden_by=admin)

    # Act
    master_service.restore_department(dept.id)
    master_service.restore_category(cat.id)

    # Assert
    for row in (db.session.get(Department, dept.id), db.session.get(Category, cat.id)):
        assert row.del_flag is False and row.deleted_at is None and row.deleted_by_id is None


def test_rows_and_counts_reflect_users_categories_reports_and_hidden(app, admin, make_report):
    # Arrange
    dept = _dept()
    hidden_dept = _dept('隠し課', hidden_by=admin)
    cat = _cat('騒音', dept)
    _cat('隠しカテゴリ', dept, hidden_by=admin)
    make_account('st1', User.ROLE_STAFF, department=dept)
    make_report(category_id=cat.id, department_id=dept.id)

    # Act
    visible = master_service.list_department_rows()
    all_rows = master_service.list_department_rows(include_deleted=True)
    categories = master_service.list_category_rows()

    # Assert
    assert [r['name'] for r in visible] == ['環境課']
    assert (visible[0]['user_count'], visible[0]['category_count'], visible[0]['report_count']) == (1, 1, 1)
    assert [r['id'] for r in all_rows] == [dept.id, hidden_dept.id]
    assert [(c['name'], c['report_count']) for c in categories] == [('騒音', 1)]
    assert master_service.count_hidden() == {'departments': 1, 'categories': 1}


# ---------------------------------------------------------------- 非表示を外す各所の絞り込み

def test_list_departments_excludes_hidden_unless_requested(app, admin):
    # Arrange
    visible, hidden = _dept('A課'), _dept('B課', hidden_by=admin)

    # Act / Assert
    assert [d.id for d in user_service.list_departments()] == [visible.id]
    assert [d.id for d in user_service.list_departments(include_deleted=True)] == [visible.id, hidden.id]


def test_create_staff_rejects_hidden_department(app, admin):
    # Arrange
    hidden = _dept('隠し課', hidden_by=admin)

    # Act / Assert
    with pytest.raises(ValidationError) as e:
        user_service.create_staff(login_id='st9', name='職員', password='password123',
                                  confirm_password='password123', department_id=str(hidden.id))
    assert MSG_DEPARTMENT_INVALID in [text for _, text in e.value.messages]
    assert User.query.filter_by(login_id='st9').count() == 0


def test_classification_choices_skip_hidden_categories_and_departments(app, admin):
    # Arrange
    dept = _dept('A課')
    _dept('B課', hidden_by=admin)
    _cat('道路', dept, code='road')
    _cat('隠し', dept, code='gone', hidden_by=admin)

    # Act
    choices = _classification_choices()

    # Assert
    assert [c.code for c in choices.categories] == ['road']
    assert [c.code for c in choices.departments] == [str(dept.id)]


def test_stats_show_hidden_category_only_when_it_has_data(app, admin, make_report):
    # Arrange
    dept = _dept()
    empty = _cat('空の隠し', dept, hidden_by=admin)
    used = _cat('使用中の隠し', dept, hidden_by=admin)
    cancelled_only = _cat('取消のみ隠し', dept, hidden_by=admin)
    make_report(category_id=used.id, department_id=dept.id)
    make_report(category_id=cancelled_only.id, department_id=dept.id, status=ReportStatus.CANCELLED.value)

    # Act
    by_category = [r['category'] for r in stats_service.count_by_category()]
    filter_ids = [cid for cid, _ in stats_service.map_filter_options()['categories']]

    # Assert
    assert '使用中の隠し' in by_category
    assert '空の隠し' not in by_category and '取消のみ隠し' not in by_category
    assert str(used.id) in filter_ids
    assert str(empty.id) not in filter_ids and str(cancelled_only.id) not in filter_ids


# ---------------------------------------------------------------- CHECK 制約

@pytest.mark.parametrize('model, kwargs', [
    (Department, {'name': 'X課'}),
    (Category, {'code': 'x', 'name': 'X'}),
])
def test_check_rejects_hidden_without_time_and_visible_with_time(app, model, kwargs):
    # Arrange
    dept = _dept()
    extra = {'default_department_id': dept.id} if model is Category else {}

    # Act / Assert（非表示なのに日時なし／表示中なのに日時あり）
    db.session.add(model(del_flag=True, **kwargs, **extra))
    with pytest.raises(IntegrityError, match='del_flag_consistent'):
        db.session.commit()
    db.session.rollback()
    db.session.add(model(del_flag=False, deleted_at=datetime.now(timezone.utc), **kwargs, **extra))
    with pytest.raises(IntegrityError, match='del_flag_consistent'):
        db.session.commit()
    db.session.rollback()
