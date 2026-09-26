import json
import logging
from decimal import Decimal, InvalidOperation
from pathlib import Path
from django.contrib import admin
from django.utils.safestring import mark_safe
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils.html import format_html
from django.contrib import messages
from django.db.models import Q
from django.views.decorators.http import require_POST
from django.utils.decorators import method_decorator

from .models import (
    Product, Category, Order, OrderItem, ProductVariant,
    VideoAd, CustomOrderRequest, SupportChat, ChatMessage, DesignSubmission,
    CustomDesignTicket, BotKnowledge, UnknownQuestion, Review,
    RegularProduct, CustomizableBlankProduct,
)
from .pod_api import PodApiClient
from .fulfillment import submit_regular_order_to_printful

logger = logging.getLogger(__name__)


def trigger_printful_api(order):
    """Real or queueable trigger for Printful order submission."""
    client = PodApiClient('PFT')
    # Connect order fulfillment payload generation here safely
    return client


def _drop_invoice_into_chat(ticket, request=None, base_url=None):
    """
    Drops a price breakdown + inline Add-to-Cart widget into the customer's chat.
    The [ADD_TO_CART:ticketId:amount] token is detected by chat_support.html JS
    and rendered as a cart button — no separate checkout page needed.
    Marks invoice_sent = True so it can never be sent twice.
    """
    invoice_amount = getattr(ticket, 'invoice_amount', None)
    if not invoice_amount or not ticket.user or ticket.invoice_sent:
        return False

    chat, _ = SupportChat.objects.get_or_create(user=ticket.user)

    product_ngn  = getattr(ticket, 'product_price_ngn', None)
    shipping_ngn = getattr(ticket, 'shipping_cost_ngn', None)

    if product_ngn and shipping_ngn:
        breakdown = (
            f"\n\n📋 **Price Breakdown:**\n"
            f"• Garment cost: ₦{product_ngn:,}\n"
            f"• Shipping: ₦{shipping_ngn:,}\n"
            f"• **Total: ₦{invoice_amount:,}**"
        )
    else:
        breakdown = f"\n\n**Total: ₦{invoice_amount:,}**"

    ChatMessage.objects.create(
        chat=chat,
        sender_type='admin',
        text=(
            f"💳 **Your Custom Order Invoice is Ready!**\n\n"
            f"Your custom **{ticket.garment_item}** has been priced and is ready to order."
            f"{breakdown}\n\n"
            f"👇 Tap **Add to Cart** below, then head to your cart to complete payment "
            f"together with any other items.\n\n"
            f"[ADD_TO_CART:{ticket.id}:{invoice_amount}]"
        )
    )
    from .services import notify_customer_of_admin_reply

    notify_customer_of_admin_reply(chat, 'Your custom order invoice is ready in your Hoxobil chat.')

    ticket.status = 'Approved & Ready for Production'
    ticket.invoice_sent = True
    ticket.save(update_fields=['status', 'invoice_sent'])
    return True


# ─────────────────────────────────────────────────────────
# INLINES
# ─────────────────────────────────────────────────────────

class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 1
    fields = ('size', 'color', 'price', 'pod_id', 'available')
    readonly_fields = ('pod_id',)


class DesignSubmissionInline(admin.TabularInline):
    model = DesignSubmission
    extra = 0
    readonly_fields = ('submitted_at', 'updated_at')


# ─────────────────────────────────────────────────────────
# ADMIN CLASSES
# ─────────────────────────────────────────────────────────

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug', 'featured_indicator']
    prepopulated_fields = {'slug': ('name',)}
    
    def featured_indicator(self, obj):
        """Display star indicator for featured categories."""
        featured_slugs = ['new-drop', 'trending', 'limited', 'outerwear', 'editorial']
        if obj.slug in featured_slugs:
            return mark_safe(
                '<span style="color: #c0392b; font-weight: 900; font-size: 1.2em;">★ FEATURED</span>'
            )
        return '-'
    featured_indicator.short_description = 'Featured'


