import django_filters
from .models import Product, Category
from django.db.models import Q
from django.conf import settings
from decimal import Decimal


def to_usd(value, currency_code):
    """Convert a value from the given currency to USD using settings rates."""
    if not value:
        return None
    rates = settings.CASH_EXCHANGE_BACKEND.get('USD', {})
    rate = Decimal(str(rates.get(currency_code, 1.0)))
    if rate == 0:
        return Decimal(str(value))
    return Decimal(str(value)) / rate


class ProductFilter(django_filters.FilterSet):

    # NOTE: category is intentionally excluded here.
    # The view (ProductListView.get_queryset) handles ?category=<slug>
    # directly via qs.filter(category__slug=...) to avoid ModelChoiceFilter
    # silently returning empty results when the slug doesn't resolve to an object.

    # Search — matches ?q=
    q = django_filters.CharFilter(
        method='filter_search',
        label='Search Products'
    )

    # Price filters — values come in user's currency, converted to USD for DB comparison
    price__gte = django_filters.NumberFilter(
        method='filter_price_gte',
        label='Min Price'
    )
    price__lte = django_filters.NumberFilter(
        method='filter_price_lte',
        label='Max Price'
    )

    class Meta:
        model = Product
        fields = ['available']

    def _get_currency(self):
        """Get the active currency from the request session, default USD."""
        request = self.request
        if request:
            return request.session.get('currency_code', 'USD')
        return 'USD'

    def filter_search(self, queryset, name, value):
        if value:
            return queryset.filter(
                Q(name__icontains=value)
            ).distinct()
        return queryset

    def filter_price_gte(self, queryset, name, value):
        currency = self._get_currency()
        usd_value = to_usd(value, currency)
        return queryset.filter(price__gte=usd_value)

    def filter_price_lte(self, queryset, name, value):
        currency = self._get_currency()
        usd_value = to_usd(value, currency)
        return queryset.filter(price__lte=usd_value)