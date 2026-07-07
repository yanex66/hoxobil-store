import logging
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand
from django.urls import reverse
from django.utils import timezone

from shop.models import Order

logger = logging.getLogger('shop')

# How long to wait after an unpaid order is created before emailing about it.
# Kept here (not settings.py) since this is specific to this one command —
# override with ABANDONED_CART_DELAY_HOURS in settings.py if you want it configurable.
DEFAULT_DELAY_HOURS = 2


class Command(BaseCommand):
    help = "Email customers who started checkout (created an unpaid Order) but never completed payment."

    def add_arguments(self, parser):
        parser.add_argument(
            '--hours',
            type=int,
            default=getattr(settings, 'ABANDONED_CART_DELAY_HOURS', DEFAULT_DELAY_HOURS),
            help="Only email orders older than this many hours (default: 2).",
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help="Print what would be sent without actually sending or marking orders.",
        )

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(hours=options['hours'])

        orders = (
            Order.objects
            .filter(paid=False, user__isnull=False, abandonment_email_sent_at__isnull=True, created__lt=cutoff)
            .select_related('user')
            .prefetch_related('items__product')
        )

        sent = 0
        for order in orders:
            items = list(order.items.all())
            if not items:
                continue

            if not order.email:
                logger.warning("Order %s has no email on file — skipping.", order.id)
                continue

            base_url = getattr(settings, 'PUBLIC_BASE_URL', '') or 'http://127.0.0.1:8000'
            resume_path = reverse('shop:checkout_payment', args=[order.id])
            resume_url = f"{base_url.rstrip('/')}{resume_path}"

            item_lines = '\n'.join(
                f"  - {item.product.name} x{item.quantity}" for item in items
            )

            subject = "You left something in your cart at HOXOBIL"
            message = (
                f"Hi {order.first_name},\n\n"
                f"You started an order with us but didn't finish checking out:\n\n"
                f"{item_lines}\n\n"
                f"Your items are still reserved for you — complete your order here:\n"
                f"{resume_url}\n\n"
                f"If you've already paid or changed your mind, you can ignore this email.\n\n"
                f"— HOXOBIL"
            )

            if options['dry_run']:
                self.stdout.write(f"[DRY RUN] Would email {order.email} for order #{order.id}")
                continue

            try:
                send_mail(
                    subject=subject,
                    message=message,
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[order.email],
                    fail_silently=False,
                )
                order.abandonment_email_sent_at = timezone.now()
                order.save(update_fields=['abandonment_email_sent_at'])
                sent += 1
                self.stdout.write(f"  Emailed {order.email} for order #{order.id}")
            except Exception:
                logger.exception("Failed to send abandonment email for order %s", order.id)

        self.stdout.write(self.style.SUCCESS(f"Sent {sent} abandoned-cart emails."))