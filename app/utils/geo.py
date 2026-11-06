# 緯度経度の距離計算（Haversine）など位置情報の処理
# 住所 ⇔ 緯度・経度の変換は国土地理院の API（無料・キー不要）を使う
#   住所 → 緯度・経度：住所検索 API（msearch.gsi.go.jp/address-search/AddressSearch）
#   緯度・経度 → 住所：逆ジオコーダー API（mreversegeocoder.gsi.go.jp/reverse-geocoder/LonLatToAddress）
import json
import logging
import math
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

# 逆ジオコーダーが返す市区町村コード（muniCd）→ 市区町村名。埼玉県のみ
# 出典：国土地理院 地理院地図の市区町村コード表（maps.gsi.go.jp/js/muni.js）。町村は郡名を省略
SAITAMA_PREFECTURE = '埼玉県'
SAITAMA_MUNICIPALITIES = {
    '11100': 'さいたま市',
    '11101': 'さいたま市西区',
    '11102': 'さいたま市北区',
    '11103': 'さいたま市大宮区',
    '11104': 'さいたま市見沼区',
    '11105': 'さいたま市中央区',
    '11106': 'さいたま市桜区',
    '11107': 'さいたま市浦和区',
    '11108': 'さいたま市南区',
    '11109': 'さいたま市緑区',
    '11110': 'さいたま市岩槻区',
    '11201': '川越市',
    '11202': '熊谷市',
    '11203': '川口市',
    '11206': '行田市',
    '11207': '秩父市',
    '11208': '所沢市',
    '11209': '飯能市',
    '11210': '加須市',
    '11211': '本庄市',
    '11212': '東松山市',
    '11214': '春日部市',
    '11215': '狭山市',
    '11216': '羽生市',
    '11217': '鴻巣市',
    '11218': '深谷市',
    '11219': '上尾市',
    '11221': '草加市',
    '11222': '越谷市',
    '11223': '蕨市',
    '11224': '戸田市',
    '11225': '入間市',
    '11227': '朝霞市',
    '11228': '志木市',
    '11229': '和光市',
    '11230': '新座市',
    '11231': '桶川市',
    '11232': '久喜市',
    '11233': '北本市',
    '11234': '八潮市',
    '11235': '富士見市',
    '11237': '三郷市',
    '11238': '蓮田市',
    '11239': '坂戸市',
    '11240': '幸手市',
    '11241': '鶴ヶ島市',
    '11242': '日高市',
    '11243': '吉川市',
    '11245': 'ふじみ野市',
    '11246': '白岡市',
    '11301': '伊奈町',
    '11324': '三芳町',
    '11326': '毛呂山町',
    '11327': '越生町',
    '11341': '滑川町',
    '11342': '嵐山町',
    '11343': '小川町',
    '11346': '川島町',
    '11347': '吉見町',
    '11348': '鳩山町',
    '11349': 'ときがわ町',
    '11361': '横瀬町',
    '11362': '皆野町',
    '11363': '長瀞町',
    '11365': '小鹿野町',
    '11369': '東秩父村',
    '11381': '美里町',
    '11383': '神川町',
    '11385': '上里町',
    '11408': '寄居町',
    '11442': '宮代町',
    '11464': '杉戸町',
    '11465': '松伏町',
}

# 逆ジオコーダーで町名がない場所の値
_NO_TOWN_NAMES = {'', '-', '－'}


def search_address(address, *, url, timeout=5):
    """住所から緯度・経度 (lat, lng) を検索する。url が空・見つからない・API の失敗時は None

    url は国土地理院の住所検索 API（結果は一致度の高い順、geometry.coordinates は [経度, 緯度]）。
    """
    address = (address or '').strip()
    if not address or not url:
        return None
    results = _get_json(url, {'q': address}, timeout)
    try:
        longitude, latitude = (float(v) for v in results[0]['geometry']['coordinates'][:2])
    except (IndexError, KeyError, TypeError, ValueError):
        # 見つからない（空の配列）・API の失敗・想定外の形式
        return None
    if not (math.isfinite(latitude) and math.isfinite(longitude)
            and -90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    return latitude, longitude


def reverse_geocode(latitude, longitude, *, url, timeout=5):
    """緯度・経度から住所（例: 埼玉県さいたま市大宮区桜木町一丁目）を求める

    url が空・海上など住所がない・埼玉県外・API の失敗時は None。
    """
    if not url or latitude is None or longitude is None:
        return None
    data = _get_json(url, {'lat': f'{latitude:.7f}', 'lon': f'{longitude:.7f}'}, timeout)
    try:
        result = data['results']
        municipality = SAITAMA_MUNICIPALITIES.get(str(result['muniCd']).strip())
        town = str(result.get('lv01Nm') or '').strip()
    except (KeyError, TypeError, AttributeError):
        # 住所がない（{}）・API の失敗・想定外の形式
        return None
    if municipality is None:
        return None
    return SAITAMA_PREFECTURE + municipality + ('' if town in _NO_TOWN_NAMES else town)


# GET で JSON を取得する。タイムアウト・接続エラー・JSON でない応答は None
def _get_json(url, params, timeout):
    try:
        with urllib.request.urlopen(f'{url}?{urllib.parse.urlencode(params)}', timeout=timeout) as response:
            return json.load(response)
    except (OSError, ValueError) as e:
        # 住所・位置は個人情報になりうるのでログに出さない
        logger.warning('国土地理院 API の呼び出しに失敗しました: %s', type(e).__name__)
        return None
