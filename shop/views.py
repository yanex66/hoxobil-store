import logging
import base64
import json as _json
import io
from decimal import Decimal, InvalidOperation
from datetime import timedelta
from django.utils import timezone

from django.views.generic import ListView, DetailView, TemplateView
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.forms import UserCreationForm, PasswordChangeForm
from django.http import JsonResponse, QueryDict
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.db import transaction
import json
import requests as http_requests
from django.utils.text import slugify
from django.urls import reverse
from django.core.mail import send_mail

from django.conf import settings
from django.db.models import Avg
from .models import Product, Order, OrderItem, ProductVariant, VideoAd, Category, CustomOrderRequest, PasswordResetOTP, SupportChat, ChatMessage, DesignSubmission, CustomDesignTicket, Review
from .filters import ProductFilter
from .pod_api import PodApiClient
from .cart import Cart
from .forms import CheckoutForm, ReviewForm
from .ai_bot import bot
from PIL import Image
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────
#  0. HOME / LANDING VIEW
# ─────────────────────────────────────────────────────────
def home(request):
    cart = Cart(request)
    video_ads = VideoAd.objects.filter(is_active=True, placement='HOME').order_by('-id')
    featured_products = Product.objects.filter(available=True, is_customizable=False).order_by('-id')[:4]
    return render(request, 'shop/home.html', {
        'cart': cart,
        'video_ads': video_ads,
        'featured_products': featured_products,
    })


# ─────────────────────────────────────────────────────────
#  1. PRODUCT LIST VIEW
# ─────────────────────────────────────────────────────────
class ProductListView(ListView):
    model = Product
    template_name = 'shop/product_list.html'
    context_object_name = 'products'
    paginate_by = 12

    def get_queryset(self):
        base_qs = Product.objects.filter(available=True, is_customizable=False)
        filter_data = self.request.GET.copy()
        filter_data.pop('category', None)
        self._filter = ProductFilter(filter_data, queryset=base_qs, request=self.request)
        qs = self._filter.qs

        category_slug = self.request.GET.get('category', '').strip()
        if category_slug:
            qs = qs.filter(categories__slug=category_slug)
            logger.debug("ProductListView | category_slug=%r | after filter count=%s", category_slug, qs.count())

        q = self.request.GET.get('q', '').strip()
        if q:
            qs = qs.filter(name__icontains=q)

        price_gte = self.request.GET.get('price__gte', '').strip()
        price_lte = self.request.GET.get('price__lte', '').strip()
        if price_gte:
            try:
                qs = qs.filter(price__gte=float(price_gte))
            except ValueError:
                pass
        if price_lte:
            try:
                qs = qs.filter(price__lte=float(price_lte))
            except ValueError:
                pass

        logger.debug(
            "ProductListView | GET=%s | category_slug=%s | q=%s | qs_count=%s",
            dict(self.request.GET), category_slug, q, qs.count(),
        )
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['filter'] = self._filter
        context['categories'] = Category.objects.all().order_by('name')
        context['cart'] = Cart(self.request)
        context['video_ads'] = VideoAd.objects.filter(is_active=True, placement='LIST').order_by('-id')
        context['filter_debug'] = {
            'GET': dict(self.request.GET),
            'errors': self._filter.errors,
            'count': self._filter.qs.count(),
        }
        return context


# ─────────────────────────────────────────────────────────
#  1b. CUSTOM PRODUCTS VIEW (blank garments customers can customize)
# ─────────────────────────────────────────────────────────
class CustomProductListView(ListView):
    """
    Shows only the blank garments flagged is_customizable=True during sync
    (Printful title was prefixed with 'c#'). These aren't finished listings —
    customers pick one, then get routed into the HOXO chat ticket flow to
    request their own design on it, instead of Add to Cart.
    """
    model = Product
    template_name = 'shop/custom_product_list.html'
    context_object_name = 'products'
    paginate_by = 12

    def get_queryset(self):
        qs = Product.objects.filter(available=True, is_customizable=True)

        category_slug = self.request.GET.get('category', '').strip()
        if category_slug:
            qs = qs.filter(categories__slug=category_slug)

        q = self.request.GET.get('q', '').strip()
        if q:
            qs = qs.filter(name__icontains=q)

        return qs.order_by('name')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['categories'] = Category.objects.filter(
            products__is_customizable=True, products__available=True
        ).distinct().order_by('name')
        context['cart'] = Cart(self.request)
        return context


# ─────────────────────────────────────────────────────────
#  2. PRODUCT DETAIL VIEW
# ─────────────────────────────────────────────────────────
class ProductDetailView(DetailView):
    model = Product
    template_name = 'shop/product_detail.html'
    context_object_name = 'product'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        product = self.get_object()
        variants = product.variants.filter(available=True)
        context['sizes'] = sorted(set(v.size for v in variants if v.size))
        context['colors'] = sorted(set(v.color for v in variants if v.color))
        context['cart'] = Cart(self.request)
        context['video_ads'] = VideoAd.objects.filter(is_active=True, placement='DETAIL').order_by('-id')

        # ── Reviews ──────────────────────────────────────────────────────
        reviews = product.reviews.filter(is_approved=True).select_related('user')
        context['reviews'] = reviews
        context['review_count'] = reviews.count()
        context['average_rating'] = reviews.aggregate(avg=Avg('rating'))['avg'] or 0

        user = self.request.user
        context['user_review'] = None
        context['can_review'] = False

        if user.is_authenticated:
            context['user_review'] = Review.objects.filter(product=product, user=user).first()

            if not context['user_review']:
                has_purchased = OrderItem.objects.filter(
                    order__user=user,
                    order__paid=True,
                    product=product,
                ).exists()
                context['can_review'] = has_purchased

            if context['can_review']:
                context['review_form'] = ReviewForm()

        return context


# ─────────────────────────────────────────────────────────
#  2b. SUBMIT PRODUCT REVIEW (verified purchase enforced here)
# ─────────────────────────────────────────────────────────
@login_required
@require_POST
def submit_review(request, slug):
    product = get_object_or_404(Product, slug=slug)

    # Re-check purchase server-side — never trust that the form was only
    # rendered for eligible users; someone could POST directly.
    purchased_item = OrderItem.objects.filter(
        order__user=request.user,
        order__paid=True,
        product=product,
    ).first()

    if not purchased_item:
        messages.error(request, "You can only review products you've purchased.")
        return redirect('shop:product_detail', slug=product.slug)

    if Review.objects.filter(product=product, user=request.user).exists():
        messages.error(request, "You've already reviewed this product.")
        return redirect('shop:product_detail', slug=product.slug)

    form = ReviewForm(request.POST)
    if form.is_valid():
        review = form.save(commit=False)
        review.product = product
        review.user = request.user
        review.order_item = purchased_item
        review.save()
        messages.success(request, "Thanks for your review!")
    else:
        messages.error(request, "There was a problem with your review — please check the fields and try again.")

    return redirect('shop:product_detail', slug=product.slug)


# ─────────────────────────────────────────────────────────
#  3. PRINTFUL SYNC FUNCTION
# ─────────────────────────────────────────────────────────
PRINTFUL_CATEGORY_MAP = {
    'bucket hat':       'Hats',
    'snapback':         'Hats',
    'beanie':           'Hats',
    'cap':              'Hats',
    'hat':              'Hats',
    'crop top':         "Women's Clothing",
    'skater dress':     "Women's Clothing",
    'dress':            "Women's Clothing",
    'sports bra':       "Women's Clothing",
    'padded bra':       "Women's Clothing",
    'bra':              "Women's Clothing",
    'skirt':            "Women's Clothing",
    'polo shirt':       "Men's Clothing",
    'polo':             "Men's Clothing",
    'crew neck':        "Men's Clothing",
    'crewneck':         "Men's Clothing",
    'sweatshirt':       "Men's Clothing",
    'hoodie':           "Men's Clothing",
    'long sleeve':      "Men's Clothing",
    'longsleeve':       "Men's Clothing",
    't-shirt':          "Men's Clothing",
    'tshirt':           "Men's Clothing",
    'unisex tee':       "Men's Clothing",
    'kids':             "Kids' Clothing",
    'youth':            "Kids' Clothing",
    'toddler':          "Kids' Clothing",
    'infant':           "Kids' Clothing",
    'jogger':           'Bottoms',
    'shorts':           'Bottoms',
    'pants':            'Bottoms',
    'leggings':         'Bottoms',
    'crossbody':        'Accessories',
    'tote':             'Accessories',
    'bag':              'Accessories',
    'backpack':         'Accessories',
    'fanny pack':       'Accessories',
    'phone case':       'Accessories',
    'socks':            'Accessories',
    'mug':              'Home & Lifestyle',
    'pillow':           'Home & Lifestyle',
    'blanket':          'Home & Lifestyle',
    'poster':           'Home & Lifestyle',
    'canvas':           'Home & Lifestyle',
}

