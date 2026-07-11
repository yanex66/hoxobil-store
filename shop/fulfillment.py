"""
shop/fulfillment.py

Shared order-fulfillment logic, deliberately written with NO dependency on
Django's `request` object, so the same functions can be called either:

  1. Immediately from a payment callback view (if you ever want instant
     fulfillment again for a specific case), or
  2. Later, headless, from the release_settled_orders management command,
     which runs on a schedule with no HTTP request in play.

Two pipelines exist because two different kinds of orders exist:
  - "Regular" cart orders (real Printful sync-variant products), submitted
    via PodApiClient.create_order().
  - Custom design ticket orders, which build their own Printful payload
    from the ticket's printful_product_id.
"""

import logging

import requests as http_requests
from django.conf import settings
from django.core.mail import send_mail

from .pod_api import PodApiClient
from .models import SupportChat, ChatMessage

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────
#  REGULAR CART ORDERS
# ─────────────────────────────────────────────────────────
def submit_regular_order_to_printful(order):
    """
    Submits a regular (non-custom-ticket) paid order to Printful.
    Returns (success: bool, error_message: str | None).
    On success, sets order.pod_order_id and order.status = 'FULFILLED' and saves.
    """
    try:
        api_client = PodApiClient('PFT')
        pod_result = api_client.create_order(order)

        if 'error' in pod_result:
            logger.error(
                "submit_regular_order_to_printful | Printful failed for order %s: %s",
                order.id, pod_result,
            )
            return False, pod_result.get('error', 'Unknown Printful error')

        pod_order_id = pod_result.get('result', {}).get('id') or pod_result.get('id')
        if pod_order_id:
            order.pod_order_id = str(pod_order_id)
            order.status = 'FULFILLED'
            order.save(update_fields=['pod_order_id', 'status'])
        else:
            order.status = 'POD_SENT'
            order.save(update_fields=['status'])

        return True, None

    except Exception as e:
        logger.error(
            "submit_regular_order_to_printful | Exception for order %s: %s", order.id, e
        )
        return False, str(e)


# ─────────────────────────────────────────────────────────
#  CUSTOM DESIGN TICKET ORDERS
# ─────────────────────────────────────────────────────────
def submit_custom_order_to_printful(order, ticket, invoice_amount):
    """
    Submits a paid custom-design-ticket order to Printful.
    Returns (success: bool, pod_order_id: str | None, error_message: str | None).
    On success, sets order.pod_order_id and order.status = 'FULFILLED', and
    updates ticket.status, saving both.
    """
    if not ticket.printful_product_id:
        return False, None, "No Printful product ID on ticket."

    try:
        printful_headers = {
            'Authorization': f'Bearer {settings.PRINTFUL_ACCESS_TOKEN}',
            'X-PF-Store-Id':  str(settings.PRINTFUL_STORE_ID),
            'Content-Type':   'application/json',
        }

        product_resp = http_requests.get(
            f'https://api.printful.com/store/products/{ticket.printful_product_id}',
            headers=printful_headers,
            timeout=15,
        )
        product_data = product_resp.json()
        variants     = product_data.get('result', {}).get('sync_variants', [])

        if not variants:
            return False, None, "No variants found for Printful product."

        sync_variant_id = variants[0].get('id')

        printful_order_payload = {
            "recipient": {
                "name":         f"{order.first_name} {order.last_name}",
                "address1":     order.address,
                "city":         order.city,
                "state_code":   order.state or '',
                "country_code": order.country,
                "zip":          order.postal_code,
                "email":        order.email,
                "phone":        order.phone or '',
            },
            "items": [{"sync_variant_id": sync_variant_id, "quantity": 1}],
            "retail_costs": {
                "currency": "USD",
                "subtotal": str(invoice_amount),
            },
            "gift": {
                "subject": f"HOXOBIL Custom Order #{order.id}",
                "message": f"Custom {ticket.garment_item} — Order #{order.id}",
            },
        }

        pf_resp = http_requests.post(
            'https://api.printful.com/orders',
            headers=printful_headers,
            json=printful_order_payload,
            timeout=20,
        )
        pf_data = pf_resp.json()

        if pf_resp.status_code in (200, 201) and pf_data.get('code') in (200, 201):
            pod_order_id = str(pf_data.get('result', {}).get('id', ''))
            order.pod_order_id = pod_order_id
            order.status       = 'FULFILLED'
            order.save(update_fields=['pod_order_id', 'status'])

            ticket.status = 'Approved & Ready for Production'
            ticket.save(update_fields=['status'])

            return True, pod_order_id, None

        error = pf_data.get('error', {}).get('message', 'Unknown Printful error')
        logger.error(
            "submit_custom_order_to_printful | Printful failed for order %s: %s",
            order.id, error,
        )
        return False, None, error

    except Exception as e:
        logger.error(
            "submit_custom_order_to_printful | Exception for order %s: %s", order.id, e
        )
        return False, None, str(e)


