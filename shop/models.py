from django.db import models
from djmoney.models.fields import MoneyField
from django.contrib.auth import get_user_model
from django.db.models import JSONField
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone
from django.conf import settings
from djmoney.money import Money
import secrets
import datetime

User = get_user_model()

POD_SERVICES = [('PFT', 'Printful'), ('PFY', 'Printify')]
ORDER_STATUS_CHOICES = [
    ('PENDING', 'Pending Payment'),
    ('PENDING_SETTLEMENT', 'Paid — Awaiting Settlement'),
    ('SUBMITTING', 'Submitting to Production'),  # ← ADDED for worker concurrency locking
    ('POD_SENT', 'Sent to POD'),
    ('FULFILLED', 'Fulfilled by POD'), 
    ('SHIPPED', 'Shipped'), 
    ('CANCELLED', 'Cancelled'),
]


# ─────────────────────────────────────────────────────────
# LIVE EXCHANGE RATES (refreshed daily by update_exchange_rates command)
# ─────────────────────────────────────────────────────────

class ExchangeRate(models.Model):
    base_currency = models.CharField(max_length=3, default='USD')
    currency = models.CharField(max_length=3)
    rate = models.DecimalField(max_digits=12, decimal_places=6)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('base_currency', 'currency')

    def __str__(self):
        return f"{self.base_currency} -> {self.currency}: {self.rate}"


# ─────────────────────────────────────────────────────────
# PRODUCT REVIEWS (verified purchase only)
# ─────────────────────────────────────────────────────────

class Review(models.Model):
    RATING_CHOICES = [(i, str(i)) for i in range(1, 6)]

    product = models.ForeignKey('Product', related_name='reviews', on_delete=models.CASCADE)
    user = models.ForeignKey(User, related_name='reviews', on_delete=models.CASCADE)
    order_item = models.ForeignKey(
        'OrderItem', related_name='review', on_delete=models.SET_NULL, null=True, blank=True,
        help_text="The purchased order item this review is attached to — proof of verified purchase."
    )
    rating = models.PositiveSmallIntegerField(choices=RATING_CHOICES)
    title = models.CharField(max_length=150, blank=True)
    comment = models.TextField(blank=True)
    is_approved = models.BooleanField(default=True, help_text="Uncheck to hide a review from the public without deleting it.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('product', 'user')
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.product.name} — {self.rating}★ by {self.user}"


class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)

    def __str__(self):
        return self.name


class Product(models.Model):
    categories = models.ManyToManyField(Category, related_name='products', blank=True)
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200, unique=True)
    description = models.TextField(blank=True)
    price = MoneyField(max_digits=14, decimal_places=2, default_currency='USD')
    image_url = models.URLField(max_length=500, blank=True, null=True)
    image_file = models.ImageField(upload_to='products/main/', blank=True, null=True)
    print_areas = JSONField(default=list, blank=True)
    available = models.BooleanField(default=True)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)
    pod_id = models.CharField(max_length=100, blank=True, null=True)
    pod_service = models.CharField(max_length=20, choices=POD_SERVICES, blank=True)
    is_customizable = models.BooleanField(
        default=False,
        help_text="Auto-set during sync when the Printful title is prefixed with 'c#'. "
                  "Marks this as a blank garment customers can request custom designs on, "
                  "instead of a finished admin-designed listing."
    )

    def __str__(self):
        return self.name


class ProductVariant(models.Model):
    product = models.ForeignKey(Product, related_name='variants', on_delete=models.CASCADE)
    pod_id = models.CharField(max_length=100, unique=True)
    size = models.CharField(max_length=50, blank=True, null=True)
    color = models.CharField(max_length=50, blank=True, null=True)
    price = MoneyField(max_digits=14, decimal_places=2, default_currency='USD')
    available = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.product.name} ({self.size} / {self.color})"


# ─────────────────────────────────────────────────────────
# PROXY MODELS FOR SEPARATED ADMIN MANAGEMENT
# ─────────────────────────────────────────────────────────

class RegularProduct(Product):
    """Proxy model for already customized / regular storefront products."""
    class Meta:
        proxy = True
        verbose_name = 'Customized Product'
        verbose_name_plural = 'Customized Products'


class CustomizableBlankProduct(Product):
    """Proxy model for customizable blank garments (c# prefix)."""
    class Meta:
        proxy = True
        verbose_name = 'Customizable Blank Garment'
        verbose_name_plural = 'Customizable Blank Garments'


