// 県民のお知らせ画面：詳細を開いた未読のお知らせを既読にし、ヘッダーのベルの件数を更新する
(function () {
    'use strict';

    document.addEventListener('DOMContentLoaded', function () {
        const list = document.querySelector('[data-notification-list]');
        if (!list) {
            return;
        }
        const csrfToken = list.dataset.csrfToken;

        // ベルのバッジと「すべて既読にする」ボタンを、サーバーが返した未読件数に合わせる（画面を読み直さないため）
        function showUnreadCount(count) {
            document.querySelectorAll('[data-bell-badge]').forEach(function (badge) {
                const number = badge.querySelector('[data-bell-count]');
                if (number) {
                    number.textContent = count > 99 ? '99+' : String(count);
                }
                badge.hidden = count <= 0;
            });
            if (count <= 0) {
                document.querySelectorAll('[data-read-all-form]').forEach(function (form) {
                    form.hidden = true;
                });
            }
        }

        function markRead(item) {
            // 既読のもの・送信中のものは送らない（開閉を素早く繰り返しても 1 回だけ送る）
            if (!item.classList.contains('is-unread') || item.dataset.reading === '1') {
                return;
            }
            item.dataset.reading = '1';
            fetch(item.dataset.readUrl, {
                method: 'POST',
                headers: { 'X-CSRFToken': csrfToken, 'Accept': 'application/json' },
                credentials: 'same-origin',
            }).then(function (response) {
                if (!response.ok) {
                    throw new Error('read failed');
                }
                return response.json();
            }).then(function (result) {
                item.classList.remove('is-unread');
                item.querySelectorAll('[data-unread-label]').forEach(function (label) { label.remove(); });
                if (typeof result.unread_count === 'number') {
                    showUnreadCount(result.unread_count);
                }
            }).catch(function () {
                // 失敗しても詳細は読める。未読のままにして、次に開いたときにもう一度送る
            }).then(function () {
                delete item.dataset.reading;
            });
        }

        // 詳細を開くとき（Bootstrap の collapse イベントは上位の要素まで伝わる）
        list.addEventListener('show.bs.collapse', function (event) {
            const item = event.target.closest('[data-notification-item]');
            if (item) {
                markRead(item);
            }
        });
    });
})();
