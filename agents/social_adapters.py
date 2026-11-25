"""Replaceable social publishing adapters (manual package + future Meta & YouTube APIs)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
from typing import Any, Optional
import zipfile

from PIL import Image

import creative
import engine
from agents.credentials import SecureCredentialStore

SUPPORTED_PLATFORMS = ('instagram_reels', 'facebook_reels', 'youtube_shorts')


@dataclass
class PublishOutcome:
    """Result returned by a social publishing adapter."""

    confirmed: bool
    external_post_id: str = ''
    published_url: str = ''
    package_path: str = ''
    message: str = ''


def extract_thumbnail(video_path: Path, thumb_path: Path, product_photo: Optional[Path] = None) -> Path:
    """Extract a JPEG cover thumbnail from the approved MP4 (or product photo fallback)."""
    thumb_path.parent.mkdir(parents=True, exist_ok=True)
    if video_path.is_file():
        try:
            subprocess.run(
                [
                    creative.ffmpeg(), '-hide_banner', '-loglevel', 'error', '-y',
                    '-ss', '0.5', '-i', str(video_path),
                    '-frames:v', '1', '-q:v', '2', str(thumb_path),
                ],
                check=True,
                timeout=30,
            )
            if thumb_path.is_file() and thumb_path.stat().st_size > 1000:
                return thumb_path
        except Exception:
            pass
    if product_photo and product_photo.is_file():
        with Image.open(product_photo) as img:
            img.convert('RGB').save(thumb_path, 'JPEG', quality=90)
        return thumb_path
    raise FileNotFoundError(f'Could not create thumbnail for {video_path}')


def build_manual_publishing_package(
    *,
    post: dict[str, Any],
    video_path: Path,
    product_photo: Optional[Path] = None,
    package_dir: Optional[Path] = None,
) -> Path:
    """Create a downloadable ZIP manual publishing package with MP4, thumbnail and copy files."""
    if not video_path.is_file():
        raise FileNotFoundError(f'Approved video not found: {video_path}')

    out_dir = package_dir or (engine.DATA / 'social_packages')
    out_dir.mkdir(parents=True, exist_ok=True)
    post_id = post['id']
    thumb_path = out_dir / f'{post_id}_thumb.jpg'
    zip_path = out_dir / f'{post_id}.zip'

    extract_thumbnail(video_path, thumb_path, product_photo=product_photo)

    hashtags_text = ' '.join(post.get('hashtags') or [])
    caption = (post.get('caption') or '').strip()
    title = (post.get('title') or '').strip()
    description = (post.get('description') or '').strip()
    product_link = (post.get('product_link') or '').strip()

    manifest = {
        'post_id': post_id,
        'platform': post['platform'],
        'sku': post['sku'],
        'production_id': post['production_id'],
        'status': post['status'],
        'title': title,
        'caption': caption,
        'description': description,
        'hashtags': post.get('hashtags') or [],
        'product_link': product_link,
        'video_file': 'video.mp4',
        'thumbnail_file': 'thumbnail.jpg',
        'manual_publishing_only': True,
    }

    with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(video_path, 'video.mp4')
        zf.write(thumb_path, 'thumbnail.jpg')
        zf.writestr('caption.txt', caption + ('\n\n' + hashtags_text if hashtags_text and hashtags_text not in caption else '') + '\n')
        zf.writestr('title.txt', title + '\n')
        zf.writestr('description.txt', description + ('\n\n' + product_link if product_link and product_link not in description else '') + '\n')
        zf.writestr('hashtags.txt', hashtags_text + '\n')
        zf.writestr('product_link.txt', product_link + '\n')
        zf.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))

    return zip_path


class SocialPublisherAdapter(ABC):
    """Replaceable social publishing adapter interface."""

    adapter_id: str
    platform: str

    @abstractmethod
    def is_configured(self, cred_store: SecureCredentialStore) -> bool:
        """Return True if this adapter has required configuration/credentials."""

    @abstractmethod
    def publish(
        self,
        *,
        post: dict[str, Any],
        video_path: Path,
        product_photo: Optional[Path],
        cred_store: SecureCredentialStore,
    ) -> PublishOutcome:
        """Prepare a manual package or execute confirmed official API publication."""


class ManualPackageAdapter(SocialPublisherAdapter):
    """Default Phase adapter creating verified manual publishing packages without auto-posting."""

    adapter_id = 'manual_package'

    def __init__(self, platform: str):
        self.platform = platform

    def is_configured(self, cred_store: SecureCredentialStore) -> bool:
        return True

    def publish(
        self,
        *,
        post: dict[str, Any],
        video_path: Path,
        product_photo: Optional[Path],
        cred_store: SecureCredentialStore,
    ) -> PublishOutcome:
        zip_path = build_manual_publishing_package(
            post=post,
            video_path=video_path,
            product_photo=product_photo,
        )
        return PublishOutcome(
            confirmed=False,
            package_path=str(zip_path),
            message='Manual publishing package ready. Post manually and confirm with the published URL.',
        )


class MetaReelsApiAdapter(SocialPublisherAdapter):
    """Replaceable adapter for future official Meta Graph API (Instagram Reels & Facebook Reels)."""

    adapter_id = 'meta_api'

    def __init__(self, platform: str, transport=None):
        if platform not in ('instagram_reels', 'facebook_reels'):
            raise ValueError('MetaReelsApiAdapter supports instagram_reels or facebook_reels.')
        self.platform = platform
        self.transport = transport

    def is_configured(self, cred_store: SecureCredentialStore) -> bool:
        if self.platform == 'instagram_reels':
            return bool(cred_store.get_secret('meta_ig_token') and cred_store.get_secret('meta_ig_id') and cred_store.get_secret('meta_graph_version'))
        return bool(cred_store.get_secret('meta_fb_page_token') and cred_store.get_secret('meta_fb_page_id') and cred_store.get_secret('meta_graph_version'))

    def publish(
        self,
        *,
        post: dict[str, Any],
        video_path: Path,
        product_photo: Optional[Path],
        cred_store: SecureCredentialStore,
    ) -> PublishOutcome:
        if not self.is_configured(cred_store):
            raise ValueError(f'Official Meta API credentials for {self.platform} are not configured. Use manual publishing package.')
        if self.transport is None:
            raise ValueError('Meta API live upload transport is disabled in this environment; use manual publishing package.')
        result = self.transport(platform=self.platform, post=post, video_path=video_path, cred_store=cred_store)
        post_id = str(result.get('external_post_id') or '').strip()
        post_url = str(result.get('published_url') or '').strip()
        if not result.get('confirmed') or not post_id or not post_url:
            raise ValueError('Meta API did not return a confirmed post ID and URL.')
        return PublishOutcome(
            confirmed=True,
            external_post_id=post_id,
            published_url=post_url,
            message=f'Confirmed {self.platform} publish.',
        )


class YouTubeShortsApiAdapter(SocialPublisherAdapter):
    """Replaceable adapter for future official YouTube Data API v3 Shorts upload."""

    adapter_id = 'youtube_api'
    platform = 'youtube_shorts'

    def __init__(self, transport=None):
        self.transport = transport

    def is_configured(self, cred_store: SecureCredentialStore) -> bool:
        return bool(cred_store.get_secret('youtube_oauth_token') and cred_store.get_secret('youtube_channel_id'))

    def publish(
        self,
        *,
        post: dict[str, Any],
        video_path: Path,
        product_photo: Optional[Path],
        cred_store: SecureCredentialStore,
    ) -> PublishOutcome:
        if not self.is_configured(cred_store):
            raise ValueError('Official YouTube API credentials are not configured. Use manual publishing package.')
        if self.transport is None:
            raise ValueError('YouTube API live upload transport is disabled in this environment; use manual publishing package.')
        result = self.transport(platform=self.platform, post=post, video_path=video_path, cred_store=cred_store)
        post_id = str(result.get('external_post_id') or '').strip()
        post_url = str(result.get('published_url') or '').strip()
        if not result.get('confirmed') or not post_id or not post_url:
            raise ValueError('YouTube API did not return a confirmed video ID and Shorts URL.')
        return PublishOutcome(
            confirmed=True,
            external_post_id=post_id,
            published_url=post_url,
            message='Confirmed YouTube Shorts publish.',
        )


def build_social_adapter(platform: str, *, mode: str = 'manual_package', transport=None) -> SocialPublisherAdapter:
    """Return the configured publishing adapter for the requested platform."""
    if platform not in SUPPORTED_PLATFORMS:
        raise ValueError(f'Unsupported social platform: {platform}')
    if mode == 'meta_api' and platform in ('instagram_reels', 'facebook_reels'):
        return MetaReelsApiAdapter(platform, transport=transport)
    if mode == 'youtube_api' and platform == 'youtube_shorts':
        return YouTubeShortsApiAdapter(transport=transport)
    return ManualPackageAdapter(platform)
