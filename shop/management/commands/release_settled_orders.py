"""
Run this on a schedule (e.g. every 30–60 minutes via cron) to push orders
that have finished their settlement wait to Printful.

    python manage.py release_settled_orders

Example crontab entry (every 30 minutes):

    */30 * * * * cd /path/to/project && /path/to/venv/bin/python manage.py release_settled_orders >> /path/to/logs/release_orders.log 2>&1

If you're on Render, use their Cron Job feature instead of a raw crontab
entry, pointing it at this same command.
"""

import logging

from django.core.management.base import BaseCommand
from django.utils import timezone

from shop.models import Order
from shop.fulfillment import (
    submit_regular_order_to_printful,
    submit_custom_order_to_printful,
    send_custom_order_release_notifications,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Submits PENDING_SETTLEMENT orders to Printful once their settlement window has passed."

    def handle(self, *args, **options):
        due_orders = Order.objects.filter(
            status='PENDING_SETTLEMENT',
            paid=True,
            settlement_release_at__lte=timezone.now(),
        )

        count = due_orders.count()
        if count == 0:
            self.stdout.write("No orders due for release.")
            return

        self.stdout.write(f"Releasing {count} order(s) to Printful...")

        released, failed = 0, 0

        for order in due_orders:
            # Custom design ticket order?
            ticket = getattr(order, 'custom_ticket', None)

            if ticket is not None:
                invoice_amount = ticket.invoice_amount or 0
                success, pod_order_id, error = submit_custom_order_to_printful(
                    order, ticket, invoice_amount
                )
                if not success:
                    # Leave status at PENDING_SETTLEMENT so it's retried next
                    # run, but log loudly — repeated failures need a human.
                    order.status = 'PENDING_SETTLEMENT'
                    order.save(update_fields=['status'])
                    logger.error(
                        "release_settled_orders | Custom order %s failed to release: %s",
                        order.id, error,
                    )
                    self.stderr.write(f"  Order #{order.id} (custom) FAILED: {error}")
                    failed += 1
                else:
                    self.stdout.write(f"  Order #{order.id} (custom) released -> PF #{pod_order_id}")
                    released += 1

                send_custom_order_release_notifications(
                    order, ticket, invoice_amount, pod_order_id, error
                )

            else:
                success, error = submit_regular_order_to_printful(order)
                if not success:
                    order.status = 'PENDING_SETTLEMENT'
                    order.save(update_fields=['status'])
                    logger.error(
                        "release_settled_orders | Order %s failed to release: %s",
                        order.id, error,
                    )
                    self.stderr.write(f"  Order #{order.id} FAILED: {error}")
                    failed += 1
                else:
                    self.stdout.write(f"  Order #{order.id} released.")
                    released += 1

        self.stdout.write(
            self.style.SUCCESS(f"Done. Released: {released}, Failed (will retry): {failed}")
        )