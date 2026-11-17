# 管理者が同時に削除し合ったときも、使える管理者が 0 人にならないこと（delete_account の行ロック）のテスト
# 「別のリクエスト」は別スレッド・別の DB 接続（スレッドごとの app_context）で再現する（test_auth_race.py と同じ考え方）
import threading
import time

import pytest
import sqlalchemy as sa

from app.constants import MSG_ACCOUNT_TARGET_NOT_FOUND, MSG_CANNOT_DELETE_LAST_ADMIN
from app.extensions import db
from app.models import User
from app.services import ServiceError, user_service
from tests.conftest import make_account

JOIN_SECONDS = 20


def _usable_admin_count():
    with db.engine.connect() as connection:
        return connection.execute(sa.text(
            "SELECT count(*) FROM users WHERE role = 'admin' AND is_active AND NOT del_flag")).scalar()


def _delete_in_thread(app, results, key, target_id, actor_id, barrier=None):
    """別スレッド・別の DB セッションで delete_account を呼び、結果（'ok' か 業務エラーの文言）を results に入れる"""
    def run():
        with app.app_context():
            try:
                if barrier is not None:
                    barrier.wait(timeout=JOIN_SECONDS)
                user_service.delete_account(target_id, actor_id=actor_id)
                results[key] = 'ok'
            except ServiceError as error:
                results[key] = str(error)
            except Exception as error:  # テストの失敗として見えるように、型と内容を残す
                results[key] = f'{type(error).__name__}: {error}'
            finally:
                db.session.remove()
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def test_delete_waits_for_admin_row_lock_then_refuses_to_remove_last_admin(app):
    # Arrange: 管理者 A・B。「A が B を削除中」の別トランザクションが、使える管理者の行をロックして保持している
    admin_a = make_account('adm_a', User.ROLE_ADMIN)
    admin_b = make_account('adm_b', User.ROLE_ADMIN)
    a_id, b_id = admin_a.id, admin_b.id
    db.session.remove()
    holder = db.engine.connect()
    transaction = holder.begin()
    holder.execute(sa.text(
        "SELECT id FROM users WHERE role = 'admin' AND is_active AND NOT del_flag ORDER BY id FOR UPDATE"))
    results = {}

    try:
        # Act: B が A を削除しようとする（実行者は B）。ロックが取れず待たされる
        thread = _delete_in_thread(app, results, 'b_deletes_a', target_id=a_id, actor_id=b_id)
        time.sleep(1.0)
        assert thread.is_alive(), '行ロックを待たずに先へ進んだ（削除がロックされていない）'
        assert results == {}

        # 保持側のトランザクション（A が B を削除）を完了させる
        holder.execute(sa.text(
            "UPDATE users SET del_flag = true, deleted_at = now(), deleted_by_id = :a WHERE id = :b"),
            {'a': a_id, 'b': b_id})
        transaction.commit()
    finally:
        if transaction.is_active:
            transaction.rollback()
        holder.close()
    thread.join(JOIN_SECONDS)

    # Assert: ロック解放後に数え直すので、B は最後の管理者 A を削除できない。管理者は 1 人残る
    assert not thread.is_alive()
    assert results == {'b_deletes_a': MSG_CANNOT_DELETE_LAST_ADMIN}
    assert _usable_admin_count() == 1
    assert db.session.get(User, a_id).del_flag is False


@pytest.mark.parametrize('round_number', range(3))
def test_two_admins_deleting_each_other_at_once_leave_exactly_one(app, round_number):
    # Arrange: 管理者 2 人。同時に開始する
    a_id = make_account('adm_a', User.ROLE_ADMIN).id
    b_id = make_account('adm_b', User.ROLE_ADMIN).id
    db.session.remove()
    results = {}
    barrier = threading.Barrier(2, timeout=JOIN_SECONDS)

    # Act: A が B を、B が A を同時に削除する
    threads = [_delete_in_thread(app, results, 'a_deletes_b', b_id, a_id, barrier),
               _delete_in_thread(app, results, 'b_deletes_a', a_id, b_id, barrier)]
    for thread in threads:
        thread.join(JOIN_SECONDS)

    # Assert: 片方だけ成功し、もう片方は「最後の管理者」で拒否される。デッドロックも 500 もない
    assert not any(thread.is_alive() for thread in threads)
    assert sorted(results.values()) == sorted(['ok', MSG_CANNOT_DELETE_LAST_ADMIN])
    assert _usable_admin_count() == 1


def test_three_admins_deleting_each_other_at_once_never_leave_zero(app):
    # Arrange: 管理者 3 人（A・B・C）。A が B を、B が A を同時に削除する（C は残る）
    a_id = make_account('adm_a', User.ROLE_ADMIN).id
    b_id = make_account('adm_b', User.ROLE_ADMIN).id
    make_account('adm_c', User.ROLE_ADMIN)
    db.session.remove()
    results = {}
    barrier = threading.Barrier(2, timeout=JOIN_SECONDS)

    # Act
    threads = [_delete_in_thread(app, results, 'a_deletes_b', b_id, a_id, barrier),
               _delete_in_thread(app, results, 'b_deletes_a', a_id, b_id, barrier)]
    for thread in threads:
        thread.join(JOIN_SECONDS)

    # Assert: どちらの結果でも使える管理者が 1 人以上残る（業務エラー以外の例外は出ない）
    assert not any(thread.is_alive() for thread in threads)
    assert all(value == 'ok' or value == MSG_CANNOT_DELETE_LAST_ADMIN for value in results.values()), results
    assert _usable_admin_count() >= 1
    assert sorted(results) == ['a_deletes_b', 'b_deletes_a']


def test_delete_and_unlock_of_same_account_do_not_deadlock(app):
    # Arrange: 管理者 2 人と職員 1 人
    admin_id = make_account('adm_a', User.ROLE_ADMIN).id
    make_account('adm_b', User.ROLE_ADMIN)
    staff_id = make_account('stf', User.ROLE_STAFF).id
    db.session.remove()
    results = {}
    barrier = threading.Barrier(2, timeout=JOIN_SECONDS)

    def unlock():
        with app.app_context():
            try:
                barrier.wait(timeout=JOIN_SECONDS)
                user_service.unlock_account(staff_id)
                results['unlock'] = 'ok'
            except ServiceError as error:
                results['unlock'] = str(error)
            finally:
                db.session.remove()

    # Act
    unlock_thread = threading.Thread(target=unlock, daemon=True)
    unlock_thread.start()
    delete_thread = _delete_in_thread(app, results, 'delete', staff_id, admin_id, barrier)
    for thread in (unlock_thread, delete_thread):
        thread.join(JOIN_SECONDS)

    # Assert: 両方とも終わる（待ち続けない）。削除は必ず成功し、ロック解除は成功か「対象なし」
    assert not unlock_thread.is_alive() and not delete_thread.is_alive()
    assert results['delete'] == 'ok'
    assert results['unlock'] in ('ok', MSG_ACCOUNT_TARGET_NOT_FOUND)
    assert db.session.get(User, staff_id).del_flag is True
