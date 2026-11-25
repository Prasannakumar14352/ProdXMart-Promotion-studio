"""Render a four-scene vertical product Reel using local catalogue assets."""
from functools import lru_cache
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from PIL import Image, ImageDraw, ImageFont, ImageOps

from soundtrack import write_music

ROOT = Path(__file__).resolve().parent
W, H = 1080, 1920
INK, FOREST, LIME, WHITE = '#102723', '#143E36', '#D6F979', '#FFFFFF'
PAPER = '#F4F4EB'
DEFAULT_FEATURE = 'మరిన్ని ఫొటోలు, వివరాలు స్టోర్‌లో చూడండి'

CATEGORY_LAYOUTS = {
    'apparel': {'label': 'LOOKBOOK', 'light_bg': '#F7F3EE', 'dark_bg': '#1B2D3A', 'accent': '#F2B880', 'light_accent': '#7C3E1D', 'ink': '#1E2022', 'card_radius': 52},
    'bags': {'label': 'CARRY & WORK', 'light_bg': '#F4F4EB', 'dark_bg': '#143E36', 'accent': '#D6F979', 'light_accent': '#143E36', 'ink': '#102723', 'card_radius': 44},
    'stationery': {'label': 'DESK STUDIO', 'light_bg': '#F6F7F2', 'dark_bg': '#1D332B', 'accent': '#D6F979', 'light_accent': '#145F53', 'ink': '#162824', 'card_radius': 34},
    'home': {'label': 'HOME & LIVING', 'light_bg': '#F5F2EB', 'dark_bg': '#24352E', 'accent': '#E5D38A', 'light_accent': '#2D5A43', 'ink': '#1A2420', 'card_radius': 48},
    'electronics': {'label': 'TECH PICK', 'light_bg': '#EEF3F7', 'dark_bg': '#122436', 'accent': '#7CE8FF', 'light_accent': '#12507B', 'ink': '#101D2A', 'card_radius': 36},
    'default': {'label': 'FEATURED PICK', 'light_bg': PAPER, 'dark_bg': FOREST, 'accent': LIME, 'light_accent': FOREST, 'ink': INK, 'card_radius': 44},
}


def layout_for_category(category: str, name: str = '') -> dict:
    """Resolve a product- and category-specific visual layout theme."""
    text = f'{category} {name}'.casefold()
    if any(k in text for k in ('shirt', 'tshirt', 't-shirt', 'apparel', 'cloth', 'kurta', 'saree', 'hoodie', 'dress', 'wear')):
        key = 'apparel'
    elif any(k in text for k in ('bag', 'backpack', 'tote', 'wallet', 'luggage', 'pouch', 'sling')):
        key = 'bags'
    elif any(k in text for k in ('stationery', 'notebook', 'book', 'pen', 'journal', 'paper', 'desk', 'office')):
        key = 'stationery'
    elif any(k in text for k in ('lamp', 'home', 'decor', 'kitchen', 'bottle', 'flask', 'living')):
        key = 'home'
    elif any(k in text for k in ('tech', 'electronic', 'gadget', 'audio', 'charger', 'cable', 'watch')):
        key = 'electronics'
    else:
        key = 'default'
    return {'layout_id': key, **CATEGORY_LAYOUTS[key]}


@lru_cache(maxsize=200)
def font(size, telugu=False, bold=False):
    family = 'NotoSansTelugu-Regular' if telugu else ('NotoSans-Bold' if bold else 'NotoSans-Regular')
    return ImageFont.truetype(str(ROOT / 'assets' / (family + '.ttf')), size)


