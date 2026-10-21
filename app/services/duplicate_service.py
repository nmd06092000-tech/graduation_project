# 重複報告の検出：同カテゴリ・近距離・未完了・最近の報告を候補として提示


# 重複候補のグループ一覧（グループごとに類似した報告をまとめる）
# ai_detected: AI が検知した報告かどうか（画面で AI分析画面へのリンクに切り替える）
# TODO: Report モデルと utils/geo.py ができたら、条件に合う報告を DB から探して候補を作る。
#       ai_detected の判定方法も Report・AiAnalysis モデルに合わせて決める
def list_candidate_groups():
    return [
        {
            'group_id': 1,
            'title': '大宮区桜木町1丁目付近の道路の穴',
            'category': '道路',
            'similarity': '96%',
            'reports': [
                {'id': 1, 'title': '道路の大きな穴', 'reporter': '市民 Aさん', 'date': '2026/10/15 10:00', 'ai_detected': False},
                {'id': 101, 'title': '道路の大きな穴（AI検知）', 'reporter': 'AIシステム', 'date': '2026/10/15 13:20', 'ai_detected': True},
            ],
        },
        {
            'group_id': 2,
            'title': '〇〇公園の遊具（ブランコ）破損',
            'category': '公園',
            'similarity': '92%',
            'reports': [
                {'id': 2, 'title': '公園の遊具破損', 'reporter': '市民 Bさん', 'date': '2026/10/14 15:30', 'ai_detected': False},
                {'id': 102, 'title': '公園遊具の鎖ちぎれ検知（AI検知）', 'reporter': 'AIシステム', 'date': '2026/10/15 12:45', 'ai_detected': True},
                {'id': 105, 'title': 'ブランコが壊れている（AI検知）', 'reporter': '市民 Cさん', 'date': '2026/10/15 14:10', 'ai_detected': True},
            ],
        },
    ]


# 指定した重複グループを1件取得する。存在しない場合は None
def get_group(group_id):
    return next((g for g in list_candidate_groups() if g['group_id'] == group_id), None)
