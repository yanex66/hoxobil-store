from django.urls import path
from django.contrib.auth import views as auth_views
from . import views
from . import launch_views   # ← ADDED: was missing, urls.py referenced launch_views without importing it

app_name = 'shop'

urlpatterns = [
    # 1. Homepage
    path('', views.home, name='home'),

    # 2. Shop Page
    path('shop/', views.ProductListView.as_view(), name='product_list'),

    # 2b. Custom Products Page (blank garments — "c#" prefixed on Printful)
    path('custom-products/', views.CustomProductListView.as_view(), name='custom_product_list'),

    # --- 3. Highly Specific Paths ---

    # Static Pages
    path('about/', views.AboutUsView.as_view(), name='about'),
    path('contact/', views.ContactUsView.as_view(), name='contact'),

    # Sync (superuser only)
    path('sync-products/', views.sync_printful_products, name='sync_printify'),

    # Currency and Registration
    path('set-currency/', views.set_currency, name='set_currency'),
    path('register/', views.register, name='register'),

    # OTP resend endpoint
    path('register/resend-otp/', views.resend_otp, name='resend_otp'),

    # Custom two-step password change
    path('password-change/', views.password_change_custom, name='password_change'),
    path('password-change/done/', auth_views.PasswordChangeDoneView.as_view(
        template_name='registration/password_change_done.html'
    ), name='password_change_done'),

    # Cart Views
    path('cart/', views.cart_detail, name='cart_detail'),
    path('cart/add/<int:product_id>/', views.cart_add, name='cart_add'),
    path('cart/remove/<int:product_id>/', views.cart_remove, name='cart_remove'),

    # Checkout Views
    path('checkout/', views.checkout_create, name='checkout_create'),
    path('checkout/shipping/<int:order_id>/', views.checkout_shipping_methods, name='checkout_shipping_methods'),
    path('checkout/final/<int:order_id>/', views.checkout_final, name='checkout_final'),
    path('checkout/payment/<int:order_id>/', views.checkout_payment, name='checkout_payment'),
    path('checkout/payment/<int:order_id>/callback/', views.flutterwave_callback, name='flutterwave_callback'),
    path('checkout/<int:order_id>/complete/', views.complete_checkout, name='checkout_complete'),
    path('checkout/shipping/calculate/', views.calculate_shipping, name='calculate_shipping'),
    path('checkout/<int:order_id>/paystack-callback/', views.paystack_callback, name='paystack_callback'),

    # Order Views
    path('orders/', views.OrderHistoryView.as_view(), name='order_history'),
    path('orders/<int:order_id>/', views.OrderDetailView.as_view(), name='order_detail'),
    path('orders/<int:order_id>/tracking/', views.order_tracking, name='order_tracking'),

    # Custom Order
    path('custom-order/', views.custom_order_request, name='custom_order'),
    path('custom-order/<slug:product_slug>/', views.custom_order_request, name='custom_order_slug'),
    path('custom-order/checkout/<int:ticket_id>/', views.custom_order_checkout, name='custom_order_checkout'),
    path('custom-order/payment/<int:order_id>/<int:ticket_id>/', views.custom_order_payment, name='custom_order_payment'),
    path('custom-order/payment/<int:order_id>/<int:ticket_id>/callback/', views.custom_order_payment_callback, name='custom_order_payment_callback'),
    path('custom-order/<int:order_id>/<int:ticket_id>/paystack-callback/', views.custom_order_payment_paystack_callback, name='custom_order_payment_paystack_callback'),

    # Legal & Support Pages
    path('privacy-policy/', views.PrivacyPolicyView.as_view(), name='privacy_policy'),
    path('terms-of-service/', views.TermsOfServiceView.as_view(), name='terms_of_service'),
    path('faq/', views.FaqView.as_view(), name='faq'),
    path('shipping-info/', views.ShippingInfoView.as_view(), name='shipping_info'),
    path('returns-policy/', views.ReturnsPolicyView.as_view(), name='returns_policy'),
    path('size-guide/', views.SizeGuideView.as_view(), name='size_guide'),

    # Chat Support
    path('support/chat/', views.chat_support_page, name='chat_support'),
    path('support/chat/send/', views.send_support_message, name='send_support_message'),
    path('support/chat/fetch/', views.fetch_support_messages, name='fetch_support_messages'),
    path('support/chat/clear/', views.clear_chat_history, name='clear_chat_history'),
    path('support/chat/adjust-position/', views.adjust_mockup_position, name='adjust_mockup_position'),
    path('support/chat/PENDING_UPLOAD', views.pending_upload_status, name='pending_upload_status'),
    path('support/chat/add-to-cart/', views.custom_order_add_to_cart, name='custom_order_add_to_cart'),

    # AI Placement Scanner (custom order page)
    path('api/ai-placement/', views.ai_placement_view, name='ai_placement'),

    # --- LAUNCH PAGE & DONATIONS ---
    # IMPORTANT: these must stay ABOVE the generic '<slug:slug>/' catch-all
    # below, otherwise Django would treat 'launch' / 'donate' as a product
    # slug and route them into ProductDetailView instead.
    path('launch/', launch_views.launch_page, name='launch_page'),
    path('donate/flutterwave/', launch_views.donate_initiate_flutterwave, name='donate_initiate_flutterwave'),
    path('donate/flutterwave/callback/', launch_views.donate_flutterwave_callback, name='donate_flutterwave_callback'),
    path('donate/paystack/', launch_views.donate_initiate_paystack, name='donate_initiate_paystack'),
    path('donate/paystack/callback/', launch_views.donate_paystack_callback, name='donate_paystack_callback'),
    path('donate/progress/', launch_views.donation_progress_api, name='donation_progress_api'),

    # --- 4. Generic Path (Lowest Priority — must stay LAST) ---
    path('<slug:slug>/review/', views.submit_review, name='submit_review'),
    path('<slug:slug>/', views.ProductDetailView.as_view(), name='product_detail'),
   ]