def _get_category_for_product(name):
    name_lower = name.lower()
    for keyword, cat_name in PRINTFUL_CATEGORY_MAP.items():
        if keyword in name_lower:
            return cat_name
    return 'Uncategorized'

def sync_printful_products(request):
    if not request.user.is_superuser:
        messages.error(request, "Superuser access required.")
        return redirect('shop:product_list')

    api_client = PodApiClient('PFT')
    products_data = api_client.sync_products()

    if not products_data:
        messages.error(request, "Failed to fetch products from Printful.")
        return redirect('shop:product_list')

    active_pod_ids = []

    for item in products_data:
        pid = str(item['id'])
        active_pod_ids.append(pid)

        title = item.get('name', f'product-{pid}')
        cat_name = _get_category_for_product(title)
        cat_slug = slugify(cat_name)[:100]

        category, _ = Category.objects.get_or_create(
            name=cat_name,
            defaults={'slug': cat_slug}
        )

        base_slug = slugify(title)[:180]
        slug = base_slug
        if Product.objects.filter(slug=slug).exclude(pod_id=pid).exists():
            slug = f"{base_slug}-{pid[-8:]}"

        image_url = item.get('thumbnail_url', '')

        variants_data = item.get('variants', [])
        prices = []
        for v in variants_data:
            try:
                prices.append(Decimal(str(v.get('retail_price', '0'))))
            except Exception:
                pass
        min_price = float(min(prices)) if prices else 0.00

        extracted_print_zones = []
        if variants_data:
            sample_variant_id = str(variants_data[0].get('variant_id') or variants_data[0]['id'])
            catalog_details = api_client.get_catalog_variant_details(sample_variant_id)
            if catalog_details and 'result' in catalog_details:
                placements = catalog_details['result'].get('placements', {})
                extracted_print_zones = list(placements.keys())

        product, _ = Product.objects.update_or_create(
            pod_id=pid,
            defaults={
                'name': title,
                'slug': slug,
                'available': True,
                'image_url': image_url,
                'pod_service': 'PFT',
                'price': min_price,
                'price_currency': 'USD',
                'print_areas': extracted_print_zones,
            }
        )

        if category not in product.categories.all():
            product.categories.add(category)

        valid_variant_ids = []
        for v in variants_data:
            catalog_vid = str(v.get('variant_id') or v['id'])
            valid_variant_ids.append(catalog_vid)

            variant_name = v.get('name', '')
            name_parts = [p.strip() for p in variant_name.split(' / ')]
            size = name_parts[-1] if name_parts else ''
            color = name_parts[1] if len(name_parts) > 2 else (name_parts[0] if len(name_parts) > 1 else '')

            try:
                price = float(Decimal(str(v.get('retail_price', '0'))))
            except Exception:
                price = 0.00

            ProductVariant.objects.update_or_create(
                pod_id=catalog_vid,
                defaults={
                    'product': product,
                    'price': price,
                    'available': v.get('is_enabled', True),
                    'size': size,
                    'color': color,
                }
            )

        stale_variants = product.variants.exclude(pod_id__in=valid_variant_ids)
        for sv in stale_variants:
            try:
                sv.delete()
            except Exception:
                sv.available = False
                sv.save()

    stale_products = Product.objects.exclude(pod_id__in=active_pod_ids)
    for sp in stale_products:
        try:
            sp.delete()
        except Exception:
            sp.available = False
            sp.save()

    messages.success(request, "HOXOBIL Catalog synced from Printful successfully.")
    return redirect('shop:product_list')


# ─────────────────────────────────────────────────────────
#  4. GLOBAL CURRENCY CONVERTER VIEW
# ─────────────────────────────────────────────────────────
def set_currency(request):
    if request.method == 'POST':
        currency_code = request.POST.get('currency_code')
        supported_currencies = ['NGN', 'USD', 'GBP', 'EUR', 'CAD', 'AUD', 'JPY']
        if currency_code in supported_currencies:
            request.session['currency_code'] = currency_code
    return redirect(request.META.get('HTTP_REFERER', '/'))


# ─────────────────────────────────────────────────────────
#  5. USER REGISTRATION VIEW
# ─────────────────────────────────────────────────────────
def register(request):
    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('login')
    else:
        form = UserCreationForm()
    return render(request, 'registration/register.html', {'form': form})


# ─────────────────────────────────────────────────────────
#  5b. REAL TWO-STEP PASSWORD CHANGE VIEWS
# ─────────────────────────────────────────────────────────
@login_required
def password_change_custom(request):
    form = PasswordChangeForm(user=request.user, data=request.POST or None)

    if request.method == 'POST':
        logger.debug("password_change_custom | POST received")
        step = request.POST.get('step')
        if step not in ('password', 'verify'):
            return redirect('shop:password_change')

        if step == 'password':
            if form.is_valid():
                otp_obj = PasswordResetOTP.generate_code(user=request.user)
                try:
                    send_mail(
                        subject="[HOXOBIL] Security Code: Change Password Request",
                        message=(
                            f"Hello {request.user.username},\n\n"
                            f"Your 6-digit verification code is: {otp_obj.code}\n\n"
                            f"This code expires in 5 minutes."
                        ),
                        from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'help.hoxobil@gmail.com'),
                        recipient_list=[request.user.email],
                        fail_silently=False,
                    )
                except Exception as e:
                    logger.error("password_change_custom | Failed sending token email: %s", e)

                request.session['temp_pw_data'] = dict(request.POST.lists())
                return JsonResponse({'status': 'next_step'})
            else:
                errors_dict = {field: errors[0] for field, errors in form.errors.items()}
                if form.non_field_errors():
                    errors_dict['non_field_errors'] = form.non_field_errors()[0]
                return JsonResponse({'status': 'error', 'errors': errors_dict}, status=400)

        elif step == 'verify':
            submitted_otp = request.POST.get('otp_code', '').strip()
            saved_data = request.session.get('temp_pw_data')

            if not saved_data:
                return JsonResponse(
                    {'status': 'error', 'otp_error': 'Session expired. Please re-enter your passwords.'},
                    status=400,
                )

            latest_otp = PasswordResetOTP.objects.filter(user=request.user, is_used=False).first()

            if latest_otp and latest_otp.code == submitted_otp and latest_otp.is_valid():
                qd = QueryDict(mutable=True)
                for key, values in saved_data.items():
                    for val in values:
                        qd.appendlist(key, val)

                final_form = PasswordChangeForm(user=request.user, data=qd)
                if final_form.is_valid():
                    final_form.save()
                    latest_otp.is_used = True
                    latest_otp.save()
                    request.session.pop('temp_pw_data', None)
                    messages.success(request, "Your password has been updated securely.")
                    return JsonResponse({'status': 'success', 'redirect': reverse('shop:password_change_done')})
                else:
                    logger.error(
                        "password_change_custom | Reconstructed form invalid for user %s: %s",
                        request.user, final_form.errors
                    )
                    return JsonResponse(
                        {'status': 'error', 'otp_error': 'Something went wrong. Please start over.'},
                        status=400,
                    )

            return JsonResponse(
                {'status': 'error', 'otp_error': 'Invalid or expired code. Please try again.'},
                status=400,
            )

    return render(request, 'registration/password_change_form.html', {'form': form})


@login_required
@csrf_exempt
@require_POST
def resend_otp(request):
    PasswordResetOTP.objects.filter(user=request.user, is_used=False).update(is_used=True)
    new_otp = PasswordResetOTP.generate_code(user=request.user)

    try:
        send_mail(
            subject="[HOXOBIL] New Security Code: Change Password Request",
            message=(
                f"Hello {request.user.username},\n\n"
                f"Your new verification code is: {new_otp.code}\n\n"
                f"This code expires in 5 minutes."
            ),
            from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'help.hoxobil@gmail.com'),
            recipient_list=[request.user.email],
            fail_silently=False,
        )
        return JsonResponse({'status': 'success'})
    except Exception as e:
        logger.error("resend_otp | Failed to send email: %s", e)
        return JsonResponse({'status': 'error', 'message': 'Email failed.'}, status=500)


