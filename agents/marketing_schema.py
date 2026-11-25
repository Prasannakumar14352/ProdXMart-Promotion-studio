"""Structured JSON schema, prompt-injection sanitization, grounding checks and fallback templates for MarketingAgent."""

from __future__ import annotations

from decimal import Decimal
import json
import re
from typing import Any, Optional

SUPPORTED_CAMPAIGN_TYPES = ('single_product', 'multi_product', 'category', 'seasonal')
SUPPORTED_LANGUAGES = ('en', 'te', 'bilingual')

_FORBIDDEN_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ('invented discount or offer', re.compile(
        r'\b(?:\d+\s*%\s*off|flat\s+\d+\s*%|discount(?:ed)?|promo\s*code|coupon\s*code|cashback|buy\s*1\s*get\s*1|bogo|limited[- ]time\s+(?:offer|deal)|flash\s+sale|clearance\s+sale)\b',
        re.I,
    )),
    ('invented Telugu discount claim', re.compile(r'(?:తగ్గింపు|డిస్కౌంట్|ఆఫర్)')),
    ('invented customer review or rating', re.compile(
        r'\b(?:\d+(?:\.\d+)?\s*(?:stars?|/5|out\s+of\s+5)|rated\s+\d|customer\s+reviews?|verified\s+buyers?|thousands\s+of\s+(?:happy\s+)?customers?|#1\s*rated|best[- ]seller)\b',
        re.I,
    )),
    ('invented stock urgency', re.compile(
        r'\b(?:only\s+\d+\s+left|few\s+left|almost\s+sold\s+out|selling\s+fast|limited\s+stock|last\s+chance\s+before\s+stock)\b',
        re.I,
    )),
]

_PRICE_MENTION_RE = re.compile(r'(?:₹|Rs\.?|INR)\s*([0-9][0-9,]*(?:\.\d{1,2})?)', re.I)
_TELUGU_CHAR_RE = re.compile(r'[\u0c00-\u0c7f]')
_CONTROL_CHARS_RE = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')
_INJECTION_RE = re.compile(r'(?:ignore\s+(?:all\s+)?previous\s+instructions|system\s+prompt|\[/?product_data\]|<\|)', re.I)


def sanitize_external_text(value: Any, max_len: int = 300) -> str:
    """Strip control characters and collapse whitespace so external text stays inert data."""
    if value is None:
        return ''
    cleaned = _CONTROL_CHARS_RE.sub(' ', str(value))
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    # Neutralize accidental XML delimiter injection inside <PRODUCT_DATA>.
    cleaned = cleaned.replace('<PRODUCT_DATA>', '[PRODUCT_DATA]').replace('</PRODUCT_DATA>', '[/PRODUCT_DATA]')
    return cleaned[:max_len]


def safe_product_fact_payload(product: dict[str, Any]) -> dict[str, Any]:
    """Extract only verified, factual attributes from a catalogue item."""
    price_dec = Decimal(str(product['price'])).quantize(Decimal('0.01'))
    price_display = f"{price_dec:,.2f}".removesuffix('.00')
    feature = sanitize_external_text(product.get('feature_te') or '', max_len=300)
    return {
        'sku': sanitize_external_text(product['sku'], max_len=120),
        'name': sanitize_external_text(product['name'], max_len=180),
        'category': sanitize_external_text(product.get('category') or 'General', max_len=120),
        'price_inr': price_display,
        'price_numeric': float(price_dec),
        'feature_te': feature,
        'product_url': sanitize_external_text(product['product_url'], max_len=2500),
        'photo_count': len(product.get('files') or []),
    }


def extract_json_object(raw_text: str) -> dict[str, Any]:
    """Parse JSON from raw LLM output, stripping markdown fences if present."""
    text = (raw_text or '').strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.I)
        text = re.sub(r'\s*```$', '', text)
    try:
        parsed = json.loads(text)
    except ValueError:
        start = text.find('{')
        end = text.rfind('}')
        if start == -1 or end <= start:
            raise ValueError('Response did not contain a valid JSON object.')
        parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError('Top-level marketing output must be a JSON object.')
    return parsed


def _nonempty_str(val: Any, field_name: str) -> str:
    if not isinstance(val, str) or not val.strip():
        raise ValueError(f'Field "{field_name}" must be a non-empty string.')
    return val.strip()


def _nonempty_str_list(val: Any, field_name: str) -> list[str]:
    if not isinstance(val, list) or not val:
        raise ValueError(f'Field "{field_name}" must be a non-empty list of strings.')
    out = [str(item).strip() for item in val if isinstance(item, str) and item.strip()]
    if not out:
        raise ValueError(f'Field "{field_name}" must contain at least one non-empty string.')
    return out


