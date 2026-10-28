// 地図表示：Google Maps に未完了の報告のピンを、市民が入力した発生場所（住所）の位置に表示する
// 住所から位置を取得できない場合は、報告時の緯度・経度に表示する
// 報告データは map.html の <script id="mapReports">（stats_service.map_points の結果）
(function () {
    'use strict';

    // 報告がないときの中心（埼玉県のほぼ中央）とズーム
    const SAITAMA_CENTER = { lat: 35.99, lng: 139.35 };
    const DEFAULT_ZOOM = 10;
    // 報告が1件だけのときのズーム
    const SINGLE_ZOOM = 15;

    // 緊急度コードごとのピンの色と表示名（凡例・従来の地図画像と同じ Bootstrap の色）
    const URGENCY_STYLES = {
        high: { color: 'text-danger', badge: 'bg-danger' },
        medium: { color: 'text-warning', badge: 'bg-warning text-dark' },
        low: { color: 'text-success', badge: 'bg-success' }
    };

    // ピン（Bootstrap Icons の geo-alt-fill）。ピンの先端が報告の位置になる
    function createPin(urgencyCode) {
        const style = URGENCY_STYLES[urgencyCode] || URGENCY_STYLES.low;
        const pin = document.createElement('i');
        pin.className = 'bi bi-geo-alt-fill fs-2 ' + style.color;
        pin.style.display = 'block';
        pin.style.lineHeight = '1';
        pin.style.filter = 'drop-shadow(0 2px 2px rgba(0, 0, 0, 0.4))';
        return pin;
    }

    // 国土地理院の住所検索 API（無料・キー不要）。結果の coordinates は [経度, 緯度]
    const GSI_ADDRESS_SEARCH_URL = 'https://msearch.gsi.go.jp/address-search/AddressSearch?q=';

    // 発生場所（住所）から位置を検索してピンの位置を決める。失敗したら報告時の緯度・経度を使う
    function resolvePosition(report) {
        const fallback = { lat: report.latitude, lng: report.longitude };
        if (!report.location) {
            return Promise.resolve(fallback);
        }
        return fetch(GSI_ADDRESS_SEARCH_URL + encodeURIComponent(report.location))
            .then(function (response) {
                return response.ok ? response.json() : [];
            })
            .then(function (results) {
                const result = results[0];
                if (!result) {
                    return fallback;
                }
                return { lat: result.geometry.coordinates[1], lng: result.geometry.coordinates[0] };
            })
            .catch(function () {
                return fallback;
            });
    }

    // ピンをクリックしたときの吹き出し（件名・住所・危険度・詳細へのリンク）
    // 市民が入力した文字列を含むため、innerHTML は使わず textContent で入れる
    function createInfoContent(report, detailUrl) {
        const box = document.createElement('div');
        box.className = 'small';
        box.style.maxWidth = '260px';

        const title = document.createElement('div');
        title.className = 'fw-bold mb-1';
        title.textContent = report.title;
        box.appendChild(title);

        if (report.location) {
            const location = document.createElement('div');
            location.className = 'text-muted mb-2';
            location.textContent = report.location;
            box.appendChild(location);
        }

        const urgency = document.createElement('span');
        const style = URGENCY_STYLES[report.urgency_code];
        urgency.className = 'badge me-2 ' + (style ? style.badge : 'bg-secondary');
        urgency.textContent = '危険度: ' + (report.urgency || '未判定');
        box.appendChild(urgency);

        const link = document.createElement('a');
        link.href = detailUrl;
        link.className = 'fw-bold';
        link.textContent = '詳細を見る';
        box.appendChild(link);
        return box;
    }

    // Google Maps の読み込み完了時に呼ばれる（map.html の callback=initReportMap）
    window.initReportMap = async function () {
        const mapElement = document.getElementById('reportMap');
        const reports = JSON.parse(document.getElementById('mapReports').textContent);
        // 報告詳細の URL（report_id=0 で作った URL の末尾を報告 ID に置き換える）
        const detailUrlOf = function (id) {
            return mapElement.dataset.detailUrl.replace(/0$/, String(id));
        };

        const { Map, InfoWindow } = await google.maps.importLibrary('maps');
        const { AdvancedMarkerElement } = await google.maps.importLibrary('marker');
        const { LatLngBounds } = await google.maps.importLibrary('core');

        const map = new Map(mapElement, {
            center: SAITAMA_CENTER,
            zoom: DEFAULT_ZOOM,
            mapId: mapElement.dataset.mapId,
            streetViewControl: false,
            mapTypeControl: false
        });
        // 吹き出しは1つを使い回す（別のピンを押すと前の吹き出しは閉じる）
        const infoWindow = new InfoWindow();
        const bounds = new LatLngBounds();
        const positions = await Promise.all(reports.map(resolvePosition));

        reports.forEach(function (report, index) {
            const position = positions[index];
            const marker = new AdvancedMarkerElement({
                map: map,
                position: position,
                content: createPin(report.urgency_code),
                title: report.title + '（' + (report.location || '') + '）'
            });
            marker.addListener('click', function () {
                infoWindow.setContent(createInfoContent(report, detailUrlOf(report.id)));
                infoWindow.open({ map: map, anchor: marker });
            });
            bounds.extend(position);
        });

        // すべてのピンが入るように表示範囲を合わせる
        if (reports.length === 1) {
            map.setCenter(bounds.getCenter());
            map.setZoom(SINGLE_ZOOM);
        } else if (reports.length > 1) {
            map.fitBounds(bounds, 48);
        }
    };

    // API キーが無効・リファラー制限で拒否されたときに Google Maps から呼ばれる
    window.gm_authFailure = function () {
        const mapElement = document.getElementById('reportMap');
        mapElement.innerHTML = '';
        const message = document.createElement('div');
        message.className = 'alert alert-danger m-3';
        message.textContent = 'Google Maps を読み込めませんでした。GOOGLE_MAPS_API_KEY の設定（有効な API・リファラー制限）を確認してください。';
        mapElement.appendChild(message);
    };
})();
