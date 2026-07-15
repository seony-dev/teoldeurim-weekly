# -*- coding: utf-8 -*-
"""
Gmail SMTP 발송 공용 모듈.

weekly.py와 send_report.py가 공유한다.
weekly.py나 benchmark.py 코드를 import 하지 않는다 — 독립 모듈로 유지.
표준 라이브러리만 사용 (외부 의존성 없음).
"""

import re
import sys
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from email.utils import formataddr


def parse_recipients(value):
    """RECIPIENT_EMAIL을 쉼표/세미콜론/공백으로 분리해 리스트 반환.

    예: "a@x.com, b@y.com; c@z.com" → ["a@x.com", "b@y.com", "c@z.com"]
    """
    if not value:
        return []
    parts = re.split(r"[,;\s]+", value.strip())
    return [p for p in parts if p and "@" in p]


def build_email_message(env, subject, html_body,
                        attachment_html, attachment_filename,
                        extra_attachments=None):
    """MIME 메시지 객체를 구성 (SMTP 발송은 안 함). 단위 테스트 가능하도록 분리.

    extra_attachments: [(filename, content_str_or_bytes), ...] 또는 None
    반환: (MIMEMultipart msg, recipients list)
    """
    recipients = parse_recipients(env["RECIPIENT_EMAIL"])
    if not recipients:
        raise ValueError(
            f"RECIPIENT_EMAIL에 유효한 주소가 없습니다: {env['RECIPIENT_EMAIL']!r}"
        )

    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = formataddr(("털어드림 자동화", env["GMAIL_ADDRESS"]))
    msg["To"] = ", ".join(recipients)

    body = MIMEMultipart("alternative")
    body.attach(MIMEText(html_body, "html", "utf-8"))
    msg.attach(body)

    if attachment_html is not None and attachment_filename:
        attach = MIMEApplication(attachment_html.encode("utf-8"), _subtype="html")
        attach.add_header("Content-Disposition", "attachment", filename=attachment_filename)
        msg.attach(attach)

    if extra_attachments:
        for fn, content in extra_attachments:
            data = content.encode("utf-8") if isinstance(content, str) else content
            ex = MIMEApplication(data, _subtype="html")
            ex.add_header("Content-Disposition", "attachment", filename=fn)
            msg.attach(ex)

    return msg, recipients


def send_email(env, subject, html_body, attachment_html, attachment_filename,
               extra_attachments=None):
    """Gmail SMTP로 발송. env는 GMAIL_ADDRESS/GMAIL_APP_PASSWORD/RECIPIENT_EMAIL 필요."""
    msg, recipients = build_email_message(
        env, subject, html_body, attachment_html, attachment_filename,
        extra_attachments=extra_attachments,
    )
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
        s.login(env["GMAIL_ADDRESS"], env["GMAIL_APP_PASSWORD"])
        s.sendmail(env["GMAIL_ADDRESS"], recipients, msg.as_string())


def send_error_email(env, error_msg, subject="[털어드림 자동화] ❌ 실행 실패"):
    """에러 발생 시 RECIPIENT_EMAIL로 알림. 메일 자체가 실패하면 그냥 패스."""
    try:
        recipients = parse_recipients(env["RECIPIENT_EMAIL"])
        if not recipients:
            return
        msg = MIMEMultipart()
        msg["Subject"] = subject
        msg["From"] = formataddr(("털어드림 자동화", env["GMAIL_ADDRESS"]))
        msg["To"] = ", ".join(recipients)
        msg.attach(MIMEText(error_msg, "plain", "utf-8"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
            s.login(env["GMAIL_ADDRESS"], env["GMAIL_APP_PASSWORD"])
            s.sendmail(env["GMAIL_ADDRESS"], recipients, msg.as_string())
    except Exception as e:
        print(f"에러 메일 발송 자체 실패: {e}", file=sys.stderr)