# ─────────────────────────────────────────────────────────
#  6. SHOPPING CART VIEWS
# ─────────────────────────────────────────────────────────
@require_POST
def cart_add(request, product_id):
    cart = Cart(request)
    variant_id = request.POST.get('variant_id')
    override_quantity = request.POST.get('override_quantity') == 'True'

    if variant_id:
        variant = get_object_or_404(ProductVariant, id=variant_id, available=True)
    else:
        product = get_object_or_404(Product, id=product_id)
        variant = product.variants.filter(available=True).first()

    if variant is None:
        messages.error(request, "Sorry, this product has no available variants.")
        return redirect('shop:product_detail', pk=product_id)

    try:
        quantity = int(request.POST.get('quantity', 1))
    except (ValueError, TypeError):
        quantity = 1

    cart.add(variant=variant, quantity=quantity, override_quantity=override_quantity)
    return redirect('shop:cart_detail')


@require_POST
def cart_remove(request, product_id):
    cart = Cart(request)
    variant_id = request.POST.get('variant_id')

    if variant_id:
        variant = get_object_or_404(ProductVariant, id=variant_id)
    else:
        product = get_object_or_404(Product, id=product_id)
        variant = product.variants.first()

    if variant is None:
        messages.error(request, "Could not find the item to remove.")
        return redirect('shop:cart_detail')

    cart.remove(variant)
    return redirect('shop:cart_detail')


def cart_detail(request):
    cart = Cart(request)
    video_ads = VideoAd.objects.filter(is_active=True, placement='CART').order_by('-id')
    return render(request, 'shop/cart_detail.html', {'cart': cart, 'video_ads': video_ads})


# ─────────────────────────────────────────────────────────
#  7. CHECKOUT & SHIPPING VIEWS
# ─────────────────────────────────────────────────────────
@login_required
def checkout_create(request):
    cart = Cart(request)

    if len(cart) == 0:
        messages.error(request, "Your cart is empty.")
        return redirect('shop:product_list')

    form = CheckoutForm(request.POST or None)

    if request.method == 'POST':
        if form.is_valid():
            try:
                with transaction.atomic():
                    order = form.save(commit=False)
                    order.user = request.user
                    order.save()

                    for item in cart:
                        OrderItem.objects.create(
                            order=order,
                            product=item['variant'].product,
                            product_variant=item['variant'],
                            price=item['price'],
                            quantity=item['quantity'],
                        )
            except Exception as e:
                logger.error("checkout_create | Failed to create order for user %s: %s", request.user, e)
                messages.error(request, "Something went wrong placing your order. Please try again.")
                return redirect('shop:cart_detail')

            return redirect('shop:checkout_shipping_methods', order_id=order.id)
        else:
            messages.error(request, "Please check the form for errors.")

    return render(request, 'shop/checkout_create.html', {
        'cart': cart,
        'form': form,
        'video_ads': VideoAd.objects.filter(is_active=True, placement='CHECKOUT').order_by('-id'),
    })


@login_required
def checkout_shipping_methods(request, order_id):
    order = get_object_or_404(Order, id=order_id, user=request.user)
    cart = Cart(request)

    cart_items_for_api = []
    for item in cart:
        v = item['variant']
        cart_items_for_api.append({'variant': v, 'quantity': item['quantity']})

    if not cart_items_for_api:
        messages.error(request, "Your cart is empty. Cannot calculate shipping.")
        return redirect('shop:product_list')

    api_client = PodApiClient('PFT')
    shipping_rates = []
    try:
        shipping_rates = api_client.get_detailed_shipping_rates(
            cart_items=cart_items_for_api,
            country=order.country,
            zip_code=order.postal_code,
            state=order.state,
            city=order.city,
            address1=order.address,
        )
    except Exception as e:
        logger.error("checkout_shipping_methods | Printful API error: %s", e)
        messages.error(request, "Could not fetch shipping rates. Please try again.")

    try:
        items_total = float(sum(item.get_cost().amount for item in order.items.all()))
    except Exception:
        items_total = 0.0

    return render(request, 'shop/checkout_shipping_methods.html', {
        'order': order,
        'shipping_rates': shipping_rates,
        'CURRENT_CURRENCY': request.session.get('currency_code', 'NGN'),
        'items_total': items_total,
    })


@login_required
@require_POST
def checkout_final(request, order_id):
    order = get_object_or_404(Order, id=order_id, user=request.user)

    try:
        shipping_cost = Decimal(request.POST.get('shipping_cost', '0'))
    except InvalidOperation:
        messages.error(request, "Invalid shipping cost value.")
        return redirect('shop:checkout_shipping_methods', order_id=order.id)

    order.shipping_cost = shipping_cost
    order.paid = False
    order.status = 'PENDING'
    order.save()

    return redirect('shop:checkout_payment', order_id=order.id)


# ─────────────────────────────────────────────────────────
#  8. FLUTTERWAVE PAYMENT VIEWS
# ─────────────────────────────────────────────────────────
@login_required
def checkout_payment(request, order_id):
    order = get_object_or_404(Order, id=order_id, user=request.user)

    session_currency = request.session.get('currency_code', '')
    supported = list(settings.CASH_EXCHANGE_BACKEND.get('USD', {}).keys())

    if session_currency in supported:
        currency = session_currency
    elif order.country.upper() == 'NG':
        currency = 'NGN'
    else:
        currency = 'USD'

    FLW_SUPPORTED = {'NGN', 'USD', 'GHS', 'KES', 'ZAR', 'GBP', 'EUR'}
    flw_currency = currency if currency in FLW_SUPPORTED else 'USD'

    PAYSTACK_SUPPORTED = {'NGN', 'USD', 'GHS', 'ZAR'}
    paystack_currency = currency if currency in PAYSTACK_SUPPORTED else None

    rates = settings.CASH_EXCHANGE_BACKEND.get('USD', {})
    rate = Decimal(str(rates.get(flw_currency, 1.0)))

    items_total_usd = sum(item.get_cost().amount for item in order.items.all())
    shipping_usd = order.shipping_cost.amount

    items_total_display = (items_total_usd * rate).quantize(Decimal('0.01'))
    shipping_display = (shipping_usd * rate).quantize(Decimal('0.01'))
    amount = ((items_total_usd + shipping_usd) * rate).quantize(Decimal('0.01'))

    currency = flw_currency
    tx_ref = f"HOXOBIL-ORDER-{order.id}-{order.created.strftime('%Y%m%d%H%M%S')}"
    callback_url = request.build_absolute_uri(
        reverse('shop:flutterwave_callback', args=[order.id])
    )
    paystack_callback_url = request.build_absolute_uri(
        reverse('shop:paystack_callback', args=[order.id])
    )

    return render(request, 'shop/checkout_payment.html', {
        'order': order,
        'amount': amount,
        'currency': currency,
        'tx_ref': tx_ref,
        'callback_url': callback_url,
        'flutterwave_public_key': settings.FLUTTERWAVE_PUBLIC_KEY,
        'paystack_public_key': settings.PAYSTACK_PUBLIC_KEY,
        'paystack_currency': paystack_currency,
        'paystack_callback_url': paystack_callback_url,
        'items_total_display': items_total_display,
        'shipping_display': shipping_display,
    })


@login_required
def flutterwave_callback(request, order_id):
    order = get_object_or_404(Order, id=order_id, user=request.user)

    if order.paid:
        return redirect('shop:order_detail', order_id=order.id)

    status = request.GET.get('status')
    transaction_id = request.GET.get('transaction_id')

    if status == 'cancelled':
        messages.warning(request, "Payment was cancelled. You can try again.")
        return redirect('shop:checkout_payment', order_id=order.id)

    if status != 'successful' or not transaction_id:
        messages.error(request, "Payment was not successful. Please try again.")
        return redirect('shop:checkout_payment', order_id=order.id)

    try:
        verify_url = f"https://api.flutterwave.com/v3/transactions/{transaction_id}/verify"
        headers = {
            "Authorization": f"Bearer {settings.FLUTTERWAVE_SECRET_KEY}",
            "Content-Type": "application/json",
        }
        response = http_requests.get(verify_url, headers=headers, timeout=15)
        data = response.json()

        if data.get('status') != 'success' or data.get('data', {}).get('status') != 'successful':
            logger.error("flutterwave_callback | Verification failed for order %s: %s", order.id, data)
            messages.error(request, "Payment verification failed. Please contact support.")
            return redirect('shop:checkout_payment', order_id=order.id)

        paid_amount = Decimal(str(data['data']['amount']))
        paid_currency = data['data']['currency']

        session_currency = request.session.get('currency_code', '')
        FLW_SUPPORTED = {'NGN', 'USD', 'GHS', 'KES', 'ZAR', 'GBP', 'EUR'}
        supported = list(settings.CASH_EXCHANGE_BACKEND.get('USD', {}).keys())

        if session_currency in supported and session_currency in FLW_SUPPORTED:
            expected_currency = session_currency
        elif order.country.upper() == 'NG':
            expected_currency = 'NGN'
        else:
            expected_currency = 'USD'

        if expected_currency not in FLW_SUPPORTED:
            expected_currency = 'USD'

        rates = settings.CASH_EXCHANGE_BACKEND.get('USD', {})
        rate = Decimal(str(rates.get(expected_currency, 1.0)))
        items_total_usd = sum(item.get_cost().amount for item in order.items.all())
        shipping_usd = order.shipping_cost.amount
        expected_amount = ((items_total_usd + shipping_usd) * rate).quantize(Decimal('0.01'))

        if paid_currency != expected_currency or paid_amount < expected_amount:
            logger.error(
                "flutterwave_callback | Amount mismatch for order %s | expected %s %s, got %s %s",
                order.id, expected_amount, expected_currency, paid_amount, paid_currency
            )
            messages.error(request, "Payment amount mismatch. Please contact support.")
            return redirect('shop:checkout_payment', order_id=order.id)

    except Exception as e:
        logger.error("flutterwave_callback | Verification error for order %s: %s", order.id, e)
        messages.error(request, "Could not verify payment. Please contact support.")
        return redirect('shop:checkout_payment', order_id=order.id)

    order.paid = True
    order.status = 'PENDING_SETTLEMENT'
    order.settlement_release_at = timezone.now() + timedelta(
        hours=getattr(settings, 'SETTLEMENT_DELAY_HOURS', 24)
    )
    order.save()

    messages.success(
        request,
        f"Payment confirmed! Order #{order.id} is being processed and will move to "
        f"production shortly."
    )

    cart = Cart(request)
    cart.clear()

    return redirect('shop:order_detail', order_id=order.id)


