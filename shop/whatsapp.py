import base64
import logging
import os
import re
import threading
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.db import transaction


logger = logging.getLogger(__name__)

WHATSAPP_ADMIN_NUMBER = 'whatsapp:+2349130273282'
TWILIO_API_TIMEOUT_SECONDS = 5


def send_whatsapp_notification(customer_name, customer_email, message_content):
    account_sid = os.environ.get('TWILIO_ACCOUNT_SID', '').strip()
    auth_token = os.environ.get('TWILIO_AUTH_TOKEN', '').strip()
    whatsapp_from = os.environ.get('TWILIO_WHATSAPP_FROM', '').strip()
    if not account_sid or not auth_token or not whatsapp_from:
        logger.warning('WhatsApp notification skipped: Twilio configuration is incomplete.')
        return False
    if not whatsapp_from.startswith('whatsapp:+'):
        logger.error('WhatsApp notification skipped: TWILIO_WHATSAPP_FROM must start with "whatsapp:+".')
        return False

    preview = re.sub(r'\s+', ' ', message_content or '').strip()
    if len(preview) > 1000:
        preview = preview[:997] + '...'
    body = (
        'New Hoxobil support message\n'
        f'Customer: {customer_name.strip() or "Customer"}\n'
        f'Email: {customer_email.strip() or "Not provided"}\n'
        f'Message: {preview or "(empty message)"}'
    )
    url = f'https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json'
    credentials = base64.b64encode(f'{account_sid}:{auth_token}'.encode('utf-8')).decode('ascii')
    request = Request(
        url,
        data=urlencode({
            'From': whatsapp_from,
            'To': WHATSAPP_ADMIN_NUMBER,
            'Body': body,
        }).encode('utf-8'),
        headers={
            'Authorization': f'Basic {credentials}',
            'Content-Type': 'application/x-www-form-urlencoded',
        },
        method='POST',
    )

    try:
        with urlopen(request, timeout=TWILIO_API_TIMEOUT_SECONDS) as response:
            if 200 <= response.status < 300:
                logger.info('WhatsApp support notification sent.')
                return True
            logger.error('WhatsApp notification failed with HTTP status %s.', response.status)
            return False
    except HTTPError as error:
        logger.error('WhatsApp notification failed with HTTP status %s.', error.code)
    except (URLError, TimeoutError, OSError) as error:
        logger.error('WhatsApp notification request failed: %s', error)
    return False


def _send_notification_in_background(customer_name, customer_email, message_content):
    try:
        send_whatsapp_notification(customer_name, customer_email, message_content)
    except Exception:
        logger.exception('Unexpected error while sending a WhatsApp support notification.')


def queue_whatsapp_notification(customer_name, customer_email, message_content):
    def start_worker():
        worker = threading.Thread(
            target=_send_notification_in_background,
            args=(customer_name, customer_email, message_content),
            name='hoxobil-whatsapp-notification',
            daemon=True,
        )
        try:
            worker.start()
        except RuntimeError:
            logger.exception('Could not start the WhatsApp notification worker.')

    transaction.on_commit(start_worker)