def _collect_strings(obj: Any) -> list[str]:
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, list):
        items: list[str] = []
        for x in obj:
            items.extend(_collect_strings(x))
        return items
    if isinstance(obj, dict):
        items = []
        for v in obj.values():
            items.extend(_collect_strings(v))
        return items
    return []


def _check_grounding(item: dict[str, Any], fact: dict[str, Any], language: str) -> None:
    """Enforce no fabricated discounts, reviews, stock claims, or altered prices/URLs."""
    texts = _collect_strings(item)
    combined = '\n'.join(texts)

    for label, pattern in _FORBIDDEN_PATTERNS:
        match = pattern.search(combined)
        if match:
            raise ValueError(f'Grounding violation ({label}): "{match.group(0)}" is not permitted in marketing copy.')

    expected_price = Decimal(str(fact['price_numeric'])).quantize(Decimal('0.01'))
    for match in _PRICE_MENTION_RE.finditer(combined):
        raw_num = match.group(1).replace(',', '')
        try:
            mentioned = Decimal(raw_num).quantize(Decimal('0.01'))
        except Exception:
            continue
        if mentioned != expected_price:
            raise ValueError(
                f'Grounding violation (invented price): mentioned ₹{raw_num} does not match actual price ₹{fact["price_inr"]}.'
            )

    if fact['product_url'] not in item.get('call_to_action', ''):
        raise ValueError('call_to_action must include the exact actual product_url.')

    if language == 'te':
        telugu_fields = ' '.join([
            item.get('opening_hook', ''),
            item.get('voiceover_script', ''),
            item.get('instagram_caption', ''),
            ' '.join(item.get('on_screen_captions', [])),
        ])
        if not _TELUGU_CHAR_RE.search(telugu_fields):
            raise ValueError('Telugu language requested, but generated output did not contain Telugu script.')


def validate_product_marketing_item(
    raw_item: Any,
    fact: dict[str, Any],
    *,
    language: str,
) -> dict[str, Any]:
    """Validate and normalize one product marketing object against the strict schema."""
    if not isinstance(raw_item, dict):
        raise ValueError('Product marketing entry must be a JSON object.')

    target_audience = _nonempty_str(raw_item.get('target_audience'), 'target_audience')
    marketing_objective = _nonempty_str(raw_item.get('marketing_objective'), 'marketing_objective')
    product_benefits = _nonempty_str_list(raw_item.get('product_benefits'), 'product_benefits')
    opening_hook = _nonempty_str(raw_item.get('opening_hook'), 'opening_hook')
    video_script = _nonempty_str(raw_item.get('video_script'), 'video_script')

    raw_sb = raw_item.get('storyboard')
    if not isinstance(raw_sb, list) or not raw_sb:
        raise ValueError('Field "storyboard" must be a non-empty list of scenes.')
    storyboard: list[dict[str, Any]] = []
    for idx, scene in enumerate(raw_sb, 1):
        if not isinstance(scene, dict):
            raise ValueError(f'Storyboard scene {idx} must be an object.')
        try:
            duration = int(scene.get('duration_seconds', 4))
        except (TypeError, ValueError):
            raise ValueError(f'Storyboard scene {idx} duration_seconds must be an integer.') from None
        if not 1 <= duration <= 60:
            raise ValueError(f'Storyboard scene {idx} duration_seconds must be between 1 and 60.')
        storyboard.append({
            'scene_number': int(scene.get('scene_number') or idx),
            'duration_seconds': duration,
            'visual': _nonempty_str(scene.get('visual'), f'storyboard[{idx}].visual'),
            'on_screen_text': _nonempty_str(scene.get('on_screen_text'), f'storyboard[{idx}].on_screen_text'),
            'voiceover': _nonempty_str(scene.get('voiceover'), f'storyboard[{idx}].voiceover'),
        })

    voiceover_script = _nonempty_str(raw_item.get('voiceover_script'), 'voiceover_script')
    on_screen_captions = _nonempty_str_list(raw_item.get('on_screen_captions'), 'on_screen_captions')
    instagram_caption = _nonempty_str(raw_item.get('instagram_caption'), 'instagram_caption')
    facebook_caption = _nonempty_str(raw_item.get('facebook_caption'), 'facebook_caption')

    yt = raw_item.get('youtube_shorts')
    if not isinstance(yt, dict):
        raise ValueError('Field "youtube_shorts" must be an object with title and description.')
    youtube_shorts = {
        'title': _nonempty_str(yt.get('title'), 'youtube_shorts.title')[:100],
        'description': _nonempty_str(yt.get('description'), 'youtube_shorts.description'),
    }

    raw_tags = _nonempty_str_list(raw_item.get('hashtags'), 'hashtags')
    hashtags: list[str] = []
    for tag in raw_tags:
        clean = re.sub(r'[^A-Za-z0-9_\u0c00-\u0c7f]', '', tag.lstrip('#'))
        if clean:
            formatted = '#' + clean
            if formatted not in hashtags:
                hashtags.append(formatted)
    if not hashtags:
        raise ValueError('Field "hashtags" must include at least one valid hashtag.')

    call_to_action = _nonempty_str(raw_item.get('call_to_action'), 'call_to_action')

    normalized = {
        'sku': fact['sku'],
        'product_name': fact['name'],
        'category': fact['category'],
        'price_inr': fact['price_inr'],
        'product_url': fact['product_url'],
        'target_audience': target_audience,
        'marketing_objective': marketing_objective,
        'product_benefits': product_benefits,
        'opening_hook': opening_hook,
        'video_script': video_script,
        'storyboard': storyboard,
        'voiceover_script': voiceover_script,
        'on_screen_captions': on_screen_captions,
        'instagram_caption': instagram_caption,
        'facebook_caption': facebook_caption,
        'youtube_shorts': youtube_shorts,
        'hashtags': hashtags,
        'call_to_action': call_to_action,
    }
    _check_grounding(normalized, fact, language)
    return normalized