@login_required
def paystack_callback(request, order_id):
    order = get_object_or_404(Order, id=order_id, user=request.user)

    if order.paid:
        return redirect('shop:order_detail', order_id=order.id)

    reference = request.GET.get('reference') or request.GET.get('trxref')

    if not reference:
        messages.error(request, "Payment was not successful. Please try again.")
        return redirect('shop:checkout_payment', order_id=order.id)

    try:
        verify_url = f"https://api.paystack.co/transaction/verify/{reference}"
        headers = {
            "Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}",
            "Content-Type": "application/json",
        }
        response = http_requests.get(verify_url, headers=headers, timeout=15)
        data = response.json()

        if not data.get('status') or data.get('data', {}).get('status') != 'success':
            logger.error("paystack_callback | Verification failed for order %s: %s", order.id, data)
            messages.error(request, "Payment verification failed. Please contact support.")
            return redirect('shop:checkout_payment', order_id=order.id)

        # Paystack returns amount in the smallest currency subunit (e.g. kobo).
        paid_amount = Decimal(str(data['data']['amount'])) / Decimal('100')
        paid_currency = data['data']['currency']

        session_currency = request.session.get('currency_code', '')
        PAYSTACK_SUPPORTED = {'NGN', 'USD', 'GHS', 'ZAR'}
        supported = list(settings.CASH_EXCHANGE_BACKEND.get('USD', {}).keys())

        if session_currency in supported and session_currency in PAYSTACK_SUPPORTED:
            expected_currency = session_currency
        elif order.country.upper() == 'NG':
            expected_currency = 'NGN'
        else:
            expected_currency = 'USD'

        if expected_currency not in PAYSTACK_SUPPORTED:
            expected_currency = 'NGN'

        rates = settings.CASH_EXCHANGE_BACKEND.get('USD', {})
        rate = Decimal(str(rates.get(expected_currency, 1.0)))
        items_total_usd = sum(item.get_cost().amount for item in order.items.all())
        shipping_usd = order.shipping_cost.amount
        expected_amount = ((items_total_usd + shipping_usd) * rate).quantize(Decimal('0.01'))

        if paid_currency != expected_currency or paid_amount < expected_amount:
            logger.error(
                "paystack_callback | Amount mismatch for order %s | expected %s %s, got %s %s",
                order.id, expected_amount, expected_currency, paid_amount, paid_currency
            )
            messages.error(request, "Payment amount mismatch. Please contact support.")
            return redirect('shop:checkout_payment', order_id=order.id)

    except Exception as e:
        logger.error("paystack_callback | Verification error for order %s: %s", order.id, e)
        messages.error(request, "Could not verify payment. Please contact support.")
        return redirect('shop:checkout_payment', order_id=order.id)

    order.paid = True
    order.status = 'PENDING_SETTLEMENT'
    order.settlement_release_at = timezone.now() + timedelta(
        hours=getattr(settings, 'SETTLEMENT_DELAY_HOURS', 24)
    )
    order.save()

    messages.success(
        request,
        f"Payment confirmed! Order #{order.id} is being processed and will move to "
        f"production shortly."
    )

    cart = Cart(request)
    cart.clear()

    return redirect('shop:order_detail', order_id=order.id)


@csrf_exempt
def complete_checkout(request, order_id):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Authentication required.'}, status=401)

    order = get_object_or_404(Order, id=order_id, user=request.user)
    if not order.paid:
        order.paid = True
        order.status = 'POD_SENT'
        order.save()
    return JsonResponse({'status': 'success', 'order_id': order.id})


@csrf_exempt
@require_POST
def calculate_shipping(request):
    try:
        data = json.loads(request.body)
        cart = Cart(request)
        api_client = PodApiClient('PFT')
        shipping_money = api_client.get_shipping_rate(
            cart_items=[{'variant': i['variant'], 'quantity': i['quantity']} for i in cart],
            country=data.get('country'),
            zip_code=data.get('postal_code'),
            state=data.get('state', ''),
            city=data.get('city', ''),
            address1=data.get('address1', ''),
        )
        return JsonResponse({
            'shipping_cost': str(shipping_money.amount),
            'currency': str(shipping_money.currency),
        })
    except Exception as e:
        logger.error("calculate_shipping | error: %s", e)
        return JsonResponse({'error': str(e)}, status=400)


# ─────────────────────────────────────────────────────────
#  9. ORDER HISTORY & DETAIL
# ─────────────────────────────────────────────────────────
class OrderHistoryView(LoginRequiredMixin, ListView):
    model = Order
    template_name = 'shop/order_history.html'
    context_object_name = 'orders'

    def get_queryset(self):
        return Order.objects.filter(user=self.request.user).prefetch_related('items').order_by('-created')