class BaseProductAdmin(admin.ModelAdmin):
    list_display = [
        'product_thumbnail', 'name', 'price', 'display_categories',
        'available', 'pod_id', 'pod_service', 'created', 'publish_action_button',
    ]
    list_filter = ['available', 'categories']
    list_editable = ['price', 'available']
    filter_horizontal = ('categories',)
    prepopulated_fields = {'slug': ('name',)}
    search_fields = ['name', 'pod_id']
    fields = (
        'name', 'slug', 'categories', 'price', 'pod_service', 'pod_id',
        'available', 'description', 'image_url', 'image_file', 'print_areas'
    )
    inlines = [ProductVariantInline]

    @admin.display(description='Image')
    def product_thumbnail(self, obj):
        if obj.image_file:
            image_url = obj.image_file.url
        elif obj.image_url:
            image_url = obj.image_url
        else:
            return '-'

        return format_html(
            '<img src="{}" width="40" height="40" alt="{}" '
            'style="object-fit: cover; border-radius: 4px;" />',
            image_url,
            obj.name,
        )

    @admin.display(description='Categories')
    def display_categories(self, obj):
        return ', '.join(obj.categories.values_list('name', flat=True))

    @admin.display(description='Actions')
    def publish_action_button(self, obj):
        if not obj.pk or not obj.pod_id:
            return '-'

        url = reverse('admin:shop_product_publish', args=[obj.pk])
        return format_html(
            '<form action="{}" method="POST" style="display:inline;">'
            '<input type="hidden" name="csrfmiddlewaretoken" value="{}">'
            '<button type="submit" class="button" style="'
            'background:#417690;padding:6px 12px;color:white;'
            'border-radius:4px;border:none;cursor:pointer;font-weight:bold;">'
            'Publish</button></form>',
            url,
            self.get_csrf_token(obj) if hasattr(self, 'get_csrf_token') else ''
        )

    def get_csrf_token(self, obj):
        return ''

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                '<int:product_id>/publish/',
                self.admin_site.admin_view(self.publish_product_view),
                name='shop_product_publish',
            ),
        ]
        return custom_urls + urls

    @method_decorator(require_POST)
    def publish_product_view(self, request, product_id):
        product = get_object_or_404(Product, pk=product_id)
        provider = product.get_pod_service_display() or product.pod_service or 'POD'
        product.available = True
        product.save(update_fields=['available', 'updated'])
        self.message_user(
            request,
            f'Product "{product.name}" is now visible on the storefront '
            f'and managed by {provider}.',
            messages.SUCCESS,
        )
        return redirect('admin:shop_product_changelist')

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        form.save_m2m()

        if change and obj.pod_service == 'PFT' and obj.pod_id:
            client = PodApiClient('PFT')
            synced, error = client.update_store_product(
                obj.pod_id,
                name=obj.name,
                description=obj.description,
            )
            if synced:
                self.message_user(
                    request,
                    f'Product "{obj.name}" saved locally and synced to Printful.',
                    messages.SUCCESS,
                )
            else:
                self.message_user(
                    request,
                    f'Product saved locally, but Printful sync failed: {error}',
                    messages.ERROR,
                )

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        form.save_m2m()


@admin.register(RegularProduct)
class RegularProductAdmin(BaseProductAdmin):
    def get_queryset(self, request):
        return super().get_queryset(request).filter(is_customizable=False)

    def save_model(self, request, obj, form, change):
        obj.is_customizable = False
        super().save_model(request, obj, form, change)


@admin.register(CustomizableBlankProduct)
class CustomizableBlankProductAdmin(BaseProductAdmin):
    def get_queryset(self, request):
        return super().get_queryset(request).filter(is_customizable=True)

    def save_model(self, request, obj, form, change):
        obj.is_customizable = True
        super().save_model(request, obj, form, change)


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ['id', 'first_name', 'last_name', 'email', 'status', 'paid', 'created']
    list_filter = ['paid', 'status', 'created']
    search_fields = ['first_name', 'last_name', 'email', 'pod_order_id']

    # ── Clean, organized layout using fieldsets to reduce clutter ──
    fieldsets = (
        ('Customer Information', {
            'fields': ('first_name', 'last_name', 'email', 'phone')
        }),
        ('Delivery Address', {
            'fields': ('address', 'city', 'state', 'country', 'postal_code', 'shipping_cost')
        }),
        ('Financial & Gateway Status', {
            'fields': ('paid', 'status', 'payment_status', 'total_amount', 'currency', 'paystack_reference', 'flutterwave_reference')
        }),
        ('Fulfillment & Production', {
            'fields': ('fulfillment_status', 'pod_order_id', 'settlement_release_at'),
            'description': 'Manage manual release to Printful and track production/shipping status.'
        }),
        ('Tracking & Logistics (Optional)', {
            'classes': ('collapse',),
            'fields': ('tracking_number', 'tracking_url', 'carrier', 'abandonment_email_sent_at')
        }),
    )

    readonly_fields = ('paystack_reference', 'flutterwave_reference', 'total_amount', 'currency')

    @admin.action(description='Send Selected Orders to Printful')
    def push_orders_to_printful(self, request, queryset):
        """Manually submit paid, unsent orders after confirming available cash."""
        selected_count = queryset.count()
        eligible_orders = queryset.filter(paid=True).filter(
            Q(pod_order_id__isnull=True) | Q(pod_order_id=''),
        ).prefetch_related(
            'items__product_variant',
        )

        eligible_ids = set(eligible_orders.values_list('id', flat=True))
        skipped_count = selected_count - len(eligible_ids)
        submitted_count = 0

        for order in eligible_orders:
            try:
                success, error = submit_regular_order_to_printful(order)
                if not success:
                    raise RuntimeError(error or 'Printful rejected the order.')

                order.refresh_from_db(fields=['pod_order_id', 'status'])
                order.status = 'POD_SENT'
                order.fulfillment_status = 'IN_PRODUCTION'
                order.save(update_fields=['status', 'fulfillment_status', 'updated'])
                submitted_count += 1
            except Exception as exc:
                logger.exception(
                    'OrderAdmin.push_orders_to_printful failed for order %s',
                    order.id,
                )
                self.message_user(
                    request,
                    f'Order #{order.id} failed to send to Printful: {exc}',
                    messages.ERROR,
                )

        if submitted_count:
            self.message_user(
                request,
                f'{submitted_count} paid order(s) sent to Printful.',
                messages.SUCCESS,
            )
        if skipped_count:
            self.message_user(
                request,
                f'{skipped_count} selected order(s) skipped: they must be paid and not already sent to Printful.',
                messages.WARNING,
            )

    actions = ['push_orders_to_printful']


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ['order', 'product', 'product_variant', 'price', 'quantity']
    search_fields = ['product__name', 'order__id', 'product_variant__pod_id']


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ['product', 'user', 'rating', 'is_approved', 'created_at']
    list_filter = ['rating', 'is_approved', 'created_at']
    search_fields = ['product__name', 'user__username', 'user__email', 'title', 'comment']
    list_editable = ['is_approved']
    readonly_fields = ['product', 'user', 'order_item', 'rating', 'title', 'comment', 'created_at', 'updated_at']


