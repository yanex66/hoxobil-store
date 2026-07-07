from shop.models import Product, Category
from django.utils.text import slugify

CATEGORY_MAP = {
    'bucket hat': 'Hats', 'snapback': 'Hats', 'beanie': 'Hats',
    'cap': 'Hats', 'hat': 'Hats',
    'crop top': "Women's Clothing", 'skater dress': "Women's Clothing",
    'dress': "Women's Clothing", 'sports bra': "Women's Clothing",
    'bra': "Women's Clothing",
    'polo shirt': "Men's Clothing", 'polo': "Men's Clothing",
    'crew neck': "Men's Clothing", 'sweatshirt': "Men's Clothing",
    'hoodie': "Men's Clothing", 't-shirt': "Men's Clothing",
    'kids': "Kids' Clothing",
    'jogger': 'Bottoms', 'shorts': 'Bottoms',
    'crossbody': 'Accessories', 'bag': 'Accessories', 'tote': 'Accessories',
}

for product in Product.objects.all():
    name_lower = product.name.lower()
    product.pod_service = 'PFT'
    for keyword, cat_name in CATEGORY_MAP.items():
        if keyword in name_lower:
            try:
                cat = Category.objects.get(name=cat_name)
            except Category.DoesNotExist:
                cat = Category.objects.create(name=cat_name, slug=slugify(cat_name))
            product.category = cat
            print(f'{product.name} -> {cat_name}')
            break
    product.save()

print('Done')