class OrderDetailView(LoginRequiredMixin, DetailView):
    model = Order
    template_name = 'shop/order_detail.html'
    pk_url_kwarg = 'order_id'

    def get_queryset(self):
        return Order.objects.filter(user=self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        try:
            context['custom_ticket'] = self.object.custom_ticket
        except Exception:
            context['custom_ticket'] = None
        return context


# ─────────────────────────────────────────────────────────
#  10. ORDER TRACKING
# ─────────────────────────────────────────────────────────
@login_required
def order_tracking(request, order_id):
    order = get_object_or_404(Order, id=order_id, user=request.user)
    tracking_events = []
    error = None

    if order.pod_order_id:
        try:
            url = f"https://api.printful.com/orders/{order.pod_order_id}"
            headers = {
                'Authorization': f'Bearer {settings.PRINTFUL_ACCESS_TOKEN}',
                'X-PF-Store-Id': str(settings.PRINTFUL_STORE_ID),
            }
            response = http_requests.get(url, headers=headers, timeout=15)
            data = response.json()

            if response.status_code == 200:
                result = data.get('result', {})
                shipments = result.get('shipments', [])

                if shipments:
                    for shipment in shipments:
                        tracking_number = shipment.get('tracking_number', '')
                        tracking_url = shipment.get('tracking_url', '')
                        carrier = shipment.get('carrier', '')

                        if tracking_number and not order.tracking_number:
                            order.tracking_number = tracking_number
                            order.tracking_url = tracking_url
                            order.carrier = carrier
                            order.status = 'SHIPPED'
                            order.save()

                        tracking_events.append({
                            'carrier': carrier,
                            'tracking_number': tracking_number,
                            'tracking_url': tracking_url,
                            'shipped_at': shipment.get('ship_date', ''),
                        })
                else:
                    error = "Your order is being prepared. Tracking will be available once it ships."
            else:
                error = "Could not fetch tracking info. Please try again later."
        except Exception as e:
            logger.error("order_tracking | error for order %s: %s", order.id, e)
            error = "A network error occurred. Please try again."
    else:
        error = "This order has not been submitted to fulfillment yet."

    return render(request, 'shop/order_tracking.html', {
        'order': order,
        'tracking_events': tracking_events,
        'error': error,
    })


# ─────────────────────────────────────────────────────────
#  CUSTOM ORDER REQUEST VIEW
# ─────────────────────────────────────────────────────────
def custom_order_request(request, product_slug=None):
    if product_slug:
        product_obj = Product.objects.filter(slug=product_slug).first()
        if product_obj:
            return redirect(
                reverse('shop:chat_support') + f'?pinned_product={product_obj.name}'
            )
    return redirect('shop:chat_support')


# ─────────────────────────────────────────────────────────────────────────────
#  SUPPORT CHAT HELPERS & ROUTINES
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_product_by_garment(garment_keyword):
    kw = (garment_keyword or 'tee').lower()
    if 'hoodie' in kw:
        product_obj = Product.objects.filter(name__icontains='hoodie').first()
    elif 'sweatshirt' in kw:
        product_obj = Product.objects.filter(name__icontains='sweatshirt').first()
    elif 'cap' in kw or 'hat' in kw or 'beanie' in kw:
        product_obj = (
            Product.objects.filter(name__icontains='beanie').first()
            or Product.objects.filter(name__icontains='cap').first()
            or Product.objects.filter(name__icontains='hat').first()
        )
    else:
        product_obj = (
            Product.objects.filter(name__icontains=kw).first()
            or Product.objects.filter(name__icontains='tee').first()
        )
    variant_obj = product_obj.variants.first() if product_obj else None
    return product_obj, variant_obj


def pending_upload_status(request):
    from django.http import JsonResponse
    return JsonResponse({'status': 'pending'}, status=202)


def _get_garment_fallback_url(request, garment_keyword):
    garment_keyword = (garment_keyword or 'tee').lower()
    product_obj, _ = _resolve_product_by_garment(garment_keyword)

    if product_obj and product_obj.image_url:
        return product_obj.image_url

    if 'cap' in garment_keyword or 'hat' in garment_keyword or 'beanie' in garment_keyword:
        return "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='1500' height='525'><rect width='100%' height='100%' fill='%23e0e0e0'/><text x='50%' y='50%' font-family='sans-serif' font-size='32' fill='%23777777' text-anchor='middle' dominant-baseline='middle'>Headwear Placement Canvas Blueprint</text></svg>"

    return "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='1200' height='1200'><rect width='100%' height='100%' fill='%23e0e0e0'/><text x='50%' y='50%' font-family='sans-serif' font-size='32' fill='%23777777' text-anchor='middle' dominant-baseline='middle'>Apparel Placement Canvas Blueprint</text></svg>"


def chat_support_page(request):
    pinned_product = request.GET.get('pinned_product', '').strip()
    pinned_color   = request.GET.get('pinned_color', '').strip()
    pinned_size    = request.GET.get('pinned_size', '').strip()
    pinned_image   = request.GET.get('pinned_image', '').strip()

    # ── A pinned product arrived via query params (from the product page) ──
    # Process it ONCE: clear old history, set the session context, stash a
    # one-time greeting payload, then redirect to the clean URL. This stops
    # a page refresh from re-triggering the "fresh start" flow over and over
    # (refreshing used to wipe out progress like ticket_ready/awaiting_instructions
    # because the query params never went away).
    if pinned_product:
        if request.user.is_authenticated:
            chat = SupportChat.objects.filter(user=request.user).first()
            if chat:
                chat.messages.all().delete()

        if pinned_color and pinned_size:
            target_step = 'awaiting_placement'
        elif pinned_size:
            target_step = 'awaiting_color'
        else:
            target_step = 'awaiting_size'

        request.session['hoxo_chat_context'] = {
            'current_step': target_step,
            'garment': pinned_product,
            'color': pinned_color if pinned_color else None,
            'size': pinned_size if pinned_size else None,
            'placement': None
        }
        request.session['hoxo_pending_greeting'] = {
            'product': pinned_product,
            'color':   pinned_color,
            'size':    pinned_size,
            'image':   pinned_image,
        }

        return redirect('shop:chat_support')

    # ── Clean URL (no pinned params): pop the one-time greeting if present ──
    pending_greeting = request.session.pop('hoxo_pending_greeting', None)

    # Deck banner + ongoing state should reflect whatever's currently in the
    # session context, not the URL, so it survives refreshes correctly.
    current_context = request.session.get('hoxo_chat_context', {})

    return render(request, 'shop/chat_support.html', {
        'pending_greeting_json': _json.dumps(pending_greeting) if pending_greeting else 'null',
        'current_garment':       current_context.get('garment', ''),
        'current_image':         pending_greeting.get('image', '') if pending_greeting else '',
    })


def _build_editor_token(garment_url, design_url, file_url, pf_width=1500, pf_height=525, garment_type=''):
    payload = _json.dumps({
        'garment_url':  garment_url,
        'design_url':   design_url,
        'file_url':     file_url,
        'pf_width':     pf_width,
        'pf_height':    pf_height,
        'garment_type': garment_type,
    })
    return base64.b64encode(payload.encode()).decode()


# ─────────────────────────────────────────────────────────
#  SEND SUPPORT MESSAGE — auth check returns JSON, no redirect
# ─────────────────────────────────────────────────────────
@require_POST
def send_support_message(request):
    """Customer sends a message or image; AI processing generates automated mockup proofs."""

    # Return a JSON auth prompt instead of redirecting — keeps the chat alive
    if not request.user.is_authenticated:
        return JsonResponse({
            'status': 'auth_required',
            'auto_reply': (
                "👋 **Welcome to HOXOBIL!**\n\n"
                "To start your custom order, you'll need an account.\n\n"
                "__LOGIN_BUTTONS__"
            )
        }, status=200)

    text       = request.POST.get('message', '').strip()
    image_file = request.FILES.get('image')

    chat, created = SupportChat.objects.get_or_create(user=request.user)

    # Load session context
    session_context = request.session.get('hoxo_chat_context', {
        'current_step': 'awaiting_garment', 'garment': None, 'color': None, 'size': None, 'placement': None
    })

    # ── STATE RESTORATION GUARD ──
    TERMINAL_STEPS = {
        'ticket_ready', 'awaiting_instructions', 'awaiting_upload', 'awaiting_font',
        'awaiting_custom_text', 'awaiting_print_color', 'awaiting_quantity',
    }
    if session_context.get('current_step') not in TERMINAL_STEPS:
        existing_ticket = CustomDesignTicket.objects.filter(
            user=request.user,
            status='Sent to Customer for Approval',
        ).order_by('-id').first()

        if existing_ticket:
            session_context = {
                'current_step': 'ticket_ready',
                'garment':      existing_ticket.garment_item,
                'color':        existing_ticket.garment_color,
                'size':         existing_ticket.garment_size,
                'placement':    existing_ticket.placement,
                'custom_text_request': existing_ticket.custom_text,
                'typography_style':    existing_ticket.typography_style,
            }
            request.session['hoxo_chat_context'] = session_context

    # Handle Image Uploads
    if image_file:
        simulated_text = f"[Uploaded Design Layer Asset: {image_file.name}]"
        msg = ChatMessage.objects.create(chat=chat, sender_type='user', text=simulated_text)
        msg.image_field = image_file
        msg.save()
        text = simulated_text
        session_context['design_asset'] = simulated_text
    else:
        if not text:
            return JsonResponse({'status': 'error', 'message': 'Empty message payloads rejected.'}, status=400)
        ChatMessage.objects.create(chat=chat, sender_type='user', text=text)

    # Bot Logic
    auto_reply_text, updated_context, trigger_upload = bot.get_response(text, context=session_context, user=request.user)

    # ── APPROVAL AUTO-INVOICE TRIGGER ──
    if trigger_upload and updated_context.get('current_step') == 'ticket_ready':
        active_ticket = CustomDesignTicket.objects.filter(
            user=request.user,
            status='Sent to Customer for Approval',
            invoice_sent=False,
        ).order_by('-id').first()

        if active_ticket and active_ticket.invoice_amount:
            from .admin import _drop_invoice_into_chat
            _drop_invoice_into_chat(active_ticket, request=request)
        elif active_ticket and not active_ticket.invoice_amount:
            ChatMessage.objects.create(
                chat=chat,
                sender_type='admin',
                text=(
                    "🔔 The customer has approved their mockup! "
                    "Please set the invoice amount on the ticket and click 'Send Invoice' to send them the payment link."
                )
            )

    # ── TICKET LOOKUP / CREATE ──
    if updated_context.get('current_step') == 'ticket_ready':
        ticket = CustomDesignTicket.objects.filter(
            user=request.user,
            status__in=['Pending Design Team Review', 'Mockup in Progress', 'Sent to Customer for Approval']
        ).order_by('-id').first()

        tweak = updated_context.get('tweak_request', '')

        if ticket:
            if tweak:
                ticket.custom_text = (ticket.custom_text or '') + f"\n\n[Revision Request]: {tweak}"
                ticket.status = 'Mockup in Progress'
                ticket.save(update_fields=['custom_text', 'status'])
                updated_context['tweak_request'] = None
        else:
            ticket = CustomDesignTicket.objects.create(
                user=request.user,
                session_key=request.session.session_key,
                garment_item=updated_context.get('garment', 'Custom'),
                garment_color=updated_context.get('color', ''),
                garment_size=updated_context.get('size', ''),
                custom_text=updated_context.get('custom_text_request', 'Uploaded Asset'),
                typography_style=updated_context.get('font_style', 'N/A'),
                placement=updated_context.get('placement', '')
            )
            updated_context['ticket_created'] = True
            updated_context['active_ticket_id'] = ticket.id

    # Save session state
    request.session['hoxo_chat_context'] = updated_context
    ChatMessage.objects.create(chat=chat, sender_type='admin', text=auto_reply_text)

    return JsonResponse({
        'status': 'success',
        'auto_reply': auto_reply_text,
        'trigger_upload': trigger_upload,
        'pinned_garment': updated_context.get('garment', ''),
        'pinned_size': updated_context.get('size', ''),
        'pinned_color': updated_context.get('color', '')
    })


@login_required
@require_POST
def adjust_mockup_position(request):
    try:
        data      = json.loads(request.body)
        file_url  = data.get('file_url', '')
        position  = data.get('position', {})
        rotation  = data.get('rotation', 0)
        confirmed = data.get('confirmed', False)

        if not file_url or not position:
            return JsonResponse({'status': 'error', 'message': 'Missing parameters.'}, status=400)

        if file_url.startswith('data:'):
            return JsonResponse({'status': 'error', 'message': 'No real design uploaded yet.'}, status=400)

        tunnel_base = getattr(settings, 'PUBLIC_BASE_URL', '').rstrip('/')
        if tunnel_base and ('127.0.0.1' in file_url or 'localhost' in file_url):
            from urllib.parse import urlparse as _up
            _p = _up(file_url)
            file_url = f"{tunnel_base}{_p.path}"
            if _p.query:
                file_url += f"?{_p.query}"

        chat, _ = SupportChat.objects.get_or_create(user=request.user)
        session_context = request.session.get('hoxo_chat_context', {})
        garment_keyword = session_context.get('garment') or 'tee'
        _, variant_obj = _resolve_product_by_garment(garment_keyword)

        preview_url = None
        pf_width, pf_height = 1500, 525
        if variant_obj:
            api_client = PodApiClient('PFT')
            try:
                raw_placement = session_context.get('placement') or 'front'
                normalized_placement = raw_placement.lower().replace(' ', '_')
                preview_url, pf_width, pf_height = api_client.generate_mockup_preview(
                    product_variant_id=variant_obj.pod_id,
                    file_url=file_url,
                    placement_zone=normalized_placement,
                    custom_position=position,
                    rotation=rotation,
                )
                if not pf_width:
                    pf_width, pf_height = 1500, 525
            except Exception as api_err:
                logger.error("adjust_mockup_position | Printful error: %s", api_err)

        if preview_url:
            garment_url = preview_url
        else:
            garment_url = _get_garment_fallback_url(request, garment_keyword)

        from urllib.parse import urlparse as _urlparse
        design_url = _urlparse(file_url).path
        get_garment_type = garment_keyword.lower()
        proof_image_url = preview_url if preview_url else garment_url

        if confirmed:
            if preview_url:
                reply = (
                    f"🎉 Mockup confirmed and locked in!\n\n"
                    f"![Final Mockup]({proof_image_url})\n\n"
                    f"✅ Your design placement has been saved. Head to **/custom-order/** "
                    f"to finalise your order and submit for production!"
                )
            else:
                reply = (
                    f"❌ **API Error:** Printful failed to generate the final mockup. "
                    f"Check your terminal logs for tunnel connection issues.\n\n"
                    f"*(Base product displayed below)*\n\n![Fallback]({proof_image_url})"
                )
        else:
            editor_token = _build_editor_token(
                garment_url=garment_url,
                design_url=design_url,
                file_url=file_url,
                pf_width=pf_width,
                pf_height=pf_height,
                garment_type=get_garment_type,
            )

            if preview_url:
                status_text = "✅ Position updated! Here is your revised mockup:"
            else:
                status_text = "⚠️ **Update Failed:** Printful could not render the new position. Displaying base product:"

            reply = (
                f"{status_text}\n\n"
                f"![Proof]({proof_image_url})\n\n"
                f"[EDITOR:{editor_token}]"
                f"<button type='button' class='hoxo-adjust-btn' onclick='document.getElementById(\"hoxoEditorPanel\").scrollIntoView({{behavior:\"smooth\"}})'>✏️ Adjust Again</button>\n\n"
                f"🚀 Happy with this? Head to **/custom-order/** to finalise your order!"
            )

        ChatMessage.objects.create(chat=chat, sender_type='admin', text=reply)
        return JsonResponse({'status': 'success', 'auto_reply': reply})

    except Exception as e:
        logger.error("adjust_mockup_position | Error: %s", e)
        return JsonResponse({'status': 'error', 'message': 'Something went wrong.'}, status=500)


# ─────────────────────────────────────────────────────────
#  MOCKUP BACKWARD DATA FLOW
# ─────────────────────────────────────────────────────────
@login_required
def fetch_support_messages(request):
    chat, _ = SupportChat.objects.get_or_create(user=request.user)

    messages_data = [
        {
            'sender_type': m.sender_type,
            'text': m.text,
            'created_at': m.created_at.strftime('%H:%M · %d %b'),
        }
        for m in chat.messages.all()
    ]
    return JsonResponse({'status': 'success', 'messages': messages_data})


@login_required
@require_POST
def clear_chat_history(request):
    try:
        chat = SupportChat.objects.get(user=request.user)
        chat.messages.all().delete()
    except SupportChat.DoesNotExist:
        pass

    if 'hoxo_chat_context' in request.session:
        del request.session['hoxo_chat_context']

    return JsonResponse({'status': 'success'})


@csrf_exempt
@require_POST
def ai_placement_view(request):
    try:
        data = json.loads(request.body)
        query = data.get('query', '').strip()
        if not query:
            return JsonResponse({'error': 'No query provided.'}, status=400)

        temp_context = {
            'current_step': 'placement_query',
            'garment': None, 'color': None, 'size': None, 'placement': None
        }
        response_text, _, _ = bot.get_response(query, context=temp_context)
        return JsonResponse({'status': 'success', 'response': response_text})
    except Exception as e:
        logger.error("ai_placement_view | error: %s", e)
        return JsonResponse({'error': 'Something went wrong.'}, status=500)


# ─────────────────────────────────────────────────────────
#  11. STATIC & BRAND TRANSMISSION VIEWS
# ─────────────────────────────────────────────────────────
class AboutUsView(TemplateView):
    template_name = 'shop/about.html'


class ContactUsView(TemplateView):
    template_name = 'shop/contact.html'

    def post(self, request, *args, **kwargs):
        name    = request.POST.get('name')
        email   = request.POST.get('email')
        subject = request.POST.get('subject')
        message = request.POST.get('message')

        if not all([name, email, subject, message]):
            messages.error(request, "Transmission failed. Missing mandatory form parameters.")
            return render(request, self.template_name, {'form_errors': True})

        messages.success(request, "Your message transmission was successfully delivered to HQ.")
        return redirect('shop:contact')


# ─────────────────────────────────────────────────────────
#  12. LEGAL & SUPPORT PAGES
# ─────────────────────────────────────────────────────────
class PrivacyPolicyView(TemplateView):
    template_name = 'shop/privacy_policy.html'


class TermsOfServiceView(TemplateView):
    template_name = 'shop/terms_of_service.html'


class FaqView(TemplateView):
    template_name = 'shop/faq.html'


class ShippingInfoView(TemplateView):
    template_name = 'shop/shipping_info.html'


class ReturnsPolicyView(TemplateView):
    template_name = 'shop/returns_policy.html'


class SizeGuideView(TemplateView):
    template_name = 'shop/size_guide.html'


# ─────────────────────────────────────────────────────────
#  13. CUSTOM DESIGN TICKET — INVOICE & CHECKOUT FLOW
# ─────────────────────────────────────────────────────────

@login_required
def custom_order_checkout(request, ticket_id):
    ticket = get_object_or_404(CustomDesignTicket, id=ticket_id, user=request.user)

    linked_order = getattr(ticket, 'linked_order', None)
    if linked_order and linked_order.paid:
        return redirect('shop:order_detail', order_id=linked_order.id)

    invoice_amount = getattr(ticket, 'invoice_amount', None)
    if not invoice_amount:
        messages.error(request, "This ticket does not have a price set yet. Please contact support.")
        return redirect('shop:support_chat')

    form = CheckoutForm(request.POST or None, initial={
        'first_name': request.user.first_name,
        'last_name':  request.user.last_name,
        'email':      request.user.email,
    })

    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                order = form.save(commit=False)
                order.user = request.user
                order.shipping_cost = 0
                order.save()

                # Convert NGN invoice to USD to match all other products on the site
                rates = getattr(settings, 'CASH_EXCHANGE_BACKEND', {}).get('USD', {})
                ngn_rate = Decimal(str(rates.get('NGN', 1500)))
                invoice_amount_usd = (Decimal(str(invoice_amount)) / ngn_rate).quantize(Decimal('0.01'))

                # Use the ticket mockup as the product image so it shows in cart
                mockup_url = ''
                if ticket.design_team_mockup:
                    try:
                        mockup_url = ticket.design_team_mockup.url
                    except Exception:
                        mockup_url = ''

                custom_product, created = Product.objects.get_or_create(
                    slug='custom-design-order',
                    defaults={
                        'name':           'Custom Design Order',
                        'price':          invoice_amount_usd,
                        'price_currency': 'USD',
                        'available':      False,
                        'image_url':      mockup_url,
                    }
                )

                # Always sync price, currency and image to current ticket values
                update_fields = []
                if float(custom_product.price.amount) != float(invoice_amount_usd):
                    custom_product.price = invoice_amount_usd
                    update_fields.append('price')
                if custom_product.price_currency != 'USD':
                    custom_product.price_currency = 'USD'
                    update_fields.append('price_currency')
                if mockup_url and custom_product.image_url != mockup_url:
                    custom_product.image_url = mockup_url
                    update_fields.append('image_url')
                if update_fields:
                    custom_product.save(update_fields=update_fields)

                # Custom orders don't come from the regular catalog, so there's no
                # real ProductVariant already tied to this purchase. Create/reuse a
                # dedicated variant scoped to this ticket so OrderItem's
                # product_variant field (required on most schemas) is always filled.
                variant_pod_id = f'custom-ticket-{ticket.id}'
                custom_variant, _ = ProductVariant.objects.get_or_create(
                    pod_id=variant_pod_id,
                    defaults={
                        'product':   custom_product,
                        'price':     invoice_amount_usd,
                        'size':      ticket.garment_size or '',
                        'color':     ticket.garment_color or '',
                        'available': True,
                    }
                )
                if float(custom_variant.price.amount) != float(invoice_amount_usd):
                    custom_variant.price = invoice_amount_usd
                    custom_variant.save(update_fields=['price'])

                OrderItem.objects.create(
                    order=order,
                    product=custom_product,
                    product_variant=custom_variant,
                    price=invoice_amount_usd,
                    quantity=1,
                )

                ticket.linked_order = order
                ticket.save(update_fields=['linked_order'])

        except Exception as e:
            logger.error("custom_order_checkout | Failed to create order for ticket %s: %s", ticket.id, e)
            messages.error(request, "Something went wrong creating your order. Please try again or contact support.")
            return redirect('shop:custom_order_checkout', ticket_id=ticket.id)

        return redirect('shop:custom_order_payment', order_id=order.id, ticket_id=ticket.id)

    return render(request, 'shop/custom_order_checkout.html', {
        'ticket':         ticket,
        'invoice_amount': invoice_amount,
        'form':           form,
    })


@login_required
def custom_order_payment(request, order_id, ticket_id):
    order  = get_object_or_404(Order, id=order_id, user=request.user)
    ticket = get_object_or_404(CustomDesignTicket, id=ticket_id, user=request.user)

    if order.paid:
        return redirect('shop:order_detail', order_id=order.id)

    invoice_amount = Decimal(str(getattr(ticket, 'invoice_amount', 0)))
    currency = 'NGN' if getattr(order, 'country', 'NG').upper() == 'NG' else 'USD'

    rates = getattr(settings, 'CASH_EXCHANGE_BACKEND', {}).get('USD', {})
    rate  = Decimal(str(rates.get(currency, 1.0)))
    amount = (invoice_amount * rate).quantize(Decimal('0.01'))

    tx_ref       = f"HOXOBIL-CUSTOM-{ticket.id}-{order.id}"
    callback_url = request.build_absolute_uri(
        reverse('shop:custom_order_payment_callback', args=[order.id, ticket.id])
    )
    paystack_callback_url = request.build_absolute_uri(
        reverse('shop:custom_order_payment_paystack_callback', args=[order.id, ticket.id])
    )

    PAYSTACK_SUPPORTED = {'NGN', 'USD', 'GHS', 'ZAR'}
    paystack_currency = currency if currency in PAYSTACK_SUPPORTED else None

    return render(request, 'shop/custom_order_payment.html', {
        'order':                  order,
        'ticket':                 ticket,
        'amount':                 amount,
        'currency':               currency,
        'tx_ref':                 tx_ref,
        'callback_url':           callback_url,
        'flutterwave_public_key': settings.FLUTTERWAVE_PUBLIC_KEY,
        'paystack_public_key':    settings.PAYSTACK_PUBLIC_KEY,
        'paystack_currency':      paystack_currency,
        'paystack_callback_url':  paystack_callback_url,
    })


def _fulfill_custom_design_order(request, order, ticket, invoice_amount):
    """
    Shared fulfillment logic for a paid custom design order: marks the order
    paid, submits to Printful, notifies the customer in chat, emails a
    receipt, and emails the admin team. Used by both the Flutterwave and
    Paystack custom-order payment callbacks so the ~180 line flow isn't
    duplicated. Sets request messages and returns nothing.
    """
    # ── 2. MARK ORDER AS PAID, HOLD FOR SETTLEMENT ────────────────────────────
    # We no longer push to Printful here. Printful charges our card the
    # instant an order is submitted, but Paystack/Flutterwave typically take
    # ~24h (sometimes longer) to actually settle the customer's payment into
    # our bank account. Pushing instantly risks the card being declined for
    # insufficient funds. Instead we hold the order at PENDING_SETTLEMENT and
    # let the release_settled_orders management command submit it to
    # Printful once the settlement window has passed. See shop/fulfillment.py.
    order.paid   = True
    order.status = 'PENDING_SETTLEMENT'
    order.settlement_release_at = timezone.now() + timedelta(
        hours=getattr(settings, 'SETTLEMENT_DELAY_HOURS', 24)
    )
    order.save()

    # ── 3. NOTIFY CUSTOMER IN CHAT ────────────────────────────────────────────
    chat = SupportChat.objects.filter(user=request.user).first()
    if chat:
        chat_text = (
            f"✅ **Payment Confirmed!**\n\n"
            f"We've received your payment for your custom **{ticket.garment_item}**. "
            f"Your order will move into production shortly.\n\n"
            f"Order reference: **#{order.id}**. Thank you! 🙏"
        )
        ChatMessage.objects.create(chat=chat, sender_type='admin', text=chat_text)

        receipt_text = (
            f"🧾 **Your Order Receipt**\n\n"
            f"{'─' * 30}\n"
            f"**Order ID:**       #{order.id}\n"
            f"**Item:**           Custom {ticket.garment_item}\n"
            f"**Garment Color:**  {ticket.garment_color or '—'}\n"
            f"**Size:**           {ticket.garment_size or '—'}\n"
            f"**Placement:**      {ticket.placement or '—'}\n"
            f"**Amount Paid:**    ₦{invoice_amount:,}\n"
            f"{'─' * 30}\n\n"
            f"📧 A copy of this receipt has been forwarded to **{order.email}**.\n\n"
            f"Keep this order ID **#{order.id}** handy — use it to track your order anytime."
        )
        ChatMessage.objects.create(chat=chat, sender_type='admin', text=receipt_text)

    # ── 4. SEND RECEIPT EMAIL TO CUSTOMER ─────────────────────────────────────
    try:
        from django.core.mail import send_mail as _send_mail
        _send_mail(
            subject=f"HOXOBIL — Your Custom Order Receipt #{order.id}",
            message=(
                f"Hi {order.first_name},\n\n"
                f"Thank you for your order! Here's your receipt:\n\n"
                f"{'─' * 40}\n"
                f"Order ID:        #{order.id}\n"
                f"Item:            Custom {ticket.garment_item}\n"
                f"Garment Color:   {ticket.garment_color or '—'}\n"
                f"Size:            {ticket.garment_size or '—'}\n"
                f"Placement:       {ticket.placement or '—'}\n"
                f"Amount Paid:     ₦{invoice_amount:,}\n"
                f"{'─' * 40}\n\n"
                f"Your order is being processed and will move into production shortly. "
                f"We'll email you again once it ships.\n\n"
                f"— The HOXOBIL Team 🖤"
            ),
            from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'help.hoxobil@gmail.com'),
            recipient_list=[order.email],
            fail_silently=True,
        )
    except Exception as e:
        logger.error("_fulfill_custom_design_order | Customer receipt email failed for order %s: %s", order.id, e)

    # ── 5. SUCCESS MESSAGE ──────────────────────────────────────────────────
    messages.success(
        request,
        f"Payment confirmed! Your custom order #{order.id} is being processed and will "
        f"move into production shortly. 🚀"
    )


