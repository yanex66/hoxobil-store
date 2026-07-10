from django.contrib.sitemaps import Sitemap
from django.urls import reverse
from .models import Product


class ProductSitemap(Sitemap):
    changefreq = 'weekly'
    priority = 0.8

    def items(self):
        return Product.objects.filter(available=True)

    def lastmod(self, obj):
        return obj.updated

    def location(self, obj):
        return reverse('shop:product_detail', kwargs={'slug': obj.slug})


class StaticViewSitemap(Sitemap):
    changefreq = 'monthly'
    priority = 0.5

    def items(self):
        # Add any static view names you want indexed (must exist in shop:urls.py)
        return ['home', 'product_list', 'about', 'contact', 'faq']

    def location(self, item):
        return reverse(f'shop:{item}')