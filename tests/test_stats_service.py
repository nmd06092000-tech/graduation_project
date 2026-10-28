# ダッシュボード・統計・地図用の集計のテスト
from app.services import stats_service, status_service


# ダッシュボード：取り消し済みは数えない
def test_dashboard_summary(make_report):
    make_report()
    make_report(status='in_progress')
    make_report(status='resolved')
    make_report(status='cancelled')

    assert stats_service.dashboard_summary() == {'received': 1, 'in_progress': 1, 'resolved': 1}


def test_count_by_urgency_and_priority(make_report):
    make_report(title='古い高', urgency='high')
    make_report(title='新しい高', urgency='high')
    make_report(title='対応済みの高', urgency='high', status='resolved')
    make_report(urgency='low')
    make_report(urgency=None)

    counts = {u['code']: (u['count'], u['percentage']) for u in stats_service.count_by_urgency()}
    assert counts == {'high': (3, '75.0%'), 'medium': (0, '0.0%'), 'low': (1, '25.0%')}
    # 本日の優先対応案件：高・未完了・古い順
    assert [r['title'] for r in stats_service.priority_reports()] == ['古い高', '新しい高']


# 地図：未完了のみ、カテゴリ ID・緊急度コードで絞り込み、不正な値は無視
def test_map_points(make_report, road):
    hole = make_report(category_id=road.id, urgency='high')
    make_report(urgency='low')
    make_report(category_id=road.id, status='resolved')

    assert len(stats_service.map_points()) == 2
    assert [p['id'] for p in stats_service.map_points(category=str(road.id), urgency='high')] == [hole.id]
    assert stats_service.map_points(category=str(road.id), urgency='medium') == []
    assert len(stats_service.map_points(category='道路', urgency='高')) == 2
    point = stats_service.map_points(urgency='high')[0]
    # Google Maps 用の緯度・経度と緊急度コード、地図画像（キー未設定時）用の top/left
    assert (point['latitude'], point['longitude'], point['urgency_code']) == (35.906, 139.624, 'high')
    assert point['top'].endswith('%') and point['left'].endswith('%')


# 統計画面：対応完了率と平均対応日数（対応済みになった履歴の日時 − 報告日時）
def test_summary(staff, make_report):
    report = make_report()
    make_report()
    status_service.update_report_status(report.id, 'in_progress', actor_id=staff.id, comment='対応開始')
    status_service.update_report_status(report.id, 'resolved', actor_id=staff.id, comment='補修完了')

    summary = stats_service.summary()

    assert summary['total_reports'] == 2
    assert summary['resolved_reports'] == 1
    assert summary['resolution_rate'] == '50.0%'
    assert summary['avg_response_days'] == '0.0日'


def test_summary_empty(app):
    assert stats_service.summary() == {
        'total_reports': 0, 'resolved_reports': 0, 'resolution_rate': '—', 'avg_response_days': '—'}


def test_count_by_category_and_trend(make_report, road):
    make_report(category_id=road.id)
    make_report()

    rows = stats_service.count_by_category()
    assert rows == [{'category': '道路', 'count': 1, 'percentage': '50.0%'},
                    {'category': '未分類', 'count': 1, 'percentage': '50.0%'}]

    trend = stats_service.monthly_trend()
    assert len(trend['labels']) == 6 and trend['counts'][-1] == 2


# 統計・レポート分析 代替フロー7a-1：CSV は BOM 付き UTF-8 で画面と同じ集計を出力する
def test_export_report_csv(make_report, road):
    make_report(category_id=road.id)

    data, mimetype, filename = stats_service.export_report('csv')

    text = data.decode('utf-8-sig')
    assert data.startswith(b'\xef\xbb\xbf')
    assert mimetype == 'text/csv' and filename.startswith('statistics_report_') and filename.endswith('.csv')
    assert '総報告件数,1件' in text
    assert '道路,1,100.0%' in text
    assert '月別報告件数トレンド' in text


# 統計・レポート分析 基本フロー7：PDF 形式
def test_export_report_pdf(make_report):
    make_report()

    data, mimetype, filename = stats_service.export_report('pdf')

    assert data.startswith(b'%PDF')
    assert mimetype == 'application/pdf' and filename.endswith('.pdf')


def test_export_report_unknown_format(app):
    assert stats_service.export_report('xlsx') is None