class Order(models.Model):
    PAYMENT_STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('PAID', 'Paid'),
        ('FAILED', 'Failed'),
        ('REFUNDED', 'Refunded'),
    ]
    FULFILLMENT_STATUS_CHOICES = [
        ('AWAITING_PAYMENT', 'Awaiting Payment'),
        ('PREPARING', 'Preparing - Design Concierge Review'),
        ('READY_FOR_PRODUCTION', 'Ready for Production'),
        ('IN_PRODUCTION', 'In Production'),
        ('SHIPPED', 'Shipped'),
        ('DELIVERED', 'Delivered'),
        ('CANCELLED', 'Cancelled'),
    ]

    user = models.ForeignKey(User, related_name='orders', on_delete=models.CASCADE)
    first_name = models.CharField(max_length=50)
    last_name = models.CharField(max_length=50)
    email = models.EmailField()
    phone = models.CharField(max_length=20, blank=True, null=True)
    address = models.CharField(max_length=250)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=100, blank=True, null=True)
    country = models.CharField(max_length=100, default='NG')
    postal_code = models.CharField(max_length=20)
    shipping_cost = MoneyField(max_digits=14, decimal_places=2, default_currency='USD', default=0)
    paid = models.BooleanField(default=False)
    status = models.CharField(max_length=20, choices=ORDER_STATUS_CHOICES, default='PENDING')
    
    customer_email = models.EmailField(blank=True, default='')
    customer_name = models.CharField(max_length=150, blank=True, default='')
    shipping_address = models.TextField(blank=True, default='')
    total_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    currency = models.CharField(max_length=3, default='USD')
    paystack_reference = models.CharField(max_length=255, unique=True, db_index=True, null=True, blank=True)
    flutterwave_reference = models.CharField(max_length=255, unique=True, db_index=True, null=True, blank=True)
    payment_status = models.CharField(
        max_length=20,
        choices=PAYMENT_STATUS_CHOICES,
        default='PENDING',
    )
    fulfillment_status = models.CharField(
        max_length=25,
        choices=FULFILLMENT_STATUS_CHOICES,
        default='AWAITING_PAYMENT',
    )
    abandonment_email_sent_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Set once a cart-abandonment reminder has been sent for this order, to avoid emailing twice."
    )
    settlement_release_at = models.DateTimeField(
        null=True, blank=True,
        help_text=(
            "Set when payment is confirmed. The order is held at status "
            "'PENDING_SETTLEMENT' and is not sent to Printful until this "
            "time passes — see the release_settled_orders management command."
        ),
    )
    pod_order_id = models.CharField(max_length=100, blank=True, null=True)
    tracking_number = models.CharField(max_length=200, blank=True, null=True)
    tracking_url = models.URLField(max_length=500, blank=True, null=True)
    carrier = models.CharField(max_length=100, blank=True, null=True)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "-created"]),
            models.Index(fields=["status", "paid", "settlement_release_at"]),
        ]

    def __str__(self):
        return f'Order {self.id}'

    def mark_paid(self, gateway_reference, gateway_name=''):
        """Unified, atomic payment completion handler for webhooks and callbacks."""
        if self.paid:
            return

        self.paid = True
        self.status = 'PENDING_SETTLEMENT'
        self.payment_status = 'PAID'
        self.fulfillment_status = 'PREPARING'
        
        if gateway_name.lower() == 'paystack':
            self.paystack_reference = gateway_reference
        elif gateway_name.lower() in ('flutterwave', 'flw'):
            self.flutterwave_reference = gateway_reference

        delay_hours = getattr(settings, 'SETTLEMENT_DELAY_HOURS', 24)
        self.settlement_release_at = timezone.now() + datetime.timedelta(hours=delay_hours)
        self.save(update_fields=[
            'paid', 'status', 'payment_status', 'fulfillment_status',
            'paystack_reference', 'flutterwave_reference', 'settlement_release_at', 'updated'
        ])

    def get_items_subtotal(self):
        """Return the order-item subtotal in the order's currency."""
        from .utils import get_converted_money

        return sum(
            (
                get_converted_money(item.get_cost(), self.currency)
                for item in self.items.all()
            ),
            Money(0, self.currency),
        )

    def get_total_cost(self):
        """Return the item subtotal plus shipping."""
        from .utils import get_converted_money

        shipping = get_converted_money(self.shipping_cost, self.currency)
        return self.get_items_subtotal() + shipping


class OrderItem(models.Model):
    order = models.ForeignKey(Order, related_name='items', on_delete=models.CASCADE)
    product = models.ForeignKey(Product, related_name='product_order_items', on_delete=models.PROTECT)
    product_variant = models.ForeignKey(ProductVariant, related_name='order_items', on_delete=models.PROTECT, null=True, blank=True)
    price = MoneyField(max_digits=14, decimal_places=2, default_currency='USD')
    quantity = models.PositiveIntegerField(default=1)

    def get_cost(self):
        return self.price * self.quantity  # returns a Money object


class VideoAd(models.Model):
    title = models.CharField(max_length=100)
    video_file = models.FileField(upload_to='ads/videos/')
    is_active = models.BooleanField(default=True)
    placement = models.CharField(max_length=20, default='LIST')
    created_at = models.DateTimeField(auto_now_add=True)