@admin.register(VideoAd)
class VideoAdAdmin(admin.ModelAdmin):
    list_display = ('title', 'placement', 'is_active', 'created_at')
    list_filter = ('is_active', 'placement')
    search_fields = ('title',)
    list_editable = ('is_active', 'placement')


@admin.register(CustomOrderRequest)
class CustomOrderRequestAdmin(admin.ModelAdmin):
    list_display  = ['full_name', 'email', 'product_type', 'size', 'quantity', 'status', 'created']
    list_filter   = ['status', 'product_type', 'created']
    search_fields = ['full_name', 'email']
    readonly_fields = ['created']
    list_editable = ['status']


@admin.register(SupportChat)
class SupportChatAdmin(admin.ModelAdmin):
    list_display = ['user', 'human_escalated', 'created_at', 'updated_at', 'open_chat']
    search_fields = ['user__username', 'user__email', 'user__first_name']
    list_filter = ['human_escalated', 'created_at']
    inlines = [DesignSubmissionInline]

    def open_chat(self, obj):
        return format_html(
            '<a class="button" href="{}">Open conversation</a>',
            reverse('admin_chat_detail', args=[obj.pk]),
        )
    open_chat.short_description = 'Conversation'


@admin.register(DesignSubmission)
class DesignSubmissionAdmin(admin.ModelAdmin):
    list_display = ['chat', 'proof_status', 'submitted_at']
    list_filter = ['proof_status']
    readonly_fields = ['submitted_at', 'updated_at']