def send_custom_order_release_notifications(order, ticket, invoice_amount, pod_order_id, printful_error):
    """
    Sends the customer chat message + receipt email + admin email once a
    custom order has been released to Printful (or has failed to release).
    Uses order.user rather than request.user so it works headlessly from
    the management command.
    """
    customer = order.user

    # ── Chat notification ──────────────────────────────────────────────
    chat = SupportChat.objects.filter(user=customer).first()
    if chat:
        if printful_error:
            chat_text = (
                f"⚠️ **Order Update**\n\n"
                f"We're finalising your custom **{ticket.garment_item}** order now. "
                "There was a hiccup submitting it to production — our team has been "
                f"alerted and will resolve this manually. Order reference: **#{order.id}**."
            )
        else:
            chat_text = (
                f"🚀 **Your Order Has Entered Production!**\n\n"
                f"Your custom **{ticket.garment_item}** has now been sent to production. "
                f"Order reference: **#{order.id}**\n\n"
                f"We'll update you here once it ships. Thank you! 🙏"
            )
        ChatMessage.objects.create(chat=chat, sender_type='admin', text=chat_text)

    # ── Customer email ─────────────────────────────────────────────────
    try:
        send_mail(
            subject=f"HOXOBIL — Your Custom Order #{order.id} Is In Production",
            message=(
                f"Hi {order.first_name},\n\n"
                f"Good news — your custom order is now in production:\n\n"
                f"{'─' * 40}\n"
                f"Order ID:        #{order.id}\n"
                f"Item:            Custom {ticket.garment_item}\n"
                f"Garment Color:   {ticket.garment_color or '—'}\n"
                f"Size:            {ticket.garment_size or '—'}\n"
                f"Placement:       {ticket.placement or '—'}\n"
                f"Amount Paid:     ₦{invoice_amount:,}\n"
                f"{'─' * 40}\n\n"
                f"We'll email you again once it ships.\n\n"
                f"— The HOXOBIL Team 🖤"
            ),
            from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'help.hoxobil@gmail.com'),
            recipient_list=[order.email],
            fail_silently=True,
        )
    except Exception as e:
        logger.error(
            "send_custom_order_release_notifications | Customer email failed for order %s: %s",
            order.id, e,
        )

    # ── Admin email ────────────────────────────────────────────────────
    try:
        admin_emails = [e for _, e in getattr(settings, 'ADMINS', [])]
        fallback     = getattr(settings, 'DEFAULT_FROM_EMAIL', 'help.hoxobil@gmail.com')
        recipients   = admin_emails or [fallback]

        if printful_error:
            subject = f"[ACTION REQUIRED] Custom Order #{order.id} — Printful Submission Failed"
            body = (
                f"Settlement window passed but Printful submission failed.\n\n"
                f"Order ID: #{order.id} | Ticket ID: #{ticket.id}\n"
                f"Customer: {customer.get_full_name() or customer.username} ({customer.email})\n"
                f"Garment: {ticket.garment_item} | Invoice: ₦{invoice_amount:,}\n"
                f"Printful ID: {ticket.printful_product_id or 'NOT SET'}\n\n"
                f"Error: {printful_error}\n\nPlease submit manually from admin."
            )
        else:
            subject = f"[HOXOBIL] Custom Order #{order.id} Released & Sent to Printful ✅"
            body = (
                f"Custom order settlement window passed and it was submitted successfully.\n\n"
                f"Order ID: #{order.id} | Ticket ID: #{ticket.id}\n"
                f"Customer: {customer.get_full_name() or customer.username} ({customer.email})\n"
                f"Garment: {ticket.garment_item} | Invoice: ₦{invoice_amount:,}\n"
                f"Printful ID: {ticket.printful_product_id} | PF Order ID: {pod_order_id}\n"
            )

        send_mail(subject=subject, message=body, from_email=fallback,
                  recipient_list=recipients, fail_silently=True)
    except Exception as e:
        logger.error(
            "send_custom_order_release_notifications | Admin email failed for order %s: %s",
            order.id, e,
        )