class CustomOrderRequest(models.Model):
    full_name = models.CharField(max_length=150)
    email = models.EmailField()
    product_type = models.CharField(max_length=20)
    size = models.CharField(max_length=20)
    quantity = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=20, default='NEW')
    created = models.DateTimeField(auto_now_add=True)


class PasswordResetOTP(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='password_otps')
    code = models.CharField(max_length=6)
    created_at = models.DateTimeField(auto_now_add=True)
    is_used = models.BooleanField(default=False)

    @classmethod
    def generate_code(cls, user):
        secure_code = secrets.randbelow(900000) + 100000
        return cls.objects.create(user=user, code=str(secure_code))

    def is_valid(self):
        return not self.is_used and timezone.now() <= self.created_at + timezone.timedelta(minutes=5)


class SupportChat(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='support_chat')
    human_escalated = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class ChatMessage(models.Model):
    chat = models.ForeignKey(SupportChat, on_delete=models.CASCADE, related_name='messages')
    sender_type = models.CharField(max_length=10, choices=[('user', 'User'), ('admin', 'Admin')])
    text = models.TextField()
    twilio_message_sid = models.CharField(max_length=34, unique=True, null=True, blank=True)
    image_field = models.FileField(upload_to='chat_uploads/', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    submission = models.ForeignKey('DesignSubmission', on_delete=models.SET_NULL, null=True, blank=True)


@receiver(post_save, sender=ChatMessage)
def notify_admin_of_customer_chat_message(sender, instance, created, **kwargs):
    if not created:
        return

    SupportChat.objects.filter(pk=instance.chat_id).update(updated_at=timezone.now())
    if instance.sender_type != 'user':
        return

    user = instance.chat.user
    customer_name = user.get_full_name().strip() or user.get_username()
    customer_email = user.email
    message_content = instance.text

    from .services import is_local_chat_turn

    if is_local_chat_turn(message_content):
        return

    from .whatsapp import queue_whatsapp_notification

    queue_whatsapp_notification(
        customer_name,
        customer_email,
        message_content,
        message_id=instance.pk,
        chat_id=instance.chat_id,
    )


class DesignSubmission(models.Model):
    STATUS_CHOICES = (
        ('PENDING_REVIEW', 'Pending Admin Review'),
        ('PROOF_SENT', 'Proof Sent to Customer'),
        ('APPROVED', 'Approved by Customer'),
        ('REJECTED', 'Rejected / Needs Changes'),
        ('IN_PRODUCTION', 'In Production'),
    )
    chat = models.ForeignKey(SupportChat, on_delete=models.CASCADE, related_name='design_submissions')
    garment = models.CharField(max_length=100)
    placement_zone = models.CharField(max_length=100)
    product_variant = models.ForeignKey(ProductVariant, on_delete=models.SET_NULL, null=True)
    design_file = models.ImageField(upload_to='design_submissions/originals/')
    design_file_url = models.URLField(blank=True, null=True)
    proof_image = models.ImageField(upload_to='design_submissions/proofs/', blank=True, null=True)
    admin_note = models.TextField(blank=True)
    proof_status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING_REVIEW')
    order = models.OneToOneField(Order, on_delete=models.SET_NULL, null=True, blank=True)
    submitted_at = models.DateTimeField(auto_now_add=True)
    proof_sent_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class CustomDesignTicket(models.Model):
    STATUS_CHOICES = (
        ('Pending Design Team Review', 'Pending Design Team Review'),
        ('Mockup in Progress', 'Mockup in Progress'),
        ('Sent to Customer for Approval', 'Sent to Customer for Approval'),
        ('Approved & Ready for Production', 'Approved & Ready for Production'),
    )

    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    session_key = models.CharField(max_length=150, null=True, blank=True)

    garment_item = models.CharField(max_length=100, help_text="e.g. Premium Polo Shirt, Streetwear Cap")
    garment_color = models.CharField(max_length=50, null=True, blank=True)
    garment_size = models.CharField(max_length=20, null=True, blank=True)

    custom_text = models.TextField(help_text="The exact text the customer wants printed/embroidered.")
    typography_style = models.CharField(max_length=100, null=True, blank=True, help_text="e.g. Minimalist, Streetwear Gothic")
    placement = models.CharField(max_length=100, null=True, blank=True, help_text="e.g. Left Chest, Center Back")

    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default='Pending Design Team Review')
    design_team_mockup = models.ImageField(
        upload_to='design_mockups/',
        null=True,
        blank=True,
        help_text="Upload the finished PNG/JPG proof here to send it to the customer.",
    )
    printful_product_id = models.CharField(max_length=100, null=True, blank=True, help_text="Paste the Printful Product ID here.")
    invoice_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, help_text="Total price in NGN.")
    invoice_sent = models.BooleanField(default=False, help_text="True once payment link sent to customer.")
    linked_order = models.OneToOneField("Order", on_delete=models.SET_NULL, null=True, blank=True, related_name="custom_ticket")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Design Ticket #{self.id} | {self.garment_item} ({self.status})" 