@login_required
def custom_order_payment_callback(request, order_id, ticket_id):
    order  = get_object_or_404(Order, id=order_id, user=request.user)
    ticket = get_object_or_404(CustomDesignTicket, id=ticket_id, user=request.user)

    if order.paid:
        return redirect('shop:order_detail', order_id=order.id)

    status         = request.GET.get('status')
    transaction_id = request.GET.get('transaction_id')

    if status == 'cancelled':
        messages.warning(request, "Payment was cancelled. You can try again.")
        return redirect('shop:custom_order_payment', order_id=order.id, ticket_id=ticket.id)

    if status != 'successful' or not transaction_id:
        messages.error(request, "Payment was not successful. Please try again.")
        return redirect('shop:custom_order_payment', order_id=order.id, ticket_id=ticket.id)

    # ── 1. VERIFY PAYMENT ────────────────────────────────────────────────────
    try:
        verify_url = f"https://api.flutterwave.com/v3/transactions/{transaction_id}/verify"
        headers    = {
            "Authorization": f"Bearer {settings.FLUTTERWAVE_SECRET_KEY}",
            "Content-Type":  "application/json",
        }
        response = http_requests.get(verify_url, headers=headers, timeout=15)
        data     = response.json()

        if data.get('status') != 'success' or data.get('data', {}).get('status') != 'successful':
            messages.error(request, "Payment verification failed. Please contact support.")
            return redirect('shop:custom_order_payment', order_id=order.id, ticket_id=ticket.id)

        paid_amount    = Decimal(str(data['data']['amount']))
        paid_currency  = data['data']['currency']
        invoice_amount = Decimal(str(getattr(ticket, 'invoice_amount', 0)))
        currency       = 'NGN' if getattr(order, 'country', 'NG').upper() == 'NG' else 'USD'
        rates          = getattr(settings, 'CASH_EXCHANGE_BACKEND', {}).get('USD', {})
        rate           = Decimal(str(rates.get(currency, 1.0)))
        expected_amount = (invoice_amount * rate).quantize(Decimal('0.01'))

        if paid_currency != currency or paid_amount < expected_amount:
            messages.error(request, "Payment amount mismatch. Please contact support.")
            return redirect('shop:custom_order_payment', order_id=order.id, ticket_id=ticket.id)

    except Exception as e:
        logger.error("custom_order_payment_callback | Verification error for order %s: %s", order.id, e)
        messages.error(request, "Could not verify payment. Please contact support.")
        return redirect('shop:custom_order_payment', order_id=order.id, ticket_id=ticket.id)

    _fulfill_custom_design_order(request, order, ticket, invoice_amount)

    return redirect('shop:order_detail', order_id=order.id)


