"""Render a four-scene vertical product Reel using local catalogue assets."""
from functools import lru_cache
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


def render(p, outfile, demo):
    with tempfile.TemporaryDirectory() as temp:
        folder = Path(temp)
        for i in range(4):
            scene(p, i, demo).save(folder / f'{i}.png')
        write_music(folder / 'music.wav')
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
