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
        navigator.geolocation.getCurrentPosition(function (position) {
            latitudeInput.value = position.coords.latitude.toFixed(7);
            longitudeInput.value = position.coords.longitude.toFixed(7);
            onDone(true);
        }, function () {
            onDone(false);
        }, { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 });
    }

    // 写真撮影画面：カメラ映像を表示し、「撮影する」で1枚撮影して送信する
    function setupCamera(form) {
        const video = document.getElementById('cameraVideo');
        const canvas = document.getElementById('cameraCanvas');
        const fileInput = document.getElementById('cameraFile');
        const shutter = document.getElementById('cameraShutter');
        let stream = null;

        // 位置情報確認モーダル：許可されたら撮影時の位置情報（写真に GPS 情報がない場合にサーバーで使う）を取得する
        const modalElement = document.getElementById('locationModal');
        const locationModal = new bootstrap.Modal(modalElement);
        function useLocation() {
            locationModal.hide();
            fillLocation(document.getElementById('cameraLatitude'), document.getElementById('cameraLongitude'), function () {});
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

        shutter.addEventListener('click', function (event) {
            // カメラ映像がない、または写真を選択済みならそのまま送信する
            if (!stream || fileInput.files.length > 0) {
                return;
            }
            event.preventDefault();
            canvas.width = video.videoWidth;
            canvas.height = video.videoHeight;
            canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
            shutter.disabled = true;
            canvas.toBlob(function (blob) {
                const transfer = new DataTransfer();
                transfer.items.add(new File([blob], 'camera.jpg', { type: 'image/jpeg' }));
                fileInput.files = transfer.files;
                stream.getTracks().forEach(function (track) { track.stop(); });
                // submit イベントを発生させて navigation.js で送信する（履歴を増やさない）
                form.requestSubmit();
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
        if (latitudeInput.value && longitudeInput.value) {
            status.textContent = '撮影時の位置情報を使用します。';
            return;
        }
        status.textContent = '端末の位置情報を取得しています…';
        fillLocation(latitudeInput, longitudeInput, function (succeeded) {
            status.textContent = succeeded
                ? '端末の位置情報を取得しました。'
                : '位置情報を取得できませんでした。端末の位置情報の利用を許可するか、位置情報付きの写真を添付してください。';
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
