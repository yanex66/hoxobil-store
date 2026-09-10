"""Deprecated automated settlement release command.

Paid orders are intentionally held until an administrator explicitly selects
them in the Order admin and runs ``Send Selected Orders to Printful``.
"""

import logging
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import transaction

from shop.models import Order
from shop.fulfillment import (
    submit_regular_order_to_printful,
    submit_custom_order_to_printful,
    send_custom_order_release_notifications,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Disabled: orders must be released manually from the Order admin."

    def handle(self, *args, **options):
        self.stdout.write(self.style.WARNING(
            'Automated settlement release is disabled. Use the Order admin action instead.'
        ))