# メール送信（SMTP + STARTTLS）。MAIL_SERVER が空のときは送信せず、内容をログに出す（開発・SMTP 未設定の EC2）
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import parseaddr

from flask import current_app


class MailError(Exception):
    """メールを送れなかった（message は例外の種類名だけ。宛先・本文・パスワードは入れない）"""


def _is_single_address(to):
    """to が空白・カンマ・セミコロン・改行を含まない、ちょうど 1 件のアドレスなら True"""
    if not isinstance(to, str) or not to:
        return False
    if any(ch.isspace() or ch in ',;' for ch in to):
        return False
    return parseaddr(to)[1] == to


def send_mail(to, subject, body):
    """1 通のテキストメールを送る。失敗したら MailError

    MAIL_SERVER が空なら送らず、宛先・件名・本文（再設定リンクを含む）を WARNING でログに出す
    （本番では運用者がログから取り出せるようにするため WARNING。SMTP を設定したら出なくなる）。
    """
    config = current_app.config
    # 宛先が「ちょうど 1 件のアドレス」でなければ送らない（ログ出力モードでも同じ検査。ヘッダ注入・複数宛先の防止）
    if not _is_single_address(to):
        raise MailError('InvalidRecipient')

    server = config.get('MAIL_SERVER')
    if not server:
        current_app.logger.warning(
            'MAIL_SERVER が未設定のためメールを送信せず、内容をログに出力します 宛先=%s 件名=%s\n%s', to, subject, body)
        return

    try:
        # ヘッダの代入も try 内：件名などに改行があると ValueError になるため MailError に変換する
        message = EmailMessage()
        message['From'] = config['MAIL_FROM']
        message['To'] = to
        message['Subject'] = subject
        message.set_content(body)
        with smtplib.SMTP(server, config['MAIL_PORT'], timeout=config['MAIL_TIMEOUT']) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            if config.get('MAIL_USERNAME'):
                smtp.login(config['MAIL_USERNAME'], config.get('MAIL_PASSWORD', ''))
            smtp.send_message(message)
    except (smtplib.SMTPException, OSError, ValueError) as e:
        # ValueError：宛先・件名に改行が入っているなど、ヘッダとして不正な場合。詳細（認証情報を含みうる）は出さない
        raise MailError(type(e).__name__) from None
