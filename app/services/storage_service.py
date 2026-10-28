# 写真ファイルの検証（拡張子・サイズ）と保存。保存先を変える場合はここだけ修正する
# 保存先は config の UPLOAD_FOLDER（instance/uploads）。DB にはそこからの相対パスを保存する
import io
import secrets
from dataclasses import dataclass
from pathlib import Path

from flask import current_app
from PIL import Image, UnidentifiedImageError

from app.constants import MSG_PHOTO_INVALID, MSG_PHOTO_TOO_LARGE
from app.services import ServiceError

# 1 枚あたりの上限
MAX_PHOTO_MB = 10

# 受け付ける画像形式（Pillow が判定した形式 → MIME タイプ・拡張子）。ファイル名や送信側の MIME は信用しない
IMAGE_FORMATS = {
    'JPEG': ('image/jpeg', '.jpg'),
    'PNG': ('image/png', '.png'),
    'WEBP': ('image/webp', '.webp'),
}

# 保存先のフォルダ（UPLOAD_FOLDER の下）
REPORT_DIR = 'reports'
DRAFT_DIR = 'drafts'
ICON_DIR = 'icons'


@dataclass(frozen=True)
class UploadedImage:
    data: bytes
    content_type: str
    extension: str


def read_image(file_storage):
    """アップロードされた画像を読み、形式とサイズを確認する。未選択なら None"""
    if file_storage is None or not file_storage.filename:
        return None
    data = file_storage.read()
    if not data:
        return None
    if len(data) > MAX_PHOTO_MB * 1024 * 1024:
        raise ServiceError(MSG_PHOTO_TOO_LARGE.format(max_mb=MAX_PHOTO_MB))
    try:
        with Image.open(io.BytesIO(data)) as img:
            image_format = img.format
            img.verify()
    except (UnidentifiedImageError, OSError, SyntaxError):
        raise ServiceError(MSG_PHOTO_INVALID)
    if image_format not in IMAGE_FORMATS:
        raise ServiceError(MSG_PHOTO_INVALID)
    content_type, extension = IMAGE_FORMATS[image_format]
    return UploadedImage(data=data, content_type=content_type, extension=extension)


def save(image, folder):
    """画像をランダムな名前で保存し、UPLOAD_FOLDER からの相対パス（例: reports/ab12….jpg）を返す"""
    relative = f'{folder}/{secrets.token_hex(16)}{image.extension}'
    path = path_of(relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(image.data)
    return relative


def move(relative, folder):
    """保存済みのファイルを別のフォルダへ移し、新しい相対パスを返す"""
    new_relative = f'{folder}/{Path(relative).name}'
    new_path = path_of(new_relative)
    new_path.parent.mkdir(parents=True, exist_ok=True)
    path_of(relative).replace(new_path)
    return new_relative


def read_bytes(relative):
    return path_of(relative).read_bytes()


def exists(relative):
    return bool(relative) and path_of(relative).is_file()


def delete(relative):
    """ファイルを削除する（ないときは何もしない）"""
    if relative:
        path_of(relative).unlink(missing_ok=True)


def path_of(relative):
    """相対パスを UPLOAD_FOLDER の中の絶対パスにする（フォルダ外を指すパスは拒否）"""
    root = Path(current_app.config['UPLOAD_FOLDER']).resolve()
    path = (root / relative).resolve()
    if root not in path.parents:
        raise ValueError(f'不正なファイルパスです: {relative}')
    return path


def content_type_of(relative):
    extension = Path(relative).suffix.lower()
    return next((ct for ct, ext in IMAGE_FORMATS.values() if ext == extension), 'application/octet-stream')


def extract_gps(data):
    """写真の EXIF から緯度・経度を取り出す。GPS 情報がなければ None"""
    try:
        with Image.open(io.BytesIO(data)) as img:
            gps = img.getexif().get_ifd(0x8825)  # GPSInfo
    except (UnidentifiedImageError, OSError, SyntaxError):
        return None
    try:
        latitude = _to_degrees(gps[2], gps.get(1, 'N'), 'S')
        longitude = _to_degrees(gps[4], gps.get(3, 'E'), 'W')
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180) or (latitude == 0 and longitude == 0):
        return None
    return latitude, longitude


# EXIF の度・分・秒（有理数）を 10 進の度にする。南緯・西経はマイナス
def _to_degrees(dms, ref, negative_ref):
    degrees, minutes, seconds = (float(v) for v in dms)
    value = degrees + minutes / 60 + seconds / 3600
    return -value if str(ref).strip().upper() == negative_ref else value
