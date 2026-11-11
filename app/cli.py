# flask コマンド（定期実行するバッチ処理）。ルートと同じく、処理は service を呼ぶだけにする
import click

from app.services import report_service


def register_commands(app):
    @app.cli.command('rejudge-urgency')
    def rejudge_urgency():
        """未対応のまま一定時間（AI_REJUDGE_HOURS）たった報告の緊急度を AI で再判定し、職員へ通知する

        EC2 では systemd timer で1時間ごとに実行する（README 参照）。
        """
        count = report_service.rejudge_stale_reports()
        click.echo(f'{count} 件の報告を再判定しました。')
