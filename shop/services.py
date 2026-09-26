import html
import logging
import re
from html import escape

import requests
from django.conf import settings
from django.db import transaction
from django.urls import reverse

logger = logging.getLogger(__name__)

SERPER_SEARCH_URL = 'https://google.serper.dev/search'
_LOCAL_CHAT_MESSAGES = {
    'hi', 'hi there', 'hello', 'hello there', 'hey', 'hey there', 'yo', 'sup',
    'whats up', 'what s up', 'what is up', 'good morning', 'good afternoon', 'good evening',
    'thanks', 'thank you', 'thank you so much', 'cheers', 'cool', 'nice',
    'awesome', 'great', 'bye', 'goodbye',
}
_SEARCH_STOP_WORDS = {
    'a', 'an', 'and', 'are', 'can', 'could', 'do', 'does', 'for', 'how',
    'i', 'in', 'is', 'it', 'me', 'my', 'of', 'on', 'or', 'please', 'the',
    'this', 'to', 'what', 'when', 'where', 'which', 'who', 'why', 'with',
    'would', 'you', 'your',
}


def _normalize_knowledge_text(text):
    return re.sub(r'\s+', ' ', re.sub(r'[^\w\s]', ' ', (text or '').lower())).strip()


def is_greeting_or_small_talk(message):
    return _normalize_knowledge_text(message) in _LOCAL_CHAT_MESSAGES


def is_local_chat_turn(message, current_step=None):
    """Identify greetings, small talk, and menu letters that should stay in chat."""
    normalized = _normalize_knowledge_text(message)
    if is_greeting_or_small_talk(message):
        return True
    return normalized in {'a', 'b', 'c', 'd', 'e'} and (
        current_step is None or current_step == 'awaiting_intent'
    )


def search_web_answer(question):
    """Return a relevant Serper result, or None when search cannot answer reliably."""
    api_key = str(getattr(settings, 'SERPER_API_KEY', '') or '').strip()
    if not api_key:
        logger.info('Web search skipped: SERPER_API_KEY is not configured.')
        return None

    query_terms = {
        word for word in _normalize_knowledge_text(question).split()
        if len(word) > 2 and word not in _SEARCH_STOP_WORDS
    }
    if not query_terms:
        return None

    try:
        response = requests.post(
            SERPER_SEARCH_URL,
            headers={'X-API-KEY': api_key, 'Content-Type': 'application/json'},
            json={'q': question[:500]},
            timeout=(3, 6),
        )
        response.raise_for_status()
        data = response.json()
    except requests.RequestException:
        logger.exception('Serper web search request failed.')
        return None
    except (TypeError, ValueError):
        logger.exception('Serper returned an invalid web search response.')
        return None

    if not isinstance(data, dict):
        logger.warning('Serper returned a web search response with an unexpected shape.')
        return None

    answer_box = data.get('answerBox') or {}
    candidates = []
    if isinstance(answer_box, dict) and (answer_box.get('answer') or answer_box.get('snippet')):
        candidates.append((
            answer_box.get('answer') or answer_box.get('snippet'),
            answer_box.get('link') or answer_box.get('sourceLink') or '',
            answer_box.get('title') or '',
        ))

    knowledge_graph = data.get('knowledgeGraph') or {}
    if isinstance(knowledge_graph, dict) and knowledge_graph.get('description'):
        candidates.append((
            knowledge_graph['description'],
            knowledge_graph.get('website') or '',
            knowledge_graph.get('title') or '',
        ))

    organic_results = data.get('organic')
    if not isinstance(organic_results, list):
        organic_results = []
    for result in organic_results[:3]:
        if not isinstance(result, dict):
            continue
        snippet = result.get('snippet')
        if snippet:
            candidates.append((
                snippet,
                result.get('link') or '',
                result.get('title') or '',
            ))

    for answer, link, title in candidates:
        plain_answer = re.sub(r'<[^>]+>', ' ', html.unescape(str(answer)))
        plain_answer = re.sub(r'\s+', ' ', plain_answer).strip()
        searchable_result = _normalize_knowledge_text(f'{title} {plain_answer}')
        if not plain_answer or not query_terms.intersection(searchable_result.split()):
            continue

        result_text = f'Here is what I found online: {plain_answer[:1500]}'
        source_url = str(link).strip()
        if source_url.startswith('https://') and not any(char.isspace() for char in source_url):
            result_text += f'\nSource: {source_url}'
        return result_text
    return None


PRINTFUL_API_URL = 'https://api.printful.com'