@login_required
def custom_order_payment_paystack_callback(request, order_id, ticket_id):
    order  = get_object_or_404(Order, id=order_id, user=request.user)
    ticket = get_object_or_404(CustomDesignTicket, id=ticket_id, user=request.user)

    if order.paid:
        return redirect('shop:order_detail', order_id=order.id)

    reference = request.GET.get('reference') or request.GET.get('trxref')

    if not reference:
        messages.error(request, "Payment was not successful. Please try again.")
        return redirect('shop:custom_order_payment', order_id=order.id, ticket_id=ticket.id)

    # ── 1. VERIFY PAYMENT ────────────────────────────────────────────────────
    try:
        verify_url = f"https://api.paystack.co/transaction/verify/{reference}"
        headers    = {
            "Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}",
            "Content-Type":  "application/json",
        }
        response = http_requests.get(verify_url, headers=headers, timeout=15)
        data     = response.json()

        if not data.get('status') or data.get('data', {}).get('status') != 'success':
            messages.error(request, "Payment verification failed. Please contact support.")
            return redirect('shop:custom_order_payment', order_id=order.id, ticket_id=ticket.id)

        # Paystack returns amount in the smallest currency subunit (e.g. kobo).
        paid_amount    = Decimal(str(data['data']['amount'])) / Decimal('100')
        paid_currency  = data['data']['currency']
        invoice_amount = Decimal(str(getattr(ticket, 'invoice_amount', 0)))
        currency       = 'NGN' if getattr(order, 'country', 'NG').upper() == 'NG' else 'USD'
        rates          = getattr(settings, 'CASH_EXCHANGE_BACKEND', {}).get('USD', {})
        rate           = Decimal(str(rates.get(currency, 1.0)))
        expected_amount = (invoice_amount * rate).quantize(Decimal('0.01'))

        if paid_currency != currency or paid_amount < expected_amount:
            messages.error(request, "Payment amount mismatch. Please contact support.")
            return redirect('shop:custom_order_payment', order_id=order.id, ticket_id=ticket.id)

    except Exception as e:
        logger.error("custom_order_payment_paystack_callback | Verification error for order %s: %s", order.id, e)
        messages.error(request, "Could not verify payment. Please contact support.")
        return redirect('shop:custom_order_payment', order_id=order.id, ticket_id=ticket.id)

    _fulfill_custom_design_order(request, order, ticket, invoice_amount)

    return redirect('shop:order_detail', order_id=order.id)


