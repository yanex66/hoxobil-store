import logging
from decimal import Decimal

import requests
from django.conf import settings
from django.core.management.base import BaseCommand

from shop.models import ExchangeRate

logger = logging.getLogger('shop')


class Command(BaseCommand):
    help = "Fetch live exchange rates against DEFAULT_CURRENCY and store them in the database."

    def handle(self, *args, **options):
        base = getattr(settings, 'DEFAULT_CURRENCY', 'USD')
        target_currencies = [c for c in settings.CURRENCIES if c != base]

        rates = self._fetch_rates(base, target_currencies)

        if not rates:
            self.stderr.write(self.style.ERROR(
                "Could not fetch live rates from any provider — "
                "existing rates in the database were left untouched."
            ))
            return

        updated = 0
        for currency, rate in rates.items():
            if currency not in target_currencies:
                continue
            ExchangeRate.objects.update_or_create(
                base_currency=base,
                currency=currency,
                defaults={'rate': Decimal(str(rate))},
            )
            self.stdout.write(f"  {base} -> {currency}: {rate}")
            updated += 1

        self.stdout.write(self.style.SUCCESS(f"Updated {updated} exchange rates."))

    def _fetch_rates(self, base, target_currencies):
        """
        Tries Fixer first (requires a paid plan for non-EUR base currencies),
        then falls back to open.er-api.com, a free API that needs no key and
        supports any base currency directly.
        """
        fixer_key = getattr(settings, 'FIXER_ACCESS_KEY', '')
        if fixer_key:
            try:
                resp = requests.get(
                    'http://data.fixer.io/api/latest',
                    params={
                        'access_key': fixer_key,
                        'base': base,
                        'symbols': ','.join(target_currencies),
                    },
                    timeout=10,
                )
                data = resp.json()
                if data.get('success'):
                    return data['rates']
                logger.warning("Fixer request failed, falling back: %s", data.get('error'))
            except requests.RequestException:
                logger.exception("Fixer request errored, falling back to open.er-api.com")

        try:
            resp = requests.get(f'https://open.er-api.com/v6/latest/{base}', timeout=10)
            data = resp.json()
            if data.get('result') == 'success':
                all_rates = data['rates']
                return {c: all_rates[c] for c in target_currencies if c in all_rates}
            logger.error("open.er-api.com request failed: %s", data)
        except requests.RequestException:
            logger.exception("open.er-api.com request errored")

        return None