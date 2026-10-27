# 報告の作成（写真保存 → AI判定 → 重複チェック）、市民別一覧、職員向け検索・絞り込み


# 撮影した写真から推定した報告場所の住所
# TODO: 現在は画面遷移の確認用にサンプル住所を返す。写真の位置情報（GPS）や AI による推定ができたら置き換える
def estimate_address():
    return '埼玉県さいたま市大宮区高鼻町1丁目 付近'


# 市民が自分で報告した内容の一覧（新しい順）
# TODO: Report モデル・constants.py ができたら DB から取得する。
#       status も DB のコード（received など）にして、表示ラベルは constants.py から取る
def list_by_citizen(user_id):
    return [
        {'title': '道路の穴', 'location': '〇〇町1丁目付近', 'date': '2026/10/01 10:23', 'status': '対応中'},
        {'title': '公園の遊具の破損', 'location': '〇〇公園', 'date': '2026/09/28 15:40', 'status': '未対応'},
        {'title': '街灯の故障', 'location': '〇〇町3丁目', 'date': '2026/09/20 09:15', 'status': '対応済み'},
    ]


# 職員向けの報告一覧（report_id 指定時はその1件、search 指定時はタイトルの部分一致で絞り込む）
# TODO: Report モデル・constants.py ができたら DB から取得し、検索・絞り込み・ページングをクエリで行う
def list_for_staff(search='', report_id=None):
    reports = [
        {'id': 1, 'title': '道路の大きな穴', 'category': '道路', 'location': 'さいたま市大宮区桜木町1丁目', 'urgency': '高', 'status': '未対応', 'date': '2026/10/15 10:00'},
        {'id': 2, 'title': '公園の遊具破損', 'category': '公園', 'location': '〇〇公園', 'urgency': '高', 'status': '未対応', 'date': '2026/10/14 15:30'},
        {'id': 3, 'title': '街灯の球切れ', 'category': '街灯', 'location': '〇〇町3丁目', 'urgency': '中', 'status': '対応中', 'date': '2026/10/12 09:15'},
        {'id': 4, 'title': '道路の落書き', 'category': 'その他', 'location': '〇〇市2丁目', 'urgency': '低', 'status': '対応済み', 'date': '2026/10/15 10:05'},
    ]
    return _filter_by_title(reports, search, report_id)