def copy_block(draw, text, xy, width, height, size, color, *, telugu=False, bold=False):
    """Wrap to a bounded box and ellipsize unusually long catalogue copy."""
    x, y = xy
    for point in range(size, 31, -2):
        face = font(point, telugu, bold)
        lines = ['']
        for word in text.split():
            candidate = (lines[-1] + ' ' + word).strip()
            if lines[-1] and draw.textlength(candidate, font=face) > width:
                lines.append(word)
            else:
                lines[-1] = candidate
        step = int(point * 1.45)
        if len(lines) * step <= height and all(draw.textlength(s, font=face) <= width for s in lines):
            break
    lines = lines[:max(1, height // step)]
    for i, line in enumerate(lines):
        truncated = i == len(lines)-1 and len(' '.join(lines)) < len(' '.join(text.split()))
        while line and draw.textlength(line + ('…' if truncated else ''), font=face) > width:
            line = line[:-1].rstrip()
            truncated = True
        draw.text((x, y), line + ('…' if truncated else ''), font=face, fill=color, anchor='lt')
        y += step


def display_name(p):
    # Prefer a complete leading product name when the supplier repeats the noun
    # later in a long title (e.g. "Grey Khadi Fabric Laptop Bag ... Laptop Bag").
    words = p['name'].split()
    if len(words) > 8:
        for end in range(3, min(8, len(words)) + 1):
            phrase = ' '.join(words[end-2:end])
            if phrase.casefold() in ' '.join(words[end:]).casefold():
                return ' '.join(words[:end])
    return ' '.join(words)


def scene(p, index, demo):
    dark = index in (1, 3)
    bg, fg, accent = (FOREST, WHITE, LIME) if dark else (PAPER, INK, FOREST)
    frame = Image.new('RGB', (W, H), bg)
    d = ImageDraw.Draw(frame)
    d.text((76, 150), 'ProdXMart', font=font(43, bold=True), fill=fg, anchor='lt')
    tag = 'SAMPLE' if demo else p['category'].upper()
    copy_block(d, tag, (600, 154), 375, 49, 33, accent, bold=True)
    d.line((76, 225, 980, 225), fill=accent, width=3)

    title = display_name(p)
    bag = re.search(r'\blaptop\s+bags?\b', p['category'] + ' ' + p['name'], re.I)
    hooks = ['ఆఫీసుకి ఏ బ్యాగ్?' if bag else 'మీ ఎంపిక ఇదేనా?', 'దగ్గరగా చూడండి', 'ఎంత ధర?', 'ఎక్కడ దొరుకుతుంది?']
    headings = [title, 'A closer look', '₹' + f"{p['price']:,.2f}".removesuffix('.00'), 'Shop this bag' if bag else 'Shop this find']
    copy_block(d, hooks[index], (76, 286), 884, 100, 48, accent, telugu=True)
    copy_block(d, headings[index], (72, 392), 908, 225, 100 if index == 2 else 84, fg, bold=True)

    files = p['files']
    chosen = files[min(index, len(files)-1)] if index in (1, 2) else files[0]
    box = (64, 637, 1016, 1465)
    d.rounded_rectangle(box, radius=44, fill=WHITE)
    with Image.open(chosen) as source:
        photo = ImageOps.exif_transpose(source).convert('RGBA')
        flat = Image.new('RGB', photo.size, WHITE)
        flat.paste(photo, mask=photo.getchannel('A'))
        if index == 1 and len(files) == 1:
            # A single supplied image can still show a genuine product detail.
            w, h = flat.size
            flat = flat.crop((w//6, h//6, w-w//6, h-h//6))
        photo = ImageOps.contain(flat, (884, 786), Image.Resampling.LANCZOS)
    frame.paste(photo, ((W-photo.width)//2, 1051-photo.height//2))
    if index == 0:
        footer = '₹' + f"{p['price']:,.2f}".removesuffix('.00')
        copy_block(d, footer, (76, 1515), 850, 125, 78, fg, bold=True)
    elif index == 1:
        feature = p['feature_te']
        caption = feature if feature and feature != DEFAULT_FEATURE else title
        copy_block(d, caption, (76, 1515), 870, 175, 52, fg,
                   telugu=bool(re.search(r'[\u0c00-\u0c7f]', caption)))
    elif index == 2:
        copy_block(d, title, (76, 1515), 870, 165, 56, fg, bold=True)
    else:
        d.rounded_rectangle((76, 1510, 831, 1648), radius=68, fill=LIME)
        d.text((127, 1544), 'prodxmart.com', font=font(62, bold=True), fill=INK, anchor='lt')
        d.line((710, 1599, 749, 1560, 716, 1560), fill=INK, width=6)
        d.line((749, 1560, 749, 1592), fill=INK, width=6)
    return frame


def ffmpeg():
    if shutil.which('ffmpeg'):
        return shutil.which('ffmpeg')
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def ffprobe():
    found = shutil.which('ffprobe')
    if found:
        return found
    ff_dir = Path(ffmpeg()).resolve().parent
    for candidate in ('ffprobe.exe', 'ffprobe'):
        p = ff_dir / candidate
        if p.is_file():
            return str(p)
    raise FileNotFoundError('ffprobe executable was not found alongside ffmpeg or on PATH.')


def render_scene_card(
    p: dict,
    *,
    scene_index: int,
    total_scenes: int,
    demo: bool,
    hook_text: str = '',
    heading_text: str = '',
    caption_text: str = '',
    cta_text: str = '',
    image_index: int = 0,
    layout: dict | None = None,
):
    """Render a single 1080x1920 vertical scene frame with category layout, ProdXMart branding and CTA."""
    theme = layout or layout_for_category(p.get('category', ''), p.get('name', ''))
    dark = scene_index % 2 == 1
    bg = theme['dark_bg'] if dark else theme['light_bg']
    fg = WHITE if dark else theme['ink']
    accent = theme['accent'] if dark else theme['light_accent']

    frame = Image.new('RGB', (W, H), bg)
    d = ImageDraw.Draw(frame)

    # Top ProdXMart branding bar & category/layout badge.
    d.text((76, 130), 'ProdXMart', font=font(44, bold=True), fill=fg, anchor='lt')
    raw_tag = 'SAMPLE' if demo else (p.get('category') or theme['label']).upper()
    copy_block(d, raw_tag, (560, 134), 440, 52, 32, accent, bold=True)
    d.line((76, 210, 1004, 210), fill=accent, width=4)

    # Progress indicator pips at top right/bottom bar.
    pip_w = min(90, max(24, int(860 / max(1, total_scenes)) - 12))
    for i in range(total_scenes):
        px = 76 + i * (pip_w + 10)
        if px + pip_w > 1004:
            break
        d.rounded_rectangle((px, 228, px + pip_w, 238), radius=5, fill=accent if i <= scene_index else '#8EA59E')

    title = display_name(p)
    has_te = lambda s: bool(re.search(r'[\u0c00-\u0c7f]', s or ''))

    top_hook = (hook_text or '').strip() or theme['label']
    main_heading = (heading_text or '').strip() or title
    copy_block(d, top_hook, (76, 276), 928, 96, 44, accent, telugu=has_te(top_hook), bold=not has_te(top_hook))
    copy_block(d, main_heading, (72, 378), 936, 220, 78, fg, telugu=has_te(main_heading), bold=not has_te(main_heading))

    files = p.get('files') or []
    box = (64, 624, 1016, 1452)
    d.rounded_rectangle(box, radius=theme['card_radius'], fill=WHITE)
    if files:
        chosen = files[image_index % len(files)]
        with Image.open(chosen) as source:
            photo = ImageOps.exif_transpose(source).convert('RGBA')
            flat = Image.new('RGB', photo.size, WHITE)
            flat.paste(photo, mask=photo.getchannel('A'))
            if len(files) == 1 and scene_index % 2 == 1:
                w, h = flat.size
                flat = flat.crop((w // 7, h // 7, w - w // 7, h - h // 7))
            photo = ImageOps.contain(flat, (884, 780), Image.Resampling.LANCZOS)
        frame.paste(photo, ((W - photo.width) // 2, 1038 - photo.height // 2))

    price_label = '₹' + f"{p['price']:,.2f}".removesuffix('.00')
    is_final = scene_index == total_scenes - 1
    if is_final:
        d.rounded_rectangle((76, 1500, 960, 1638), radius=68, fill=theme['accent'] if not dark else LIME)
        cta_label = (cta_text or '').strip() or 'Shop at prodxmart.com'
        copy_block(d, cta_label, (124, 1532), 790, 90, 44, INK, telugu=has_te(cta_label), bold=not has_te(cta_label))
        copy_block(d, f'{title} · {price_label}', (76, 1668), 928, 120, 42, fg, bold=True)
    else:
        bottom = (caption_text or '').strip() or f'{title} · {price_label}'
        copy_block(d, bottom, (76, 1504), 928, 220, 52, fg, telugu=has_te(bottom), bold=not has_te(bottom))
        d.text((76, 1790), f'{price_label} · prodxmart.com', font=font(36, bold=True), fill=accent, anchor='lt')
    return frame


def render_composed_video(
    *,
    scene_specs: list[dict],
    music_wav: Path | None,
    narration_wav: Path | None,
    outfile: Path,
    fps: int = 24,
    duck_music: bool = True,
) -> dict:
    """Render synchronized 1080x1920 H.264 + AAC MP4 from scene frames, music and optional narration with ducking."""
    if not scene_specs:
        raise ValueError('At least one scene is required to render a video.')

    total_duration = sum(max(1, int(s['duration_seconds'])) for s in scene_specs)
    with tempfile.TemporaryDirectory() as temp:
        folder = Path(temp)
        segment_paths: list[Path] = []
        ff = ffmpeg()

        for idx, spec in enumerate(scene_specs):
            dur = max(1, int(spec['duration_seconds']))
            frames = dur * fps
            png_path = folder / f'scene_{idx:02d}.png'
            spec['image'].save(png_path)
            seg_path = folder / f'seg_{idx:02d}.mp4'
            direction = (
                f"zoompan=z='1+0.04*(on/{max(1, frames - 1)})':"
                if idx % 2 == 0
                else f"zoompan=z='1.04-0.04*(on/{max(1, frames - 1)})':"
            )
            vf = (
                f"{direction}x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d={frames}:s={W}x{H}:fps={fps},"
                f"fade=t=in:st=0:d=0.2,fade=t=out:st={max(0.2, dur - 0.25)}:d=0.2"
            )
            seg_cmd = [
                ff, '-hide_banner', '-loglevel', 'error', '-y',
                '-loop', '1', '-i', str(png_path),
                '-vf', vf, '-t', str(dur),
                '-c:v', 'libx264', '-threads', '2', '-preset', 'veryfast', '-crf', '20',
                '-pix_fmt', 'yuv420p', '-an', str(seg_path),
            ]
            subprocess.run(seg_cmd, check=True, timeout=180)
            segment_paths.append(seg_path)

        concat_txt = folder / 'concat.txt'
        concat_txt.write_text(
            '\n'.join(f"file '{p.as_posix()}'" for p in segment_paths),
            encoding='utf-8',
        )

        # Ensure rights-cleared background music is always present if caller did not supply one.
        bg_wav = music_wav or (folder / 'bg_music.wav')
        if music_wav is None:
            write_music(bg_wav, seconds=total_duration)

        final_mp4 = folder / 'final.mp4'
        if narration_wav is not None and narration_wav.is_file():
            if duck_music:
                # Duck music cleanly while narration is active using sidechaincompress, then mix both to AAC.
                filter_complex = (
                    '[1:a]aresample=48000,volume=0.85[bg];'
                    '[2:a]aresample=48000,volume=1.15,asplit=2[vo_sc][vo_mix];'
                    '[bg][vo_sc]sidechaincompress=threshold=0.02:ratio=8:attack=25:release=320[bg_ducked];'
                    '[bg_ducked][vo_mix]amix=inputs=2:duration=first:dropout_transition=2,alimiter=limit=0.95[aout]'
                )
            else:
                filter_complex = (
                    '[1:a]aresample=48000,volume=0.75[bg];'
                    '[2:a]aresample=48000,volume=1.15[vo];'
                    '[bg][vo]amix=inputs=2:duration=first:dropout_transition=2,alimiter=limit=0.95[aout]'
                )
            mux_cmd = [
                ff, '-hide_banner', '-loglevel', 'error', '-y',
                '-f', 'concat', '-safe', '0', '-i', str(concat_txt),
                '-i', str(bg_wav),
                '-i', str(narration_wav),
                '-filter_complex', filter_complex,
                '-map', '0:v', '-map', '[aout]',
                '-t', str(total_duration),
                '-c:v', 'copy',
                '-c:a', 'aac', '-ar', '48000', '-ac', '2', '-b:a', '128k',
                '-movflags', '+faststart', str(final_mp4),
            ]
        else:
            mux_cmd = [
                ff, '-hide_banner', '-loglevel', 'error', '-y',
                '-f', 'concat', '-safe', '0', '-i', str(concat_txt),
                '-i', str(bg_wav),
                '-map', '0:v', '-map', '1:a',
                '-t', str(total_duration),
                '-c:v', 'copy',
                '-c:a', 'aac', '-ar', '48000', '-ac', '2', '-b:a', '128k',
                '-movflags', '+faststart', str(final_mp4),
            ]
        subprocess.run(mux_cmd, check=True, timeout=240)
        outfile.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(final_mp4, outfile)
    return {
        'duration_seconds': total_duration,
        'scene_count': len(scene_specs),
        'width': W,
        'height': H,
        'has_narration': bool(narration_wav is not None and narration_wav.is_file()),
        'ducking_applied': bool(duck_music and narration_wav is not None and narration_wav.is_file()),
    }


def render(p, outfile, demo):
    with tempfile.TemporaryDirectory() as temp:
        folder = Path(temp)
        for i in range(4):
            scene(p, i, demo).save(folder / f'{i}.png')
        write_music(folder / 'music.wav', seconds=15)
        # One image per eight beats. Alternate push-in and pull-back across cuts.
        motion = ("zoompan=z='1+0.045*if(mod(in,2),1-mod(on,90)/89,mod(on,90)/89)':"
                  "x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=90:s=1080x1920:fps=24")
        cmd = [ffmpeg(), '-hide_banner', '-loglevel', 'error', '-y',
               '-framerate', '4/15', '-i', str(folder/'%d.png'), '-i', str(folder/'music.wav'),
               '-map', '0:v', '-map', '1:a', '-vf', motion, '-t', '15',
               '-c:v', 'libx264', '-threads', '2', '-preset', 'veryfast', '-crf', '20',
               '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-ar', '48000', '-b:a', '128k',
               '-movflags', '+faststart', str(folder/'reel.mp4')]
        subprocess.run(cmd, check=True, timeout=240)
        shutil.copyfile(folder/'reel.mp4', outfile)
