# shop/utils.py
from django.conf import settings
from decimal import Decimal
from djmoney.money import Money
import logging
import resend

from .models import ExchangeRate

logger = logging.getLogger(__name__)


def send_hoxobil_email(to_email, subject, html_content):
    """Send email through Resend's HTTPS API."""
    if not settings.RESEND_API_KEY:
        logger.error("Resend API key missing; email not sent to %s", to_email)
        return None

    resend.api_key = settings.RESEND_API_KEY
    try:
        return resend.Emails.send({
            'from': 'Hoxobil Support <support@hoxobil.store>',
            'to': [to_email],
            'subject': subject,
            'html': html_content,
        })
    except Exception:
        logger.exception("Resend API error while sending email to %s", to_email)
        return None

def _get_rate_table():
    """
    Returns a dict of {currency_code: rate} against USD.

    Prefers live rates stored in the database (refreshed daily by the
    `update_exchange_rates` management command). Falls back to the
    hardcoded settings.CASH_EXCHANGE_BACKEND snapshot for any currency
    missing from the database — e.g. on first deploy before the refresh
    job has run, or if a scheduled refresh fails and the DB is stale for
    a particular currency.
    """
    db_rates = {
        r.currency: float(r.rate)
        for r in ExchangeRate.objects.filter(base_currency='USD')
    }
    db_rates.setdefault('USD', 1.0)

    fallback = settings.CASH_EXCHANGE_BACKEND.get('USD', {})

    # Start from the fallback so every configured currency has *some* rate,
    # then overlay whatever live rates we actually have.
    merged = dict(fallback)
    merged.update(db_rates)
    return merged


def get_converted_money(money_object, target_currency_code):
    """
    Converts a Money object's amount using live exchange rates (falling
    back to settings.CASH_EXCHANGE_BACKEND where live data isn't available)
    and returns a new Money object.
    """
    try:
        base_amount = money_object.amount
        base_currency = str(money_object.currency)
    except AttributeError:
        return Money(amount=Decimal('0.00'), currency=settings.DEFAULT_CURRENCY)

    target_currency_code = str(target_currency_code).upper()

    if base_currency == target_currency_code:
        return money_object

    rates = _get_rate_table()

    if base_currency != 'USD':
        rate_to_usd = rates.get(base_currency, 1.0)
        if rate_to_usd == 0:
            return money_object
        amount_in_usd = base_amount / Decimal(str(rate_to_usd))
    else:
        amount_in_usd = base_amount

    rate_to_target = rates.get(target_currency_code, 1.0)
    converted_amount = amount_in_usd * Decimal(str(rate_to_target))

    return Money(converted_amount, target_currency_code)