from django.db import models
from djmoney.models.fields import MoneyField
from django.contrib.auth import get_user_model
from django.db.models import JSONField
from django.utils import timezone
import random

User = get_user_model()

POD_SERVICES = [('PFT', 'Printful'), ('PFY', 'Printify')]
ORDER_STATUS_CHOICES = [
    ('PENDING', 'Pending Payment'),
    ('PENDING_SETTLEMENT', 'Paid — Awaiting Settlement'),
    ('POD_SENT', 'Sent to POD'),
    ('FULFILLED', 'Fulfilled by POD'), ('SHIPPED', 'Shipped'), ('CANCELLED', 'Cancelled'),
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


class Order(models.Model):
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

    def __str__(self):
        return f'Order {self.id}'


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
        return cls.objects.create(user=user, code=f"{random.randint(100000, 999999)}")

    def is_valid(self):
        return not self.is_used and timezone.now() <= self.created_at + timezone.timedelta(minutes=5)


class SupportChat(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='support_chat')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class ChatMessage(models.Model):
    chat = models.ForeignKey(SupportChat, on_delete=models.CASCADE, related_name='messages')
    sender_type = models.CharField(max_length=10, choices=[('user', 'User'), ('admin', 'Admin')])
    text = models.TextField()
    image_field = models.ImageField(upload_to='chat_uploads/', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    submission = models.ForeignKey('DesignSubmission', on_delete=models.SET_NULL, null=True, blank=True)


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


# ─────────────────────────────────────────────────────────
# CUSTOM DESIGN TICKET MODEL (For the Design Team)
# ─────────────────────────────────────────────────────────

class CustomDesignTicket(models.Model):
    STATUS_CHOICES = (
        ('Pending Design Team Review', 'Pending Design Team Review'),
        ('Mockup in Progress', 'Mockup in Progress'),
        ('Sent to Customer for Approval', 'Sent to Customer for Approval'),
        ('Approved & Ready for Production', 'Approved & Ready for Production'),
    )

    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    session_key = models.CharField(max_length=150, null=True, blank=True)

    # The Blank Garment Specs
    garment_item = models.CharField(max_length=100, help_text="e.g. Premium Polo Shirt, Streetwear Cap")
    garment_color = models.CharField(max_length=50, null=True, blank=True)
    garment_size = models.CharField(max_length=20, null=True, blank=True)

    # The Customer's Custom Request
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


# ─────────────────────────────────────────────────────────
# SIGNAL: Auto-push admin mockup into the customer's chat
# ─────────────────────────────────────────────────────────

from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(post_save, sender=CustomDesignTicket)
def push_mockup_to_chat(sender, instance, **kwargs):
    """
    When an admin uploads a design_team_mockup to a CustomDesignTicket,
    automatically write a ChatMessage into the customer's SupportChat so
    the image appears inline in their chat window.

    Fires on every save but only acts when:
      1. A mockup image is present on the ticket
      2. The ticket belongs to a real user (not anonymous)
      3. That exact mockup URL hasn't already been posted into this chat
    """
    if not instance.design_team_mockup:
        return
    if not instance.user:
        return

    # Build the public-facing URL for the uploaded mockup file
    try:
        mockup_url = instance.design_team_mockup.url  # e.g. /media/design_mockups/proof.png
    except Exception:
        return

    # Resolve the customer's support chat
    try:
        chat = SupportChat.objects.get(user=instance.user)
    except SupportChat.DoesNotExist:
        return

    # De-duplication guard — never post the same image URL twice
    already_sent = chat.messages.filter(
        sender_type='admin',
        text__contains=mockup_url,
    ).exists()
    if already_sent:
        return

    # Build a readable specs line from whatever fields are filled in
    parts = [p for p in [instance.garment_color, instance.garment_size, instance.placement] if p]
    specs_line = f"**Specs:** {' · '.join(parts)}\n\n" if parts else ''

    garment_label = instance.garment_item or 'your garment'

    message_text = (
        f"🎨 **Your Custom Mockup Proof is Ready!**\n\n"
        f"Our design team has reviewed your asset specifications and cooked up your layout draft. "
        f"Take a close look at the layout mockup below.\n\n"
        f"{specs_line}"
        f"![{garment_label} Mockup]({mockup_url})\n\n"
        f"👉 Reply with **'Approve'** to send it directly to production!\n"
        f"👉 Or type any tweaks or positioning changes you want adjusted."
    )

    ChatMessage.objects.create(
        chat=chat,
        sender_type='admin',
        text=message_text,
    )

# ─────────────────────────────────────────────────────────
# SELF-LEARNING BOT MODELS
# ─────────────────────────────────────────────────────────

class BotKnowledge(models.Model):
    keywords = models.TextField(
        help_text="Comma-separated keywords that trigger this answer. e.g. 'delivery time, how long, when will i get'"
    )
    answer = models.TextField(
        help_text="The exact response the bot will give when a customer asks this."
    )
    times_used = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Bot Knowledge'
        verbose_name_plural = 'Bot Knowledge Base'
        ordering = ['-times_used']

    def __str__(self):
        return f"KB #{self.id}: {self.keywords[:60]}"

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


# ─────────────────────────────────────────────────────────
# DONATIONS (Launch Fund)
# ─────────────────────────────────────────────────────────

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

    # Stored in NGN — donations are naira-only, no currency conversion needed.
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