# 報告の作成（写真保存 → AI判定 → 重複チェック）、市民別一覧、職員向け検索・絞り込み


# 市民が自分で報告した内容の一覧（新しい順）
# TODO: Report モデル・constants.py ができたら DB から取得する。
#       status も DB のコード（received など）にして、表示ラベルは constants.py から取る
def list_by_citizen(user_id):
    return [
        {'title': '道路の穴', 'location': '〇〇町1丁目付近', 'date': '2026/10/01 10:23', 'status': '対応中'},
        {'title': '公園の遊具の破損', 'location': '〇〇公園', 'date': '2026/09/28 15:40', 'status': '未対応'},
        {'title': '街灯の故障', 'location': '〇〇町3丁目', 'date': '2026/09/20 09:15', 'status': '対応済み'},
    ]
