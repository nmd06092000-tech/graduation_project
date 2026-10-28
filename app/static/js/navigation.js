// 画面遷移の履歴管理：親画面と子画面（写真撮影・アイコン設定など）を行き来しても、
// ブラウザの「戻る」で子画面や同じ画面が出てこないようにする
// - <body data-nav-parent="URL"> の画面は子画面。子画面から親画面へは、直前の履歴が親画面なら history.back() で戻る
// - <a data-nav="replace"> は履歴を増やさずに移動する
// - <form data-nav="replace"> は fetch で送信し、サーバーが返した移動先（app/__init__.py）へ履歴を増やさずに移動する
(function () {
    'use strict';

    // 最後に表示した、子画面ではない画面（子画面の直前の履歴）
    const LAST_PAGE_KEY = 'nav.lastPage';

    function getItem(key) {
        try {
            return sessionStorage.getItem(key);
        } catch (e) {
            return null;
        }
    }

    function setItem(key, value) {
        try {
            sessionStorage.setItem(key, value);
        } catch (e) {
            // 保存できない環境では、親画面へも location.replace で移動する
        }
    }

    // 比較用に URL をパスとクエリだけにする
    function pathOf(url) {
        const parsed = new URL(url, window.location.href);
        return parsed.pathname + parsed.search;
    }

    const parentPath = document.body.dataset.navParent ? pathOf(document.body.dataset.navParent) : null;

    // 履歴を増やさずに url へ移動する。子画面から親画面へは、履歴を戻る
    function go(url) {
        if (parentPath && pathOf(url) === parentPath && getItem(LAST_PAGE_KEY) === parentPath) {
            window.history.back();
            return;
        }
        window.location.replace(url);
    }

    window.addEventListener('pageshow', function (event) {
        if (parentPath) {
            return;
        }
        const current = pathOf(window.location.href);
        // 子画面から戻ってきた親画面は、子画面で変わった内容（撮影した写真・メッセージなど）を読み直す
        const navigation = performance.getEntriesByType('navigation')[0];
        const isBack = event.persisted || (navigation && navigation.type === 'back_forward');
        if (isBack && getItem(LAST_PAGE_KEY) === current) {
            document.documentElement.style.visibility = 'hidden';
            window.location.reload();
            return;
        }
        setItem(LAST_PAGE_KEY, current);
    });

    document.addEventListener('click', function (event) {
        const link = event.target.closest('a[data-nav="replace"]');
        // 画面側で止めたクリック・新しいタブで開く操作はそのままにする
        if (!link || event.defaultPrevented || event.button !== 0
            || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) {
            return;
        }
        event.preventDefault();
        go(link.href);
    });

    document.addEventListener('submit', function (event) {
        const form = event.target;
        if (!form.matches('form[data-nav="replace"]') || event.defaultPrevented) {
            return;
        }
        event.preventDefault();
        const data = new FormData(form);
        if (event.submitter && event.submitter.name) {
            data.append(event.submitter.name, event.submitter.value);
        }
        // 二重送信を防ぐ
        const buttons = form.querySelectorAll('button[type="submit"], input[type="submit"]');
        buttons.forEach(function (button) { button.disabled = true; });
        fetch(form.action, {
            method: form.method,
            body: data,
            headers: { 'X-Replace-History': '1' },
            credentials: 'same-origin',
        }).then(function (response) {
            const contentType = response.headers.get('Content-Type') || '';
            if (contentType.indexOf('application/json') !== -1) {
                return response.json().then(function (result) { go(result.redirect); });
            }
            // リダイレクト以外（404・500 などのエラー画面）は、そのまま表示する
            return response.text().then(function (html) {
                document.open();
                document.write(html);
                document.close();
            });
        }).catch(function () {
            buttons.forEach(function (button) { button.disabled = false; });
        });
    });

    window.historyNav = { go: go };
})();
