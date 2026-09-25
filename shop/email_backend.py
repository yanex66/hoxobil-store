from django.core.mail.backends.base import BaseEmailBackend
from django.utils.html import escape

from .utils import send_hoxobil_email


class ResendEmailBackend(BaseEmailBackend):
    """Django adapter for framework-generated messages such as password resets."""

    def send_messages(self, email_messages):
        sent = 0
        for message in email_messages or []:
            html_content = next(
                (content for content, mimetype in message.alternatives if mimetype == 'text/html'),
                '<pre style="white-space:pre-wrap;font-family:Arial,sans-serif;">'
                f'{escape(message.body)}</pre>',
            )
            for recipient in message.to:
                if send_hoxobil_email(recipient, message.subject, html_content):
                    sent += 1
        return sent