# ─────────────────────────────────────────────────────────
#  14. CUSTOM ORDER — ADD TO CART FROM CHAT
# ─────────────────────────────────────────────────────────

@login_required
@require_POST
def custom_order_add_to_cart(request):
    from djmoney.money import Money

    ticket_id = request.POST.get('ticket_id', '').strip()
    if not ticket_id:
        return JsonResponse({'status': 'error', 'message': 'No ticket ID provided.'}, status=400)

    ticket = get_object_or_404(CustomDesignTicket, id=ticket_id, user=request.user)

    if not ticket.invoice_amount:
        return JsonResponse(
            {'status': 'error', 'message': 'This ticket has no price set yet. Please contact support.'},
            status=400
        )

    invoice_amount = Decimal(str(ticket.invoice_amount))

    # Convert NGN to USD to match all other products
    rates = getattr(settings, 'CASH_EXCHANGE_BACKEND', {}).get('USD', {})
    ngn_rate = Decimal(str(rates.get('NGN', 1500)))
    invoice_amount_usd = (invoice_amount / ngn_rate).quantize(Decimal('0.01'))

    # Mockup image for cart display
    mockup_url = ''
    if ticket.design_team_mockup:
        try:
            mockup_url = ticket.design_team_mockup.url
        except Exception:
            mockup_url = ''

    custom_product, _ = Product.objects.get_or_create(
        slug='custom-design-order',
        defaults={
            'name':           'Custom Design Order',
            'price':          invoice_amount_usd,
            'price_currency': 'USD',
            'available':      False,
            'image_url':      mockup_url,
        }
    )

    update_fields = []
    if float(custom_product.price.amount) != float(invoice_amount_usd):
        custom_product.price = invoice_amount_usd
        update_fields.append('price')
    if custom_product.price_currency != 'USD':
        custom_product.price_currency = 'USD'
        update_fields.append('price_currency')
    if mockup_url and custom_product.image_url != mockup_url:
        custom_product.image_url = mockup_url
        update_fields.append('image_url')
    if update_fields:
        custom_product.save(update_fields=update_fields)

    variant_pod_id = f'custom-ticket-{ticket.id}'
    variant, _ = ProductVariant.objects.get_or_create(
        pod_id=variant_pod_id,
        defaults={
            'product':   custom_product,
            'price':     invoice_amount_usd,
            'size':      ticket.garment_size or '',
            'color':     ticket.garment_color or '',
            'available': True,
        }
    )

    if float(variant.price.amount) != float(invoice_amount_usd):
        variant.price = invoice_amount_usd
        variant.save(update_fields=['price'])

    cart = Cart(request)
    cart.add(variant=variant, quantity=1, override_quantity=True)

    return JsonResponse({
        'status':     'ok',
        'cart_count': len(cart),
        'message':    f'Custom order added to cart (₦{invoice_amount:,.0f})',
    })