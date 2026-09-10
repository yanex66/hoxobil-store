from django.core.management.base import BaseCommand
from shop.models import Category


class Command(BaseCommand):
    help = 'Create or update homepage category navigation items'

    def handle(self, *args, **options):
        categories_data = [
            {'name': 'New Drop', 'slug': 'new-drop'},
            {'name': 'Trending', 'slug': 'trending'},
            {'name': 'Limited', 'slug': 'limited'},
            {'name': 'Outerwear', 'slug': 'outerwear'},
            {'name': 'Editorial', 'slug': 'editorial'},
        ]

        for cat_data in categories_data:
            category, created = Category.objects.get_or_create(
                slug=cat_data['slug'],
                defaults={'name': cat_data['name']}
            )
            if created:
                self.stdout.write(
                    self.style.SUCCESS(
                        f'✓ Created category: {category.name} (slug: {category.slug})'
                    )
                )
            else:
                self.stdout.write(
                    self.style.WARNING(
                        f'~ Category already exists: {category.name} (slug: {category.slug})'
                    )
                )

        self.stdout.write(
            self.style.SUCCESS(
                '\n✓ Homepage categories setup complete!'
            )
        )
