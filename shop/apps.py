# shop/apps.py

from django.apps import AppConfig

class ShopConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'shop'

    def ready(self):
        # We removed the problematic import that was crashing Python 3.14
        pass