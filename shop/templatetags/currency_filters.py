from django import template
from django.conf import settings
from decimal import Decimal
from django.utils.safestring import mark_safe
import ast  # Helper to clean text lists

from shop.utils import _get_rate_table

register = template.Library()

CURRENCY_SYMBOLS = {
    'USD': '$', 'EUR': '€', 'GBP': '£', 'CAD': 'CA$',
    'AUD': 'A$', 'JPY': '¥', 'NGN': '₦',
}

@register.filter
def convert_manual(money_object, target_currency_code):
    # 1. Validation
    try:
        base_amount = money_object.amount
        base_currency = str(money_object.currency)
    except AttributeError:
        # Handle plain numbers/decimals
        try:
            base_amount = Decimal(str(money_object))
            base_currency = settings.DEFAULT_CURRENCY
        except Exception:
            return money_object

    target_currency_code = str(target_currency_code).upper()
    symbol = CURRENCY_SYMBOLS.get(target_currency_code, target_currency_code + ' ')

    # 2. Optimization
    if base_currency == target_currency_code:
        return mark_safe(f"{symbol}{base_amount:,.2f}")

    # 3. Get Rates — live DB rates first, hardcoded settings snapshot as fallback
    # (same source used by shop/utils.get_converted_money, so both paths agree)
    base_rates = _get_rate_table()

    # 4. Conversion Logic
    try:
        usd_to_base_rate = Decimal(str(base_rates.get(base_currency, 1.0)))
        usd_to_target_rate = Decimal(str(base_rates.get(target_currency_code, 1.0)))

        # Step A: Convert Item Price to Default Currency (USD)
        amount_in_default = base_amount / usd_to_base_rate

        # Step B: Convert to Target Currency
        final_amount = amount_in_default * usd_to_target_rate

        return mark_safe(f"{symbol}{final_amount:,.2f}")

    except Exception:
        return mark_safe("Error")


# Add this function to clean image URLs
@register.filter(name='clean_image_url')
def clean_image_url(variant):
    """
    Safely extracts the first image URL from a variant,
    handling Lists, JSON strings, or plain text.
    """
    images = getattr(variant, 'variant_images', None)

    if not images:
        return ""

    # Case 1: It's already a real Python list (['http...'])
    if isinstance(images, list):
        return images[0] if len(images) > 0 else ""

    # Case 2: It's a string (Text Field) like "['http...']"
    if isinstance(images, str):
        images = images.strip()

        if images.startswith("[") and images.endswith("]"):
            try:
                real_list = ast.literal_eval(images)
                if isinstance(real_list, list) and len(real_list) > 0:
                    return real_list[0]
            except Exception:
                pass

        if len(images) > 10:
            return images

    return ""