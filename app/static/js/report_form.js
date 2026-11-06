// 報告フォーム：スマホの位置情報取得・カメラ撮影
// カメラ・位置情報はブラウザの仕様で HTTPS（または localhost）のときだけ使える
(function () {
    'use strict';

    // 端末の位置情報を取得して緯度・経度の hidden 欄に入れる
    function fillLocation(latitudeInput, longitudeInput, onDone) {
        if (!navigator.geolocation) {
            onDone(false);
            return;
        }
        function request(highAccuracy) {
            navigator.geolocation.getCurrentPosition(function (position) {
                latitudeInput.value = position.coords.latitude.toFixed(7);
                longitudeInput.value = position.coords.longitude.toFixed(7);
                onDone(true);
            }, function (error) {
                // 高精度（GPS）で取れない場合は、精度を下げて（Wi-Fi・基地局）1回だけ取り直す。許可されていない場合は取り直さない
                if (highAccuracy && error.code !== error.PERMISSION_DENIED) {
                    request(false);
                    return;
                }
                onDone(false);
            }, { enableHighAccuracy: highAccuracy, timeout: 10000, maximumAge: 60000 });
        }
        request(true);
    }

    // 写真撮影画面：カメラ映像を表示し、「撮影する」で1枚撮影して送信する
    function setupCamera(form) {
        const video = document.getElementById('cameraVideo');
        const canvas = document.getElementById('cameraCanvas');
        const fileInput = document.getElementById('cameraFile');
        const shutter = document.getElementById('cameraShutter');
        let stream = null;
        // 位置情報の取得中は Promise を持つ（取得が終わったら null に戻す）
        let locationPending = null;

        // 位置情報確認モーダル：許可されたら撮影時の位置情報（写真に GPS 情報がない場合にサーバーで使う）を取得する
        // カメラ映像から作る写真（canvas）には EXIF の GPS 情報がないため、この位置情報が撮影地点になる
        const modalElement = document.getElementById('locationModal');
        const locationModal = new bootstrap.Modal(modalElement);
        function useLocation() {
            locationModal.hide();
            locationPending = new Promise(function (resolve) {
                fillLocation(document.getElementById('cameraLatitude'), document.getElementById('cameraLongitude'), resolve);
            }).then(function () {
                locationPending = null;
            });
        }
        // キャンセル・× は報告画面に戻る（履歴を増やさない。navigation.js）
        function cancelLocation() {
            locationModal.hide();
            window.historyNav.go(modalElement.dataset.cancelUrl);
        }
        document.getElementById('cancelLocationBtn').addEventListener('click', cancelLocation);
        document.getElementById('modalCloseBtn').addEventListener('click', cancelLocation);
        // 「今回だけ使用」「オンにする」はどちらもこの撮影で位置情報を使う（許可の保存はブラウザに任せる）
        document.getElementById('onceLocationBtn').addEventListener('click', useLocation);
        document.getElementById('okLocationBtn').addEventListener('click', useLocation);
        locationModal.show();

        // カメラを使えない場合は写真の選択欄（スマホではカメラが起動する）を表示する
        function showFileInput() {
            document.getElementById('cameraFileArea').classList.remove('d-none');
        }

        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || typeof DataTransfer === 'undefined') {
            showFileInput();
            return;
        }
        navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' }, audio: false })
            .then(function (mediaStream) {
                stream = mediaStream;
                video.srcObject = stream;
                video.classList.remove('d-none');
                document.getElementById('cameraPlaceholder').classList.add('d-none');
            })
            .catch(showFileInput);

        // 位置情報の取得が終わってから送信する（終わる前に送ると緯度・経度が空になり、位置情報の確認画面でエラーになる）
        function submitAfterLocation() {
            // submit イベントを発生させて navigation.js で送信する（履歴を増やさない）
            if (!locationPending) {
                form.requestSubmit();
                return;
            }
            const label = shutter.textContent;
            shutter.textContent = '位置情報を取得しています…';
            locationPending.then(function () {
                shutter.textContent = label;
                form.requestSubmit();
            });
        }

        shutter.addEventListener('click', function (event) {
            // カメラ映像がない、または写真を選択済みなら撮影しない
            const capture = stream && fileInput.files.length === 0;
            // 撮影せず、位置情報の取得も終わっていればそのまま送信する
            if (!capture && !locationPending) {
                return;
            }
            event.preventDefault();
            shutter.disabled = true;
            if (!capture) {
                submitAfterLocation();
                return;
            }
            canvas.width = video.videoWidth;
            canvas.height = video.videoHeight;
            canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
            canvas.toBlob(function (blob) {
                const transfer = new DataTransfer();
                transfer.items.add(new File([blob], 'camera.jpg', { type: 'image/jpeg' }));
                fileInput.files = transfer.files;
                stream.getTracks().forEach(function (track) { track.stop(); });
                submitAfterLocation();
            }, 'image/jpeg', 0.9);
        });

        window.addEventListener('pagehide', function () {
            if (stream) {
                stream.getTracks().forEach(function (track) { track.stop(); });
            }
        });
    }

    // 新規報告画面：撮影時の位置情報がなければ、端末の位置情報を緯度・経度に入れる
    function setupReportForm(latitudeInput) {
        const longitudeInput = document.getElementById('longitude');
        const status = document.getElementById('locationStatus');
        // 位置情報がある場合、発生場所が空欄なら送信時にサーバーが位置情報から住所を入れる（report_service.create_report）
        if (latitudeInput.value && longitudeInput.value) {
            status.textContent = '撮影時の位置情報を使用します。発生場所が空欄の場合は位置情報の住所を使用します。';
            return;
        }
        status.textContent = '端末の位置情報を取得しています…';
        fillLocation(latitudeInput, longitudeInput, function (succeeded) {
            // 取得できなくても、送信時にサーバーが発生場所の住所から位置を検索する
            status.textContent = succeeded
                ? '端末の位置情報を取得しました。発生場所が空欄の場合は位置情報の住所を使用します。'
                : '位置情報を取得できませんでした。入力した発生場所の住所から位置を特定します。';
        });
    }

    const cameraForm = document.getElementById('cameraForm');
    const latitudeInput = document.getElementById('latitude');
    if (cameraForm) {
        setupCamera(cameraForm);
    } else if (latitudeInput) {
        setupReportForm(latitudeInput);
    }
})();