@admin.register(CustomDesignTicket)
class CustomDesignTicketAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'garment_item', 'user', 'status', 'garment_color',
        'garment_size', 'invoice_amount_display', 'chat_button', 'invoice_button'
    )
    list_filter = ('status', 'garment_item')
    search_fields = ('user__username', 'user__email', 'session_key', 'custom_text')

    fieldsets = (
        ('Customer Info', {
            'fields': ('user', 'session_key', 'status'),
            'description': 'Track user authentication records and current ticket lifecycle phase.'
        }),
        ('Blank Garment Specs', {
            'fields': ('garment_item', 'garment_color', 'garment_size'),
            'description': 'Base apparel inventory profiles selected by the customer.'
        }),
        ('Design Blueprint (Customer Request)', {
            'fields': ('custom_text', 'typography_style', 'placement'),
            'description': 'Custom configuration requests extracted from the automated chat conversation flow.'
        }),
        ('Printful Integration', {
            'fields': ('printful_product_id', 'fetch_price_button', 'fetch_mockup_button'),
            'description': (
                '1. Design the product on Printful dashboard. '
                '2. Paste the Printful Product ID here and save. '
                '3. Click "Fetch Price + Shipping" to auto-calculate the invoice amount. '
                '4. Click "Fetch Mockup from Printful" to pull the mockup and send it to the customer.'
            ),
        }),
        ('Design Team Action (Upload Mockup Manually)', {
            'fields': ('design_team_mockup', 'live_chat_panel'),
            'description': 'Alternative: upload a PNG/JPG proof manually. Saving pushes it into the customer\'s chat.',
            'classes': ('collapse',),
        }),
        ('Invoice & Payment', {
            'fields': ('invoice_amount', 'invoice_sent'),
            'description': (
                'Set the total price in NGN. '
                'Once the customer approves the mockup in chat, the payment link is sent automatically. '
                'You can also trigger it manually from the ticket list using the 💳 Send Invoice button.'
            ),
        }),
    )

    readonly_fields = ('live_chat_panel', 'fetch_mockup_button', 'fetch_price_button', 'invoice_sent')

    def fetch_price_button(self, obj):
        if not obj.pk:
            return mark_safe('<p style="color:#999;">Save the ticket first.</p>')
        if not obj.printful_product_id:
            return mark_safe('<p style="color:#999;">Enter a Printful Product ID above and save first.</p>')
        url = reverse('admin:shop_ticket_fetch_price', args=[obj.id])
        extra = ''
        if obj.invoice_amount:
            extra = f'<p style="margin-top:6px;font-size:12px;color:#555;">Current invoice: <strong>₦{obj.invoice_amount:,}</strong></p>'
        return format_html(
            '<form action="{}" method="POST" style="display:inline;">'
            '<input type="hidden" name="csrfmiddlewaretoken" value="{}">'
            '<button type="submit" class="button" style="'
            'background:#27ae60;color:#fff;padding:6px 14px;'
            'border-radius:4px;border:none;cursor:pointer;font-size:13px;font-weight:bold;">'
            '💰 Fetch Price + Shipping from Printful</button></form>{}',
            url,
            self.get_csrf_token(obj) if hasattr(self, 'get_csrf_token') else '',
            mark_safe(extra)
        )
    fetch_price_button.short_description = 'Price & Shipping'

    def fetch_mockup_button(self, obj):
        if not obj.pk:
            return mark_safe('<p style="color:#999;">Save the ticket first, then fetch the mockup.</p>')
        if not obj.printful_product_id:
            return mark_safe('<p style="color:#999;">Enter a Printful Product ID above and save first.</p>')
        url = reverse('admin:shop_ticket_fetch_mockup', args=[obj.id])
        return format_html(
            '<form action="{}" method="POST" style="display:inline;">'
            '<input type="hidden" name="csrfmiddlewaretoken" value="{}">'
            '<button type="submit" class="button" style="'
            'background:#2980b9;color:#fff;padding:6px 14px;'
            'border-radius:4px;border:none;cursor:pointer;font-size:13px;font-weight:bold;">'
            '🖼 Fetch Mockup from Printful</button></form>',
            url,
            self.get_csrf_token(obj) if hasattr(self, 'get_csrf_token') else ''
        )
    fetch_mockup_button.short_description = 'Printful Mockup'

    def invoice_amount_display(self, obj):
        amount = getattr(obj, 'invoice_amount', None)
        if amount:
            return f"₦{amount:,}"
        return '—'
    invoice_amount_display.short_description = 'Invoice'

    def invoice_button(self, obj):
        amount = getattr(obj, 'invoice_amount', None)
        if not amount or not obj.user:
            return '—'
        url = reverse('admin:shop_ticket_send_invoice', args=[obj.id])
        return format_html(
            '<form action="{}" method="POST" style="display:inline;">'
            '<input type="hidden" name="csrfmiddlewaretoken" value="{}">'
            '<button type="submit" class="button" style="'
            'background:#c0392b;color:#fff;padding:4px 10px;'
            'border-radius:4px;border:none;cursor:pointer;font-size:12px;">'
            '💳 Send Invoice</button></form>',
            url,
            self.get_csrf_token(obj) if hasattr(self, 'get_csrf_token') else ''
        )
    invoice_button.short_description = 'Invoice Action'

    def chat_button(self, obj):
        if not obj.user:
            return '—'
        chat = SupportChat.objects.filter(user=obj.user).first()
        if not chat:
            return '—'
        url = reverse('admin_chat_detail', args=[chat.pk])
        return format_html(
            '<a class="button" href="{}" style="'
            'background:#1a7a4a;color:#fff;padding:4px 10px;'
            'border-radius:4px;text-decoration:none;font-size:12px;">'
            '💬 Open Chat</a>',
            url
        )
    chat_button.short_description = 'Chat'

    def live_chat_panel(self, obj):
        if not obj.user:
            return mark_safe('<p style="color:#999;">No user linked to this ticket.</p>')

        chat = SupportChat.objects.filter(user=obj.user).first()
        if not chat:
            return mark_safe('<p style="color:#999;">No chat session found for this customer.</p>')

        messages_qs = chat.messages.order_by('created_at')

        bubbles_html = ''
        for msg in messages_qs:
            is_admin = msg.sender_type == 'admin'
            align = 'flex-end' if is_admin else 'flex-start'
            bg    = '#1a7a4a' if is_admin else '#f0f0f0'
            color = '#fff'    if is_admin else '#222'
            label = '🛠 Design Team' if is_admin else f'👤 {obj.user.first_name or obj.user.username}'
            time_str = msg.created_at.strftime('%d %b, %H:%M') if msg.created_at else ''

            attachment_html = ''
            if msg.image_field:
                attachment_name = Path(msg.image_field.name).name
                attachment_html = format_html(
                    '<br><a href="{}" target="_blank" rel="noopener" download>{}</a>',
                    msg.image_field.url,
                    attachment_name,
                )
                if Path(attachment_name).suffix.lower() in {'.avif', '.gif', '.jpeg', '.jpg', '.png', '.webp'}:
                    attachment_html += format_html(
                        '<br><img src="{}" alt="{}" style="max-width:260px;border-radius:8px;margin-top:8px;">',
                        msg.image_field.url,
                        attachment_name,
                    )

            bubbles_html += format_html(
                '<div style="display:flex;justify-content:{};margin-bottom:12px;">'
                '<div style="max-width:70%;background:{};color:{};padding:10px 14px;border-radius:12px;font-size:13px;line-height:1.5;">'
                '<div style="font-size:11px;opacity:0.75;margin-bottom:4px;">{} · {}</div>'
                '{}'
                '{}'
                '</div></div>',
                align, bg, color, label, time_str, msg.text, attachment_html
            )

        if not messages_qs.exists():
            bubbles_html = mark_safe('<p style="color:#999;text-align:center;">No messages yet.</p>')

        reply_url = reverse('admin:shop_ticket_admin_reply', args=[obj.id])

        return format_html(
            '''
            <div id="chat-panel" style="border:1px solid #ddd;border-radius:10px;overflow:hidden;font-family:sans-serif;margin-top:8px;">
                <div style="background:#1a7a4a;color:#fff;padding:12px 16px;font-weight:bold;font-size:14px;">
                    💬 Live Chat — {} ({})
                </div>
                <div id="chat-messages" style="height:400px;overflow-y:auto;padding:16px;background:#fafafa;">
                    {}
                </div>
                <div style="border-top:1px solid #ddd;padding:12px;background:#fff;display:flex;gap:8px;align-items:flex-end;">
                    <textarea id="admin-reply-text" rows="2"
                        placeholder="Type your reply to the customer..."
                        style="flex:1;padding:10px;border:1px solid #ccc;border-radius:6px;font-size:13px;resize:vertical;font-family:sans-serif;"></textarea>
                    <button type="button" onclick="sendAdminReply('{}')"
                        style="background:#1a7a4a;color:#fff;border:none;padding:10px 18px;border-radius:6px;cursor:pointer;font-size:13px;font-weight:bold;">
                        Send ➤
                    </button>
                </div>
            </div>
            <script>
            function sendAdminReply(url) {
                var text = document.getElementById('admin-reply-text').value.trim();
                if (!text) return;
                fetch(url, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/x-www-form-urlencoded',
                        'X-CSRFToken': document.cookie.match(/csrftoken=([^;]+)/)[1]
                    },
                    body: 'message=' + encodeURIComponent(text)
                })
                .then(r => r.json())
                .then(data => {
                    if (data.status === 'ok') {
                        document.getElementById('admin-reply-text').value = '';
                        var feed = document.getElementById('chat-messages');
                        var now = new Date().toLocaleString('en-GB', {day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit'});
                        var bubbleRow = document.createElement('div');
                        bubbleRow.style.cssText = 'display:flex;justify-content:flex-end;margin-bottom:12px;';
                        var bubble = document.createElement('div');
                        bubble.style.cssText = 'max-width:70%;background:#1a7a4a;color:#fff;padding:10px 14px;border-radius:12px;font-size:13px;line-height:1.5;';
                        var metadata = document.createElement('div');
                        metadata.style.cssText = 'font-size:11px;opacity:0.75;margin-bottom:4px;';
                        metadata.textContent = '🛠 Design Team · ' + now;
                        bubble.appendChild(metadata);
                        bubble.appendChild(document.createTextNode(text));
                        bubbleRow.appendChild(bubble);
                        feed.appendChild(bubbleRow);
                        feed.scrollTop = feed.scrollHeight;
                    }
                })
                .catch(err => alert('Send failed: ' + err));
            }
            window.addEventListener('load', function() {
                var feed = document.getElementById('chat-messages');
                if (feed) feed.scrollTop = feed.scrollHeight;
            });
            </script>
            ''',
            obj.user.get_full_name() or obj.user.username,
            obj.user.email,
            mark_safe(bubbles_html),
            reply_url
        )

    live_chat_panel.short_description = 'Customer Chat'

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                '<int:ticket_id>/reply/',
                self.admin_site.admin_view(self.admin_reply_view),
                name='shop_ticket_admin_reply',
            ),
            path(
                '<int:ticket_id>/send-invoice/',
                self.admin_site.admin_view(self.send_invoice_view),
                name='shop_ticket_send_invoice',
            ),
            path(
                '<int:ticket_id>/fetch-mockup/',
                self.admin_site.admin_view(self.fetch_mockup_from_printful_view),
                name='shop_ticket_fetch_mockup',
            ),
            path(
                '<int:ticket_id>/fetch-price/',
                self.admin_site.admin_view(self.fetch_price_and_shipping_view),
                name='shop_ticket_fetch_price',
            ),
        ]
        return custom_urls + urls

    @method_decorator(require_POST)
    def fetch_price_and_shipping_view(self, request, ticket_id):
        import requests as _req
        from django.http import HttpResponseRedirect
        from django.conf import settings as _settings

        ticket = CustomDesignTicket.objects.get(id=ticket_id)
        redirect_url = reverse('admin:shop_customdesignticket_change', args=[ticket_id])

        if not ticket.printful_product_id:
            self.message_user(request, "Set a Printful Product ID first.", level='error')
            return HttpResponseRedirect(redirect_url)

        headers = {
            'Authorization': f'Bearer {_settings.PRINTFUL_ACCESS_TOKEN}',
            'X-PF-Store-Id': str(_settings.PRINTFUL_STORE_ID),
            'Content-Type': 'application/json',
        }

        product_price_usd  = Decimal('0')
        sync_variant_id    = None
        external_variant_id = None

        try:
            api_url = f'https://api.printful.com/store/products/{ticket.printful_product_id}'
            resp = _req.get(api_url, headers=headers, timeout=15)
            resp.raise_for_status()
            data = resp.json()

            sync_product = data.get('result', {})
            variants = sync_product.get('sync_variants', [])

            if not variants:
                self.message_user(request, "No variants found for this Printful product.", level='error')
                return HttpResponseRedirect(redirect_url)

            target_size = (ticket.garment_size or '').upper()
            matched_variant = None
            for v in variants:
                if target_size and target_size in (v.get('name') or '').upper():
                    matched_variant = v
                    break
            if not matched_variant:
                matched_variant = variants[0]

            sync_variant_id = matched_variant.get('id')
            external_variant_id = matched_variant.get('variant_id')

            retail_price = (
                matched_variant.get('retail_price')
                or sync_product.get('sync_product', {}).get('retail_price')
            )
            if retail_price:
                product_price_usd = Decimal(str(retail_price))

        except Exception as e:
            self.message_user(request, f"Failed to fetch product price from Printful: {e}", level='error')
            return HttpResponseRedirect(redirect_url)

        shipping_cost_usd = Decimal('0')
        shipping_rate_name = ''

        customer_order = None
        if ticket.user:
            customer_order = (
                Order.objects.filter(user=ticket.user)
                .exclude(address='')
                .order_by('-created')
                .first()
            )

        if customer_order:
            recipient_country = customer_order.country or ''
            recipient_zip     = customer_order.postal_code or ''
            recipient_state   = customer_order.state or ''
            recipient_city    = customer_order.city or ''
            recipient_address = customer_order.address or 'N/A'
        else:
            recipient_country = ''
            recipient_zip     = ''
            recipient_state   = ''
            recipient_city    = ''
            recipient_address = 'N/A'

        shipping_item = None
        if external_variant_id:
            shipping_item = {"variant_id": external_variant_id, "quantity": 1}
        elif sync_variant_id:
            shipping_item = {"sync_variant_id": sync_variant_id, "quantity": 1}

        if shipping_item and customer_order and recipient_country and recipient_city:
            try:
                shipping_payload = {
                    "recipient": {
                        "address1":     recipient_address,
                        "city":         recipient_city,
                        "country_code": recipient_country,
                        "zip":          recipient_zip,
                        "state_code":   recipient_state,
                    },
                    "items": [shipping_item]
                }
                ship_resp = _req.post(
                    'https://api.printful.com/shipping/rates',
                    headers=headers,
                    json=shipping_payload,
                    timeout=15,
                )
                ship_data = ship_resp.json()

                if ship_resp.status_code == 200 and ship_data.get('code') == 200:
                    rates = ship_data.get('result', [])
                    if rates:
                        cheapest = min(rates, key=lambda r: float(r.get('rate', 0)))
                        shipping_cost_usd = Decimal(str(cheapest.get('rate', '0')))
                        shipping_rate_name = cheapest.get('name', '')
                else:
                    err = ship_data.get('error', {})
                    err_msg = err.get('message', str(err)) if isinstance(err, dict) else str(err)
                    self.message_user(
                        request,
                        f"Shipping estimate unavailable ({err_msg}). Product price fetched — shipping set to ₦0.",
                        level='warning'
                    )
            except Exception as e:
                self.message_user(
                    request,
                    f"Shipping API error: {e}. Product price fetched — shipping set to ₦0.",
                    level='warning'
                )
        else:
            self.message_user(
                request,
                "Could not determine variant ID for shipping. Product price fetched — shipping set to ₦0.",
                level='warning'
            )

        try:
            rates_map = getattr(_settings, 'CASH_EXCHANGE_BACKEND', {}).get('USD', {})
            ngn_rate  = Decimal(str(rates_map.get('NGN', 1500)))
        except Exception:
            ngn_rate = Decimal('1500')

        product_ngn  = (product_price_usd * ngn_rate).quantize(Decimal('1'))
        shipping_ngn = (shipping_cost_usd * ngn_rate).quantize(Decimal('1'))
        total_ngn    = product_ngn + shipping_ngn

        ticket.invoice_amount = total_ngn
        if hasattr(ticket, 'product_price_ngn'):
            ticket.product_price_ngn = product_ngn
        if hasattr(ticket, 'shipping_cost_ngn'):
            ticket.shipping_cost_ngn = shipping_ngn

        save_fields = ['invoice_amount']
        if hasattr(ticket, 'product_price_ngn'):
            save_fields.append('product_price_ngn')
        if hasattr(ticket, 'shipping_cost_ngn'):
            save_fields.append('shipping_cost_ngn')
        ticket.save(update_fields=save_fields)

        parts = [f"Product: ${product_price_usd} (₦{product_ngn:,})"]
        if shipping_cost_usd:
            parts.append(f"Shipping ({shipping_rate_name}): ${shipping_cost_usd} (₦{shipping_ngn:,})")
        else:
            parts.append("Shipping: not available (₦0)")
        parts.append(f"Rate: ₦{ngn_rate:,}/USD → Invoice: ₦{total_ngn:,}")
        if customer_order:
            parts.append(f"Ship-to: {recipient_city}, {recipient_country}")
        else:
            parts.append("No customer shipping details on file — shipping estimate skipped")

        self.message_user(request, "✅ " + " · ".join(parts), level='success')
        return HttpResponseRedirect(redirect_url)

    @method_decorator(require_POST)
    def fetch_mockup_from_printful_view(self, request, ticket_id):
        import requests as _req
        from django.core.files.base import ContentFile
        from django.http import HttpResponseRedirect

        ticket = CustomDesignTicket.objects.get(id=ticket_id)

        if not ticket.printful_product_id:
            self.message_user(request, "Set a Printful Product ID on the ticket first.", level='error')
            return HttpResponseRedirect(reverse('admin:shop_customdesignticket_change', args=[ticket_id]))

        try:
            from django.conf import settings as _settings
            headers = {
                'Authorization': f'Bearer {_settings.PRINTFUL_ACCESS_TOKEN}',
                'X-PF-Store-Id': str(_settings.PRINTFUL_STORE_ID),
            }
            api_url = f'https://api.printful.com/store/products/{ticket.printful_product_id}'
            resp = _req.get(api_url, headers=headers, timeout=15)
            resp.raise_for_status()
            data = resp.json()

            sync_product = data.get('result', {})
            mockup_url = None
            mockup_url = sync_product.get('sync_product', {}).get('thumbnail_url')

            if not mockup_url:
                variants = sync_product.get('sync_variants', [])
                for variant in variants:
                    for f in variant.get('files', []):
                        if f.get('type') == 'preview':
                            mockup_url = f.get('preview_url') or f.get('url')
                            break
                        elif f.get('type') == 'default':
                            mockup_url = f.get('preview_url') or f.get('url')
                    if mockup_url:
                        break

            if not mockup_url:
                self.message_user(
                    request,
                    "Printful returned no mockup image for this product. Make sure the product has a mockup generated.",
                    level='error'
                )
                return HttpResponseRedirect(reverse('admin:shop_customdesignticket_change', args=[ticket_id]))

            if not ticket.invoice_amount:
                try:
                    retail_price = sync_product.get('sync_product', {}).get('retail_price')
                    if not retail_price and variants:
                        retail_price = variants[0].get('retail_price')
                    if retail_price:
                        from decimal import Decimal as _Decimal
                        ticket.invoice_amount = _Decimal(str(retail_price))
                except Exception:
                    pass

            img_resp = _req.get(mockup_url, timeout=20)
            img_resp.raise_for_status()

            content_type = img_resp.headers.get('Content-Type', 'image/png')
            ext = 'jpg' if 'jpeg' in content_type else 'png'
            filename = f'printful_mockup_{ticket.id}.{ext}'

            ticket.design_team_mockup.save(filename, ContentFile(img_resp.content), save=False)
            ticket.status = 'Sent to Customer for Approval'
            ticket.save(update_fields=['design_team_mockup', 'status', 'invoice_amount'])

            self.message_user(
                request,
                "Mockup fetched from Printful and sent to customer's chat successfully.",
                level='success'
            )

        except Exception as e:
            self.message_user(request, f"Failed to fetch mockup from Printful: {e}", level='error')

        return HttpResponseRedirect(reverse('admin:shop_customdesignticket_change', args=[ticket_id]))

    @method_decorator(require_POST)
    def send_invoice_view(self, request, ticket_id):
        from django.http import HttpResponseRedirect
        ticket = CustomDesignTicket.objects.get(id=ticket_id)
        invoice_amount = getattr(ticket, 'invoice_amount', None)

        if not invoice_amount:
            self.message_user(request, "Set an invoice amount on the ticket first.", level='error')
            return HttpResponseRedirect(reverse('admin:shop_customdesignticket_change', args=[ticket_id]))

        if not ticket.user:
            self.message_user(request, "No user linked to this ticket.", level='error')
            return HttpResponseRedirect(reverse('admin:shop_customdesignticket_change', args=[ticket_id]))

        if ticket.invoice_sent:
            self.message_user(request, "Invoice already sent. Check the customer's chat.", level='warning')
            return HttpResponseRedirect(reverse('admin:shop_customdesignticket_change', args=[ticket_id]))

        _drop_invoice_into_chat(ticket, request)
        self.message_user(
            request,
            f"Invoice of ₦{invoice_amount:,} sent to {ticket.user.email} in chat.",
            level='success'
        )
        return HttpResponseRedirect(reverse('admin:shop_customdesignticket_change', args=[ticket_id]))

    @method_decorator(require_POST)
    def admin_reply_view(self, request, ticket_id):
        from django.http import JsonResponse as _JsonResponse
        from .services import learn_from_admin_reply, notify_customer_of_admin_reply

        text = request.POST.get('message', '').strip()
        attachment = request.FILES.get('attachment')
        if not text and not attachment:
            return _JsonResponse({'status': 'error', 'message': 'Empty message'}, status=400)

        try:
            ticket = CustomDesignTicket.objects.get(id=ticket_id)
        except CustomDesignTicket.DoesNotExist:
            return _JsonResponse({'status': 'error', 'message': 'Ticket not found'}, status=404)

        if not ticket.user:
            return _JsonResponse({'status': 'error', 'message': 'No user on ticket'}, status=400)

        chat, _ = SupportChat.objects.get_or_create(user=ticket.user)
        ChatMessage.objects.create(
            chat=chat,
            sender_type='admin',
            text=text,
            image_field=attachment,
        )
        if text:
            learn_from_admin_reply(chat, text)
        notify_customer_of_admin_reply(chat, text or 'A file was attached to the conversation.')
        if chat.human_escalated:
            chat.human_escalated = False
            chat.save(update_fields=['human_escalated', 'updated_at'])
        return _JsonResponse({'status': 'ok'})