def get_printful_order_tracking(order_id):
    """Return normalized live fulfillment and shipment data for a Printful order."""
    token = (
        getattr(settings, 'PRINTFUL_API_KEY', '')
        or getattr(settings, 'PRINTFUL_ACCESS_TOKEN', '')
    ).strip()
    if not token:
        return {
            'status': None,
            'shipments': [],
            'error': 'Printful tracking is not configured yet.',
        }

    headers = {
        'Authorization': f'Bearer {token}',
        'Accept': 'application/json',
        'User-Agent': 'HoxobilStore/1.0',
    }
    store_id = getattr(settings, 'PRINTFUL_STORE_ID', '')
    if store_id:
        headers['X-PF-Store-Id'] = str(store_id)

    try:
        response = requests.get(
            f'{PRINTFUL_API_URL}/orders/{order_id}',
            headers=headers,
            timeout=(10, 20),
        )
        response.raise_for_status()
        result = response.json().get('result') or {}
        shipments = []
        for shipment in result.get('shipments') or []:
            shipments.append({
                'carrier': shipment.get('carrier') or '',
                'tracking_number': shipment.get('tracking_number') or '',
                'tracking_url': shipment.get('tracking_url') or '',
                'shipped_at': shipment.get('ship_date') or '',
                'status': shipment.get('shipment_status') or '',
            })
        return {
            'status': result.get('status') or '',
            'shipments': shipments,
            'error': None,
        }
    except requests.RequestException:
        logger.exception('Printful tracking request failed for order %s', order_id)
        return {
            'status': None,
            'shipments': [],
            'error': 'Live tracking is temporarily unavailable. Please try again later.',
        }
    except (TypeError, ValueError):
        logger.exception('Invalid Printful tracking response for order %s', order_id)
        return {
            'status': None,
            'shipments': [],
            'error': 'Printful returned an invalid tracking response.',
        }


def find_bot_knowledge_answer(question):
    from .models import BotKnowledge

    normalized = _normalize_knowledge_text(question)
    if not normalized:
        return None

    for entry in BotKnowledge.objects.filter(is_active=True).order_by('-times_used', 'id'):
        if any(
            normalized_keyword and normalized_keyword in normalized
            for normalized_keyword in (_normalize_knowledge_text(k) for k in entry.get_keywords_list())
        ):
            BotKnowledge.objects.filter(pk=entry.pk).update(times_used=entry.times_used + 1)
            return entry.answer
    return None


def record_bot_knowledge_gap(question, user=None, session_step=''):
    from .models import UnknownQuestion

    normalized = _normalize_knowledge_text(question)
    if not normalized or len(normalized) < 6 or is_local_chat_turn(question, session_step):
        return None
    unknown, _ = UnknownQuestion.objects.get_or_create(
        user=user if getattr(user, 'is_authenticated', False) else None,
        message=normalized[:500],
        status='pending',
        defaults={'session_step': session_step},
    )
    return unknown


def escalate_chat_to_human(chat, question):
    from .whatsapp import queue_whatsapp_notification

    if is_local_chat_turn(question):
        logger.info('Human escalation skipped for local chat message.')
        return False

    was_escalated = chat.human_escalated
    if not was_escalated:
        chat.human_escalated = True
        chat.save(update_fields=['human_escalated', 'updated_at'])
        queue_whatsapp_notification(
            chat.user.get_full_name().strip() or chat.user.get_username(),
            chat.user.email,
            f'Human support requested. Unanswered customer question: {question}',
            chat_id=chat.pk,
        )
    return was_escalated


def learn_from_admin_reply(chat, answer, question_message=None):
    from .models import BotKnowledge, UnknownQuestion

    if question_message is None:
        question_message = (
            chat.messages.filter(sender_type='user')
            .order_by('-created_at', '-id')
            .first()
        )
    if not question_message or not answer.strip():
        return None

    normalized_question = _normalize_knowledge_text(question_message.text)
    unknown = (
        UnknownQuestion.objects.filter(
            user=chat.user,
            status='pending',
            message=normalized_question[:500],
        )
        .order_by('-asked_at')
        .first()
    )
    if not unknown:
        return None

    knowledge = (
        BotKnowledge.objects.filter(keywords=unknown.message)
        .order_by('pk')
        .first()
    )
    if knowledge:
        knowledge.title = unknown.message[:200]
        knowledge.answer = answer.strip()
        knowledge.category = 'learned'
        knowledge.is_active = True
        knowledge.save(update_fields=['title', 'answer', 'category', 'is_active', 'updated_at'])
    else:
        knowledge = BotKnowledge.objects.create(
            title=unknown.message[:200],
            keywords=unknown.message,
            answer=answer.strip(),
            category='learned',
            is_active=True,
        )
    unknown.status = 'answered'
    unknown.converted_to = knowledge
    unknown.save(update_fields=['status', 'converted_to'])
    return knowledge


def notify_customer_of_admin_reply(chat, reply):
    if not chat.user.email:
        return

    from .utils import send_hoxobil_email

    base_url = getattr(settings, 'PUBLIC_BASE_URL', '').rstrip('/') or 'https://hoxobil.store'
    chat_url = f'{base_url}{reverse("shop:chat_support")}'
    excerpt = escape(reply.strip()[:500])
    body = (
        '<p>There is a new reply from Hoxobil Support.</p>'
        f'<blockquote>{excerpt}</blockquote>'
        f'<p><a href="{escape(chat_url, quote=True)}">Open your Hoxobil chat</a></p>'
    )
    transaction.on_commit(
        lambda: send_hoxobil_email(
            chat.user.email,
            'New reply from Hoxobil Support',
            body,
        )
    )