@receiver(post_save, sender=CustomDesignTicket)
def push_mockup_to_chat(sender, instance, **kwargs):
    if not instance.design_team_mockup:
        return
    if not instance.user:
        return

    try:
        mockup_url = instance.design_team_mockup.url
    except Exception:
        return

    chat, _ = SupportChat.objects.get_or_create(user=instance.user)

    already_sent = chat.messages.filter(sender_type='admin').filter(
        Q(text__contains=mockup_url)
        | Q(text__contains='Your Custom Mockup Proof is Ready!')
    ).exists()
    if already_sent:
        return

    parts = [p for p in [instance.garment_color, instance.garment_size, instance.placement] if p]
    specs_line = f"**Specs:** {' · '.join(parts)}\n\n" if parts else ''

    message_text = (
        f"🎨 **Your Custom Mockup Proof is Ready!**\n\n"
        f"Our design team has reviewed your asset specifications and cooked up your layout draft. "
        f"Take a close look at the layout mockup below.\n\n"
        f"{specs_line}"
        f"👉 Reply with **'Approve'** to send it directly to production!\n"
        f"👉 Or type any tweaks or positioning changes you want adjusted."
    )

    ChatMessage.objects.create(
        chat=chat,
        sender_type='admin',
        text=message_text,
        image_field=instance.design_team_mockup,
    )
    from .services import notify_customer_of_admin_reply

    notify_customer_of_admin_reply(chat, 'Your custom design proof is ready to review in your Hoxobil chat.')


@receiver(post_save, sender=CustomDesignTicket)
def notify_new_design_request(sender, instance, created, **kwargs):
    if not created or not instance.user:
        return
    chat = SupportChat.objects.filter(user=instance.user).first()
    if not chat:
        return

    from .whatsapp import queue_whatsapp_notification

    specs = ', '.join(
        value for value in (instance.garment_item, instance.garment_color, instance.garment_size, instance.placement)
        if value
    )
    queue_whatsapp_notification(
        instance.user.get_full_name().strip() or instance.user.get_username(),
        instance.user.email,
        f'New design request submitted: {specs}. Request: {instance.custom_text}',
        chat_id=chat.pk,
    )


class BotKnowledge(models.Model):
    title = models.CharField(max_length=200, blank=True)
    keywords = models.TextField(
        help_text="Comma-separated keywords that trigger this answer. e.g. 'delivery time, how long, when will i get'"
    )
    answer = models.TextField(
        help_text="The exact response the bot will give when a customer asks this."
    )
    category = models.CharField(max_length=80, default='general')
    times_used = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Bot Knowledge'
        verbose_name_plural = 'Bot Knowledge Base'
        ordering = ['-times_used']

    def __str__(self):
        return self.title or f"KB #{self.id}: {self.keywords[:60]}"

    def get_keywords_list(self):
        return [k.strip().lower() for k in self.keywords.split(',') if k.strip()]


class UnknownQuestion(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending Review'),
        ('answered', 'Converted to Answer'),
        ('ignored', 'Ignored'),
    ]
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    message = models.TextField()
    session_step = models.CharField(max_length=50, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    converted_to = models.ForeignKey(BotKnowledge, on_delete=models.SET_NULL, null=True, blank=True, related_name='source_questions')
    asked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Unknown Question'
        verbose_name_plural = 'Unknown Questions (Train the Bot)'
        ordering = ['-asked_at']

    def __str__(self):
        return f"Unknown #{self.id}: {self.message[:80]}"


class NewsletterSubscriber(models.Model):
    email = models.EmailField(unique=True)
    subscribed_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['-subscribed_at']

    def __str__(self):
        return self.email
     
     
class Donation(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('SUCCESSFUL', 'Successful'),
        ('FAILED', 'Failed'),
    ]
    PROVIDER_CHOICES = [
        ('FLUTTERWAVE', 'Flutterwave'),
        ('PAYSTACK', 'Paystack'),
    ]

    name = models.CharField(max_length=150, blank=True)
    email = models.EmailField(blank=True)

    amount = models.DecimalField(max_digits=12, decimal_places=2)

    provider = models.CharField(max_length=20, choices=PROVIDER_CHOICES)
    reference = models.CharField(max_length=100, unique=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='PENDING')

    created_at = models.DateTimeField(auto_now_add=True)
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name or 'Anonymous'} — ₦{self.amount:,.0f} ({self.status})"