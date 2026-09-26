from pathlib import Path

from django.contrib.auth.decorators import permission_required
from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_http_methods

from .models import ChatMessage, SupportChat
from .services import learn_from_admin_reply, notify_customer_of_admin_reply


IMAGE_EXTENSIONS = {'.avif', '.gif', '.jpeg', '.jpg', '.png', '.webp'}


def _serialize_message(message):
    attachment_url = message.image_field.url if message.image_field else ''
    attachment_name = Path(message.image_field.name).name if message.image_field else ''
    return {
        'id': message.pk,
        'sender_type': message.sender_type,
        'text': message.text,
        'created_at': message.created_at.strftime('%H:%M · %d %b'),
        'attachment_url': attachment_url,
        'attachment_name': attachment_name,
        'attachment_is_image': Path(attachment_name).suffix.lower() in IMAGE_EXTENSIONS,
    }


@staff_member_required
@permission_required('shop.view_supportchat', raise_exception=True)
@require_http_methods(['GET', 'POST'])
def chat_detail(request, chat_id):
    chat = get_object_or_404(
        SupportChat.objects.select_related('user'),
        pk=chat_id,
    )

    if request.method == 'GET' and request.GET.get('format') == 'json':
        try:
            after_id = int(request.GET.get('after_id', '0'))
        except ValueError:
            return JsonResponse({'error': 'Invalid message cursor.'}, status=400)
        messages_data = [
            _serialize_message(message)
            for message in chat.messages.filter(pk__gt=after_id).order_by('pk')
        ]
        latest_id = chat.messages.order_by('-pk').values_list('pk', flat=True).first() or 0
        return JsonResponse({'messages': messages_data, 'latest_message_id': latest_id})

    if request.method == 'POST':
        if not request.user.has_perm('shop.change_chatmessage'):
            return JsonResponse({'error': 'You do not have permission to reply.'}, status=403)
        text = request.POST.get('message', '').strip()
        attachment = request.FILES.get('attachment')
        if not text and not attachment:
            return JsonResponse({'error': 'Write a reply or attach a file.'}, status=400)

        message = ChatMessage.objects.create(
            chat=chat,
            sender_type='admin',
            text=text,
            image_field=attachment,
        )
        learn_from_admin_reply(chat, text)
        notify_customer_of_admin_reply(chat, text or 'A file was attached to the conversation.')
        if chat.human_escalated:
            chat.human_escalated = False
            chat.save(update_fields=['human_escalated', 'updated_at'])
        return JsonResponse({'message': _serialize_message(message)})

    messages_data = [
        _serialize_message(message)
        for message in chat.messages.select_related('chat').order_by('pk')
    ]
    return render(request, 'shop/admin/chat_detail.html', {
        'chat': chat,
        'chat_messages': messages_data,
        'latest_message_id': messages_data[-1]['id'] if messages_data else 0,
    })
