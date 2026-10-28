# テスト用のアプリ・DB・テストクライアントを用意する共通フィクスチャ
import io
import os

import pytest
from PIL import Image
from werkzeug.datastructures import FileStorage

from app import create_app
from app.constants import Urgency
from app.extensions import db
from app.models import Category, Department, Report, User
from config import TestingConfig


@pytest.fixture
def app(tmp_path):
    """テスト用 DB（TEST_DATABASE_URL）にテーブルを作り、終了後に削除する。写真は一時フォルダに保存する"""
    url = TestingConfig.SQLALCHEMY_DATABASE_URI
    if not url:
        pytest.skip('TEST_DATABASE_URL が未設定のため、DB を使うテストをスキップします')
    # 開発 DB のテーブルを消さないように、同じ接続先は拒否する
    if url == os.environ.get('DATABASE_URL'):
        pytest.exit('TEST_DATABASE_URL に開発 DB（DATABASE_URL）と同じ接続先は指定できません')

    app = create_app(TestingConfig)
    app.config['UPLOAD_FOLDER'] = str(tmp_path / 'uploads')
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def staff(app):
    return _make_user('staff01', '鈴木 健一', User.ROLE_STAFF)


@pytest.fixture
def citizen(app):
    return _make_user('taro@example.com', '山田 太郎', User.ROLE_CITIZEN)


@pytest.fixture
def road(app):
    """カテゴリ「道路」（既定の担当部署: 道路管理課）"""
    department = Department(name='道路管理課')
    category = Category(code='road', name='道路', default_department=department)
    db.session.add(category)
    db.session.commit()
    return category


@pytest.fixture
def make_report(citizen):
    """報告を1件作るヘルパー（指定しない項目はテスト用の既定値）"""
    def _make(**values):
        values = {
            'citizen_id': citizen.id,
            'title': '道路の大きな穴',
            'description': '車道に穴が開いています。',
            'latitude': 35.906,
            'longitude': 139.624,
            'address': 'さいたま市大宮区桜木町1丁目',
            'urgency': Urgency.HIGH.value,
            **values,
        }
        report = Report(**values)
        db.session.add(report)
        db.session.commit()
        return report
    return _make


def jpeg_bytes(gps=None):
    """テスト用の JPEG 画像。gps=(緯度, 経度) を指定すると EXIF に GPS 情報を入れる"""
    exif = Image.Exif()
    if gps:
        def dms(value):
            degrees = int(value)
            minutes = int((value - degrees) * 60)
            return (float(degrees), float(minutes), round((value - degrees - minutes / 60) * 3600, 4))
        exif[0x8825] = {1: 'N', 2: dms(gps[0]), 3: 'E', 4: dms(gps[1])}
    buffer = io.BytesIO()
    Image.new('RGB', (64, 48), (120, 120, 120)).save(buffer, format='JPEG', exif=exif)
    return buffer.getvalue()


def upload(data, filename='photo.jpg'):
    """フォームから送られたファイル（werkzeug の FileStorage）"""
    return FileStorage(stream=io.BytesIO(data), filename=filename, content_type='image/jpeg')


def _make_user(login_id, name, role):
    user = User(login_id=login_id, name=name, role=role)
    user.set_password('password123')
    db.session.add(user)
    db.session.commit()
    return user