# ─────────────────────────────────────────────────────────
# BOT KNOWLEDGE BASE ADMIN
# ─────────────────────────────────────────────────────────

@admin.register(BotKnowledge)
class BotKnowledgeAdmin(admin.ModelAdmin):
    list_display = ('title', 'category', 'keywords', 'answer_preview', 'times_used', 'is_active', 'created_at')
    list_filter = ('category', 'is_active')
    search_fields = ('title', 'keywords', 'answer', 'category')
    readonly_fields = ('times_used', 'created_at')
    list_editable = ('is_active',)

    fieldsets = (
        ('Knowledge Entry', {
            'fields': ('title', 'category', 'is_active'),
        }),
        ('Trigger Keywords', {
            'fields': ('keywords',),
            'description': (
                'Enter comma-separated keywords that should trigger this answer. '
                'Example: "delivery, shipping, how long, when will" — if any of these appear '
                'in a customer message, the bot will reply with the answer below.'
            ),
        }),
        ('Bot Answer', {
            'fields': ('answer',),
            'description': 'What the bot will say when a matching keyword is detected. Write in the bot\'s voice.',
        }),
        ('Status', {
            'fields': ('times_used', 'created_at'),
        }),
    )

    def answer_preview(self, obj):
        return obj.answer[:80] + '...' if len(obj.answer) > 80 else obj.answer
    answer_preview.short_description = 'Answer Preview'


