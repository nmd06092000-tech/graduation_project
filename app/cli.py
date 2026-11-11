# flask コマンド（定期実行するバッチ処理・アカウント運用）。ルートと同じく、処理は service を呼ぶだけにする
import click

from app.services import ServiceError, ValidationError, auth_service, report_service, user_service


def _fail(error):
    """業務エラーをコマンドのエラー（終了コード 1）にする。入力エラーは1行に1メッセージで表示する"""
    if isinstance(error, ValidationError):
        raise click.ClickException('\n'.join(text for _, text in error.messages))
    raise click.ClickException(str(error))


def register_commands(app):
    @app.cli.command('rejudge-urgency')
    def rejudge_urgency():
        """未対応のまま一定時間（AI_REJUDGE_HOURS）たった報告の緊急度を AI で再判定し、職員へ通知する

        EC2 では systemd timer で1時間ごとに実行する（README 参照）。
        """
        count = report_service.rejudge_stale_reports()
        click.echo(f'{count} 件の報告を再判定しました。')

    @app.cli.command('create-admin')
    @click.option('--login-id', default=None, help='管理者ID（@ は使えません）。省略すると入力を求めます')
    @click.option('--name', default=None, help='表示名。省略すると入力を求めます')
    def create_admin(login_id, name):
        """管理者アカウントを作る（パスワードは画面に表示されない入力で、8文字以上）"""
        if login_id is None:
            login_id = click.prompt('管理者ID')
        if name is None:
            name = click.prompt('表示名')
        password = click.prompt('パスワード（8文字以上）', hide_input=True, confirmation_prompt='パスワード（確認）')
        try:
            user = user_service.create_admin(login_id=login_id, name=name, password=password)
        except ServiceError as e:
            _fail(e)
        click.echo(f'管理者アカウント「{user.login_id}」を作成しました。')

    @app.cli.command('set-password')
    @click.argument('login_id')
    def set_password(login_id):
        """アカウントのパスワードを設定し直し、ログインのロックを解除する（職員・管理者・市民のどれでも）"""
        password = click.prompt('新しいパスワード（8文字以上）', hide_input=True,
                                confirmation_prompt='新しいパスワード（確認）')
        try:
            user = auth_service.set_password_by_operator(login_id, password)
        except ServiceError as e:
            _fail(e)
        click.echo(f'「{user.login_id}」のパスワードを変更しました。ログインのロックも解除しました。')
        if not user.is_active:
            click.echo('注意：このアカウントは利用停止中のため、パスワードを変えてもログインできません。')

    @app.cli.command('remove-test-users')
    @click.option('--dry-run', is_flag=True, help='実際には変更せず、結果だけ表示する')
    @click.option('--yes', is_flag=True, help='確認を省略する')
    @click.option('--force', is_flag=True,
                  help='パスワードを変更済みのテストアカウントも対象にする（ID と役割の組み合わせが一致するものだけ: '
                       'taro@example.com=市民 / staff01=職員 / admin01=管理者。役割が違うアカウントは対象外）')
    def remove_test_users(dry_run, yes, force):
        """公開パスワード（password123）のテストアカウントを削除する。報告などのデータがあるものは無効化する

        対象: taro@example.com（市民） / staff01（職員） / admin01（管理者）。有効な管理者が1人も残らない場合は
        何もせず中止する（先に create-admin で管理者を作成してください）。
        """
        if not dry_run and not yes:
            click.confirm('テストアカウント（taro@example.com / staff01 / admin01）を削除または無効化します。よろしいですか？',
                          abort=True)
        try:
            results = user_service.remove_test_users(dry_run=dry_run, force=force)
        except ServiceError as e:
            _fail(e)
        if dry_run:
            click.echo('ドライラン：変更は保存されません。')
        for login_id, action in results:
            click.echo(f'{login_id}: {action}')
        if dry_run:
            click.echo('以上は予定です。実際に実行するには --dry-run を外してください。')