# 職員向けの報告詳細（報告内容・AI判定・対応履歴）。存在しない場合は None
# TODO: Report・AiAnalysis・ReportStatusHistory モデルができたら DB から取得する。
#       写真は instance/uploads/ から権限チェック付きのルートで配信する（現状は写真なし）
def get_for_staff(report_id):
    reports = {
        1: {
            'id': 1, 'title': '道路の大きな穴', 'category': '道路', 'urgency': '高',
            'address': 'さいたま市大宮区桜木町1丁目', 'status': '未対応', 'date': '2026/10/15 10:00',
            'department': '道路管理課', 'ai_confidence': '98%',
            'description': '歩道の近くの車道に直径約30cm、深さ10cmほどの穴が開いています。自転車や歩行者が足を取られる可能性があり危険です。',
            'image_url': '',
            'history': [{'date': '2026/10/15 10:00', 'user': '市民 Aさん', 'action': '新規報告を受信（AI自動判定完了）'}],
        },
        2: {
            'id': 2, 'title': '公園の遊具破損', 'category': '公園', 'urgency': '高',
            'address': '〇〇公園', 'status': '未対応', 'date': '2026/10/14 15:30',
            'department': '公園緑地課', 'ai_confidence': '95%',
            'description': 'ブランコの鎖が片方ちぎれており、子供が遊ぶ際に大変危険な状態です。',
            'image_url': '',
            'history': [{'date': '2026/10/14 15:30', 'user': '市民 Bさん', 'action': '新規報告を受信（AI自動判定完了）'}],
        },
        3: {
            'id': 3, 'title': '街灯の球切れ', 'category': '街灯', 'urgency': '中',
            'address': '〇〇町3丁目', 'status': '対応中', 'date': '2026/10/12 09:15',
            'department': '道路照明課', 'ai_confidence': '89%',
            'description': '夜間、十字路付近の街灯が点灯せず、見通しが悪くなっています。',
            'image_url': '',
            'history': [{'date': '2026/10/12 09:15', 'user': '市民 Cさん', 'action': '新規報告を受信'}],
        },
        4: {
            'id': 4, 'title': '道路の落書き', 'category': 'その他', 'urgency': '低',
            'address': '〇〇市2丁目', 'status': '対応済み', 'date': '2026/10/15 10:05',
            'department': '市民生活課', 'ai_confidence': '91%',
            'description': 'ガードレールにスプレーによる落書きが発見されました。',
            'image_url': '',
            'history': [{'date': '2026/10/15 10:05', 'user': '市民 Dさん', 'action': '新規報告を受信'}],
        },
        101: {
            'id': 101, 'title': '道路の大きな穴', 'category': '道路', 'urgency': '高',
            'address': '〇〇市1丁目', 'status': '未対応', 'date': '2026/10/15 13:20',
            'department': '道路管理課', 'ai_confidence': '98%',
            'description': 'AI自動検知システムにより抽出された案件です。車道の中央部に危険な窪みが確認されました。',
            'image_url': '',
            'history': [{'date': '2026/10/15 13:20', 'user': 'AI判定エンジン', 'action': '自動分類・緊急度『高』と判定完了'}],
        },
        102: {
            'id': 102, 'title': '公園の遊具破損', 'category': '公園', 'urgency': '高',
            'address': '〇〇公園', 'status': '未対応', 'date': '2026/10/15 12:45',
            'department': '公園緑地課', 'ai_confidence': '95%',
            'description': 'AI画像判定により遊具（すべり台の手すり部）の破損・亀裂が検知されました。',
            'image_url': '',
            'history': [{'date': '2026/10/15 12:45', 'user': 'AI判定エンジン', 'action': '自動分類・緊急度『高』と判定完了'}],
        },
        103: {
            'id': 103, 'title': '街灯の球切れ', 'category': '街灯', 'urgency': '中',
            'address': '〇〇町3丁目', 'status': '未対応', 'date': '2026/10/15 11:10',
            'department': '道路照明課', 'ai_confidence': '89%',
            'description': 'センサー検知および市民テキスト報告のAI解析により、街灯不点灯と判定されました。',
            'image_url': '',
            'history': [{'date': '2026/10/15 11:10', 'user': 'AI判定エンジン', 'action': '自動分類・緊急度『中』と判定完了'}],
        },
        104: {
            'id': 104, 'title': '道路の落書き', 'category': 'その他', 'urgency': '低',
            'address': '〇〇市2丁目', 'status': '未対応', 'date': '2026/10/15 10:05',
            'department': '市民生活課', 'ai_confidence': '91%',
            'description': '画像解析により壁面へのスプレー塗料による不審な落書きと判定されました。',
            'image_url': '',
            'history': [{'date': '2026/10/15 10:05', 'user': 'AI判定エンジン', 'action': '自動分類・緊急度『低』と判定完了'}],
        },
    }
    return reports.get(report_id)


# 直近の AI 自動判定結果（分類カテゴリ・緊急度・担当部署・信頼度）。絞り込みは list_for_staff と同じ
# TODO: AiAnalysis モデルができたら ai_analyses テーブルから取得する
def list_ai_analyses(search='', report_id=None):
    ai_logs = [
        {'id': 101, 'title': '道路の大きな穴（AI検知）', 'category': '道路', 'department': '道路管理課', 'urgency': '高', 'confidence': '98%', 'status': '判定完了・自動分類済み', 'date': '2026/10/15 13:20'},
        {'id': 102, 'title': '公園遊具の鎖ちぎれ検知', 'category': '公園', 'department': '公園緑地課', 'urgency': '高', 'confidence': '95%', 'status': '判定完了・自動分類済み', 'date': '2026/10/15 12:45'},
        {'id': 103, 'title': '街灯の球切れ', 'category': '街灯', 'department': '道路照明課', 'urgency': '中', 'confidence': '89%', 'status': '判定完了・自動分類済み', 'date': '2026/10/15 11:10'},
        {'id': 104, 'title': '道路の落書き', 'category': 'その他', 'department': '市民生活課', 'urgency': '低', 'confidence': '91%', 'status': '判定完了・自動分類済み', 'date': '2026/10/15 10:05'},
    ]
    return _filter_by_title(ai_logs, search, report_id)


# ID 指定があればその1件、なければタイトルの部分一致（大文字小文字を区別しない）で絞り込む
def _filter_by_title(items, search='', report_id=None):
    if report_id:
        return [item for item in items if item['id'] == report_id]
    if search:
        keyword = search.lower()
        return [item for item in items if keyword in item['title'].lower()]
    return items