@admin.register(UnknownQuestion)
class UnknownQuestionAdmin(admin.ModelAdmin):
    list_display = ('message_preview', 'session_step', 'status', 'asked_at', 'teach_button')
    list_filter = ('status', 'session_step')
    search_fields = ('message',)
    readonly_fields = ('message', 'session_step', 'asked_at', 'user')

    fieldsets = (
        ('Customer Message', {
            'fields': ('user', 'message', 'session_step', 'asked_at', 'status'),
            'description': (
                'This message was sent by a customer but the bot had no answer for it. '
                'Click "🧠 Teach Bot" to convert it into a trained response.'
            ),
        }),
    )

    def message_preview(self, obj):
        return obj.message[:80] + '...' if len(obj.message) > 80 else obj.message
    message_preview.short_description = 'Customer Message'

    def teach_button(self, obj):
        url = reverse('admin:shop_unknownquestion_teach', args=[obj.id])
        return format_html(
            '<form action="{}" method="POST" style="display:inline;">'
            '<input type="hidden" name="csrfmiddlewaretoken" value="{}">'
            '<button type="submit" class="button" style="'
            'background:#8e44ad;color:#fff;padding:4px 10px;'
            'border-radius:4px;border:none;cursor:pointer;font-size:12px;">'
            '🧠 Teach Bot</button></form>',
            url,
            self.get_csrf_token(obj) if hasattr(self, 'get_csrf_token') else ''
        )
    teach_button.short_description = 'Action'

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                '<int:question_id>/teach/',
                self.admin_site.admin_view(self.teach_bot_view),
                name='shop_unknownquestion_teach',
            ),
        ]
        return custom_urls + urls

    def teach_bot_view(self, request, question_id):
        from django.http import HttpResponseRedirect

        try:
            question = UnknownQuestion.objects.get(id=question_id)
        except UnknownQuestion.DoesNotExist:
            self.message_user(request, "Question not found.", level='error')
            return HttpResponseRedirect(reverse('admin:shop_unknownquestion_changelist'))

        if request.method == 'POST':
            title = request.POST.get('title', '').strip()
            category = request.POST.get('category', 'learned').strip() or 'learned'
            keywords = request.POST.get('keywords', '').strip()
            answer = request.POST.get('answer', '').strip()
            if keywords and answer:
                knowledge = BotKnowledge.objects.create(
                    title=title or question.message[:200],
                    category=category,
                    keywords=keywords,
                    answer=answer,
                )
                question.status = 'answered'
                question.converted_to = knowledge
                question.save(update_fields=['status', 'converted_to'])
                self.message_user(
                    request,
                    "✅ Bot taught successfully! It will now answer similar questions automatically.",
                    level='success'
                )
                return HttpResponseRedirect(reverse('admin:shop_unknownquestion_changelist'))
            else:
                self.message_user(request, "Both keywords and answer are required.", level='error')

        suggested_keywords = ', '.join(
            w for w in question.message.lower().split()
            if len(w) > 3 and w not in {'what', 'when', 'where', 'does', 'will', 'your', 'have', 'this', 'that'}
        )[:200]

        context = {
            **self.admin_site.each_context(request),
            'question': question,
            'suggested_title': question.message[:200],
            'suggested_keywords': suggested_keywords,
            'title': 'Teach the Bot',
            'opts': self.model._meta,
        }
        return render(request, 'admin/shop/teach_bot.html', context)