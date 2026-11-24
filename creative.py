"""Render a silent 15-second vertical product reel from verified catalogue fields."""
from pathlib import Path
import shutil
import subprocess
import tempfile
from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parent
W, H = 720, 1280
INK, PAPER, TEAL = '#173738', '#f7f4ea', '#076861'


def font(size, telugu=False, bold=False):
    family = 'NotoSansTelugu-Regular' if telugu else ('NotoSans-Bold' if bold else 'NotoSans-Regular')
    return ImageFont.truetype(str(ROOT / 'assets' / (family + '.ttf')), size)


def text_block(draw, text, pos, width, size=40, telugu=False, bold=False, max_lines=3):
    # Reduce size until the whole title or caption fits its allotted space.
    for point in range(size, 15, -1):
        face = font(point, telugu, bold)
        lines = ['']
        for word in text.split():
            candidate = (lines[-1] + ' ' + word).strip()
            if lines[-1] and draw.textlength(candidate, font=face) > width:
                lines.append(word)
            else:
                lines[-1] = candidate
        if len(lines) <= max_lines and max(draw.textlength(s, font=face) for s in lines) <= width:
            break
    else:
        raise ValueError('Display text is too long. Shorten the product name or Telugu feature.')
    x, y = pos
    for line in lines:
        draw.text((x, y), line, font=face, fill=INK)
        y += int(point * 1.55)


def ffmpeg():
    if shutil.which('ffmpeg'):
        return shutil.which('ffmpeg')
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def render(p, outfile, demo):
    with tempfile.TemporaryDirectory() as temp:
        folder = Path(temp)
        for i in range(3):
            canvas = Image.new('RGB', (W,H), PAPER)
            d = ImageDraw.Draw(canvas)
            d.text((58,64),'ProdXMart',font=font(33,bold=True),fill=INK)
            d.text((58,134),'SAMPLE PRODUCT' if demo else 'PRODUCT SPOTLIGHT',font=font(20,bold=True),fill=TEAL)
            text_block(d,p['name'],(58,180),604,47,bold=True,max_lines=2)
            with Image.open(p['files'][i % len(p['files'])]) as source:
                img = ImageOps.contain(ImageOps.exif_transpose(source).convert('RGB'),(620,568))
            canvas.paste(img,((W-img.width)//2,315+(568-img.height)//2))
            d.rectangle((58,935,662,939),fill=TEAL)
            if i == 0:
                text_block(d,'ఈ వారం మీ కోసం',(58,972),604,48,True)
            elif i == 1:
                text_block(d,p['feature_te'],(58,972),604,40,True)
            else:
                d.text((58,958),f"₹{p['price']:,.2f}".removesuffix('.00'),font=font(64,bold=True),fill=INK)
                d.text((58,1064),'prodxmart.com',font=font(37),fill=TEAL)
            if demo:
                d.text((58,1164),'Illustration for preview only',font=font(20),fill=TEAL)
            canvas.save(folder / f'{i}.png')
        # Each input still is displayed for five seconds, with gentle camera movement.
        cmd = [ffmpeg(),'-hide_banner','-loglevel','error','-y','-framerate','1/5',
               '-i',str(folder/'%d.png'),'-vf',
               "zoompan=z='1+0.018*mod(on,150)/149':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=150:s=720x1280:fps=30",
               '-frames:v','450','-c:v','libx264','-threads','2','-preset','veryfast','-crf','23',
               '-pix_fmt','yuv420p','-movflags','+faststart',str(folder/'reel.mp4')]
        subprocess.run(cmd,check=True,timeout=240)
        shutil.copyfile(folder/'reel.mp4',outfile)