def build_fallback_product_marketing(
    fact: dict[str, Any],
    *,
    campaign_type: str,
    language: str,
    season_theme: str = '',
) -> dict[str, Any]:
    """Deterministic, strictly grounded fallback marketing template in English, Telugu or bilingual."""
    name = fact['name']
    category = fact['category']
    price = fact['price_inr']
    url = fact['product_url']
    raw_feature = fact['feature_te'] or ''
    if _INJECTION_RE.search(raw_feature) or any(p.search(raw_feature) for _, p in _FORBIDDEN_PATTERNS):
        feature = ''
    else:
        feature = raw_feature
    cat_tag = re.sub(r'[^A-Za-z0-9]', '', category) or 'Shopping'
    season_prefix = f'{season_theme} · ' if season_theme else ''

    if language == 'te':
        hook = f'{season_prefix}{category} కోసం సరైన ఎంపిక ఇదేనా?'
        audience = f'{category} విభాగంలో నాణ్యమైన ఉత్పత్తులను కోరుకునే ఆన్‌లైన్ కొనుగోలుదారులు'
        objective = f'{name} యొక్క వివరాలు మరియు ధర ₹{price} ను స్పష్టంగా చూపించి ProdXMart ఉత్పత్తి పేజీకి ట్రాఫిక్ పెంచడం.'
        benefits = [
            f'{category} విభాగంలో రోజువారీ అవసరాలకు అనువైన {name}.',
            f'ProdXMart లో అందుబాటులో ఉన్న ధర: ₹{price}.',
        ]
        if feature:
            benefits.append(feature)
        storyboard = [
            {
                'scene_number': 1,
                'duration_seconds': 4,
                'visual': f'{name} ప్రధాన ఫోటోను శుభ్రమైన నేపథ్యంలో జూమ్-ఇన్ షాట్‌తో చూపించండి.',
                'on_screen_text': hook,
                'voiceover': f'{hook} {name} చూడండి.',
            },
            {
                'scene_number': 2,
                'duration_seconds': 4,
                'visual': f'{name} యొక్క వివరాలు మరియు దగ్గరి ఫోటోలను చూపించండి.',
                'on_screen_text': feature or name,
                'voiceover': feature or f'{category} విభాగంలో ఇది ఒక చక్కని ఎంపిక.',
            },
            {
                'scene_number': 3,
                'duration_seconds': 3,
                'visual': f'ధర కార్డుతో పాటు {name} ఫోటోను ప్రదర్శించండి.',
                'on_screen_text': f'ధర: ₹{price}',
                'voiceover': f'ProdXMart లో ధర కేవలం ₹{price}.',
            },
            {
                'scene_number': 4,
                'duration_seconds': 4,
                'visual': 'ProdXMart ఉత్పత్తి లింక్ మరియు బ్రాండ్ కార్డును చూపించండి.',
                'on_screen_text': 'prodxmart.com చూడండి',
                'voiceover': f'పూర్తి వివరాల కోసం ఇప్పుడే {url} సందర్శించండి.',
            },
        ]
        captions = [s['on_screen_text'] for s in storyboard]
        voiceover_script = ' '.join(s['voiceover'] for s in storyboard)
        video_script = '\n'.join(f"Scene {s['scene_number']} ({s['duration_seconds']}s): {s['visual']} | VO: {s['voiceover']}" for s in storyboard)
        cta = f'పూర్తి వివరాల కోసం సందర్శించండి: {url}'
        hashtags = ['#ProdXMart', f'#{cat_tag}', '#TeluguReels', '#ProductShowcase']
        ig_caption = f'{hook}\n{name} · ₹{price}\n{feature}\n{cta}\n' + ' '.join(hashtags)
        fb_caption = f'{season_prefix}{name} ({category}) — ధర ₹{price}.\n{feature}\n{cta}'
        yt_title = f'{name} · ₹{price} | ProdXMart Shorts'[:100]
        yt_desc = f'{name} ({category}) వివరాలు మరియు ధర ₹{price}.\n{cta}\n' + ' '.join(hashtags)
    else:
        hook = f'{season_prefix}Looking for a dependable {category.lower()} pick?'
        audience = f'Shoppers browsing {category} products online on ProdXMart'
        objective = f'Drive qualified product page visits to {name} ({campaign_type.replace("_", " ")}) with transparent ₹{price} pricing.'
        benefits = [
            f'Categorized under {category} on ProdXMart for straightforward product discovery.',
            f'Listed transparently at ₹{price} with direct storefront link access.',
        ]
        if feature:
            benefits.append(f'Verified product highlight: {feature}')
        storyboard = [
            {
                'scene_number': 1,
                'duration_seconds': 4,
                'visual': f'Hero push-in frame showcasing {name} cover photo.',
                'on_screen_text': hook,
                'voiceover': f'{hook} Meet {name}.',
            },
            {
                'scene_number': 2,
                'duration_seconds': 4,
                'visual': f'Closer product angle highlighting {name} details.',
                'on_screen_text': feature or name,
                'voiceover': f'Here is a closer look at {name} from our {category} catalogue.',
            },
            {
                'scene_number': 3,
                'duration_seconds': 3,
                'visual': f'Clean price card beside {name} gallery image.',
                'on_screen_text': f'₹{price}',
                'voiceover': f'Available on ProdXMart at ₹{price}.',
            },
            {
                'scene_number': 4,
                'duration_seconds': 4,
                'visual': 'Closing ProdXMart CTA card with product link.',
                'on_screen_text': 'Explore on prodxmart.com',
                'voiceover': f'View full details and specifications at {url}.',
            },
        ]
        if language == 'bilingual':
            storyboard[0]['on_screen_text'] = f'{hook} · మీ ఎంపిక ఇదేనా?'
            storyboard[3]['on_screen_text'] = 'prodxmart.com చూడండి'
        captions = [s['on_screen_text'] for s in storyboard]
        voiceover_script = ' '.join(s['voiceover'] for s in storyboard)
        video_script = '\n'.join(f"Scene {s['scene_number']} ({s['duration_seconds']}s): {s['visual']} | VO: {s['voiceover']}" for s in storyboard)
        cta = f'Explore full product details at {url}'
        hashtags = ['#ProdXMart', f'#{cat_tag}', '#ProductReel', '#OnlineShoppingIndia']
        ig_caption = f'{hook}\n{name} · ₹{price}\n{cta}\n' + ' '.join(hashtags)
        fb_caption = f'{season_prefix}{name} in {category} — ₹{price}.\n{cta}'
        yt_title = f'{name} | ₹{price} · ProdXMart'[:100]
        yt_desc = f'{name} ({category}) — ₹{price}.\n{cta}\n' + ' '.join(hashtags)

    return {
        'sku': fact['sku'],
        'product_name': name,
        'category': category,
        'price_inr': price,
        'product_url': url,
        'target_audience': audience,
        'marketing_objective': objective,
        'product_benefits': benefits,
        'opening_hook': hook,
        'video_script': video_script,
        'storyboard': storyboard,
        'voiceover_script': voiceover_script,
        'on_screen_captions': captions,
        'instagram_caption': ig_caption,
        'facebook_caption': fb_caption,
        'youtube_shorts': {'title': yt_title, 'description': yt_desc},
        'hashtags': hashtags,
        'call_to_action': cta,
    }


def build_video_handoff_payload(
    *,
    campaign_id: str,
    mode: str,
    campaign_type: str,
    language: str,
    season_theme: str,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Construct the validated storyboard payload for downstream Video Agent consumption."""
    return {
        'handoff_target': 'video_agent',
        'campaign_id': campaign_id,
        'mode': mode,
        'campaign_type': campaign_type,
        'language': language,
        'season_theme': season_theme,
        'items': [
            {
                'sku': item['sku'],
                'product_name': item['product_name'],
                'price_inr': item['price_inr'],
                'product_url': item['product_url'],
                'opening_hook': item['opening_hook'],
                'video_script': item['video_script'],
                'storyboard': item['storyboard'],
                'voiceover_script': item['voiceover_script'],
                'on_screen_captions': item['on_screen_captions'],
                'call_to_action': item['call_to_action'],
            }
            for item in items
        ],
    }
