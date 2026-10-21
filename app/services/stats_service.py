# ダッシュボード・統計・地図用の集計


# 地図に表示する報告（緊急度と地図画像上の配置位置 top/left）。'all' 以外を指定するとカテゴリ・緊急度で絞り込む
# TODO: Report モデルができたら緯度・経度を返し、map.js（Leaflet）で描画する
def map_points(category='all', urgency='all'):
    points = [
        {'id': 1, 'title': '道路の大きな穴', 'category': '道路', 'location': 'さいたま市大宮区', 'urgency': '高', 'top': '40%', 'left': '65%'},
        {'id': 2, 'title': '公園の遊具破損', 'category': '公園', 'location': '川越市', 'urgency': '高', 'top': '55%', 'left': '45%'},
        {'id': 3, 'title': '街灯の球切れ', 'category': '街灯', 'location': '所沢市', 'urgency': '中', 'top': '70%', 'left': '35%'},
        {'id': 4, 'title': 'ゴミの不法投棄', 'category': 'その他', 'location': '秩父市', 'urgency': '低', 'top': '60%', 'left': '20%'},
    ]
    if category != 'all':
        points = [p for p in points if p['category'] == category]
    if urgency != 'all':
        points = [p for p in points if p['urgency'] == urgency]
    return points


# 統計画面のサマリー（総件数・解決済み件数・対応完了率・平均対応日数）
# TODO: Report モデルができたら DB から集計する
def summary():
    return {
        'total_reports': 142,
        'resolved_reports': 118,
        'resolution_rate': '83.1%',
        'avg_response_days': '2.4日',
    }


# カテゴリ別の件数と割合
# TODO: Report・Category モデルができたら DB から集計する
def count_by_category():
    return [
        {'category': '道路・インフラ', 'count': 58, 'percentage': '40.8%'},
        {'category': '公園・緑地', 'count': 34, 'percentage': '23.9%'},
        {'category': '街灯・照明', 'count': 26, 'percentage': '18.3%'},
        {'category': 'ゴミ・不法投棄', 'count': 16, 'percentage': '11.3%'},
        {'category': 'その他', 'count': 8, 'percentage': '5.7%'},
    ]
