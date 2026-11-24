"""Local catalogue editing and reusable product photographs."""
import base64
import csv
import hashlib
import http.client
import io
import json
import os
import re
import socket
import ssl
import tempfile
from contextlib import closing
from decimal import InvalidOperation
from html.parser import HTMLParser
from ipaddress import ip_address
from urllib.parse import urlsplit, urljoin, quote

from PIL import Image, ImageOps, UnidentifiedImageError
import engine

MAX_IMAGE = 5 * 1024 * 1024
MAX_PHOTOS = 6


def public_image(url):
    return fetch_public(url)[0]


def fetch_public(url, redirects=0, *, product_page=False):
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.port not in (None, 443) or redirects > 3):
        raise ValueError('Use a public HTTPS image URL, or upload the image file.')
    if product_page and (parsed.hostname not in ('prodxmart.com','www.prodxmart.com') or parsed.path in ('','/')):
        raise ValueError('Enter a ProdXMart product page link.')
    host = parsed.hostname.encode('idna').decode()
    addresses = [record[4][0] for record in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)]
    if not addresses or any(not ip_address(addr).is_global or ip_address(addr).is_multicast for addr in addresses):
        raise ValueError('The image link must point to a public host.')
    # Pin the validated destination while retaining the origin name for TLS.
    connection = http.client.HTTPSConnection(host, timeout=12)
    try:
        with socket.create_connection((addresses[0], 443), timeout=12) as raw:
            connection.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
            path = parsed.path or '/'
            if parsed.query:
                path += '?' + parsed.query
            connection.request('GET', quote(path, safe="/%?=&:@!$'()*+,;~-"),
                               headers={'Accept': 'text/html' if product_page else 'image/*', 'User-Agent': 'ProdXMart-Promotion-Studio/1.0'})
            response = connection.getresponse()
            if response.status in (301,302,303,307,308) and response.getheader('Location'):
                return fetch_public(urljoin(url,response.getheader('Location')),redirects+1,product_page=product_page)
            if response.status != 200:
                raise ValueError(f'The host returned HTTP {response.status}.')
            raw = response.read(MAX_IMAGE + 1)
            if product_page and len(raw) > MAX_IMAGE:
                raise ValueError('The public product page is too large to read.')
            return raw, url
    finally:
        connection.close()


class StorePhotoParser(HTMLParser):
    def __init__(self, url, sku, name):
        super().__init__()
        self.url = url
        self.sku = sku.strip().casefold()
        self.name = name.strip().casefold()
        self.photos = []
        self.social_image = None
        self.structured = None

    def add(self, value):
        if not isinstance(value, str) or not value.strip():
            return
        url = urljoin(self.url, value.strip())
        if urlsplit(url).scheme == 'https' and url not in self.photos and len(self.photos) < MAX_PHOTOS:
            self.photos.append(url)

    def handle_starttag(self, tag, pairs):
        attrs = dict(pairs)
        if tag == 'script' and attrs.get('type') == 'application/ld+json':
            self.structured = []
        elif tag == 'meta' and attrs.get('property') == 'og:image':
            self.social_image = attrs.get('content')
        elif tag == 'img':
            src = attrs.get('data-src') or attrs.get('data-original') or attrs.get('src') or ''
            same_name = self.name and (attrs.get('alt') or '').strip().casefold() == self.name
            same_sku = self.sku and re.search(r'(?<![a-z0-9])' + re.escape(self.sku) + r'(?![a-z0-9])',src.casefold())
            if same_name or same_sku:
                self.add(src)

    def handle_data(self, data):
        if self.structured is not None:
            self.structured.append(data)

    def handle_endtag(self, tag):
        if tag != 'script' or self.structured is None:
            return
        raw = ''.join(self.structured)
        self.structured = None
        try:
            node = json.loads(raw)
        except ValueError:
            return
        pending = node if isinstance(node, list) else [node]
        while pending:
            item = pending.pop()
            if not isinstance(item, dict):
                continue
            for key in ('@graph','mainEntity'):
                child = item.get(key)
                if child:
                    pending.extend(child if isinstance(child,list) else [child])
            if item.get('@type') != 'Product':
                continue
            sku = str(item.get('sku') or '').casefold()
            name = str(item.get('name') or '').strip().casefold()
            if sku and self.sku:
                matches = sku == self.sku
            else:
                matches = bool(name and name == self.name)
            if matches:
                images = item.get('image', [])
                for image in images if isinstance(images,list) else [images]:
                    self.add(image.get('contentUrl') or image.get('url') if isinstance(image,dict) else image)


def store_photos(url, sku, name):
    try:
        raw, final_url = fetch_public(url, product_page=True)
    except (OSError, http.client.HTTPException):
        raise ValueError('Could not reach the public product page. Open its link in Chrome and check it loads.') from None
    parser = StorePhotoParser(final_url, sku, name)
    parser.feed(raw.decode('utf-8', errors='replace'))
    parser.close()
    if not parser.photos:
        parser.add(parser.social_image)
    return {'images':parser.photos}


def photo_bytes(item):
    if 'url' in item:
        try:
            raw = public_image(item['url'])
        except (OSError, http.client.HTTPException):
            raise ValueError('Could not reach the image host or finish the download.') from None
    else:
        raw = base64.b64decode(item.get('base64', ''), validate=True)
    if not 0 < len(raw) <= MAX_IMAGE:
        raise ValueError('Each photo must be between 1 byte and 5 MB.')
    try:
        with Image.open(io.BytesIO(raw), formats=('JPEG','PNG','WEBP')) as source:
            if source.width * source.height > 20_000_000:
                raise ValueError('Please resize photos to 20 megapixels or less.')
            src = ImageOps.exif_transpose(source).convert('RGBA')
            src.thumbnail((1800, 1800))
            image = Image.new('RGB',src.size,'white')
            image.paste(src,mask=src.getchannel('A'))
            out = io.BytesIO()
            image.save(out,format='JPEG',quality=91)
            return out.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ValueError('One photo could not be read. Choose a JPG, PNG or WebP image.') from None


def version(row):
    return hashlib.sha256(json.dumps(row,sort_keys=True).encode()).hexdigest()


def prepare_store():
    base = engine.ROOT/'store'
    base.mkdir(exist_ok=True)
    (engine.ROOT/'media').mkdir(exist_ok=True)
    for template in (engine.ROOT/'templates').glob('*.csv'):
        try:
            with (base/template.name).open('xb') as dest:
                dest.write(template.read_bytes())
        except FileExistsError:
            pass


def read_rows():
    path = engine.directory('store')/'catalogue.csv'
    with path.open(encoding='utf-8-sig',newline='') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        if not reader.fieldnames or any(None in row or None in row.values() for row in rows):
            raise ValueError('Malformed catalogue. Check CSV headers and rows.')
        return list(reader.fieldnames), rows


def details(sku):
    mapping = engine.config()['catalogue_columns']
    _, rows = read_rows()
    for row in rows:
        if row[mapping.get('sku','sku')].strip() == sku:
            result = {k:row[mapping.get(k,k)] for k in engine.HEADERS}
            result.update(version=version(row), photos=[p.strip() for p in result['photos'].split('|') if p.strip()])
            return result
    raise ValueError('Product no longer exists. Refresh the catalogue.')


def save(data):
    values = {key:str(data.get(key,'')).strip() for key in engine.HEADERS}
    if not values['name'] or not values['sku'] or not values['category']:
        raise ValueError('Enter a product name, SKU and category.')
    if any(len(values[k]) > n for k,n in [('sku',120),('name',180),('category',120),('feature_te',300),('product_url',2500)]):
        raise ValueError('Please shorten the product details.')
    u = urlsplit(values['product_url'])
    if u.scheme!='https' or u.netloc.lower() not in ('prodxmart.com','www.prodxmart.com') or u.path in ('','/'):
        raise ValueError('Paste the full HTTPS link to a product on prodxmart.com.')
    try:
        for k in ('price','cost','fees'):
            engine.amount(values[k])
        if engine.amount(values['price']) <= 0 or int(values['stock']) < 0:
            raise ValueError()
    except (ValueError,InvalidOperation):
        raise ValueError('Use a positive price, nonnegative costs and a whole stock quantity.') from None
    values['media_ok'] = 'yes' if data.get('media_ok') is True else 'no'
    kept, new = data.get('keep',[]), data.get('new',[])
    if not isinstance(kept,list) or not isinstance(new,list) or len(new)>MAX_PHOTOS:
        raise ValueError('Choose up to six new photos.')
    if len(kept)+len(new)>MAX_PHOTOS and new:
        raise ValueError('Keep up to six photos before adding another.')
    photos = []
    link_number = 0
    for item in new:
        if 'url' not in item:
            photos.append(photo_bytes(item))
            continue
        link_number += 1
        try:
            photos.append(photo_bytes(item))
        except ValueError as error:
            raise ValueError(f'Image link {link_number}: {error} Remove that link, then paste the copied photo or choose its file.') from None
    path = engine.directory('store')/'catalogue.csv'
    mapping = engine.config()['catalogue_columns']
    col = lambda k:mapping.get(k,k)
    with engine.LOCK, closing(engine.db()) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        fields, rows = read_rows()
        current = next((r for r in rows if r[col('sku')].strip()==values['sku']), None)
        if current is not None and data.get('version')!=version(current):
            raise ValueError('This SKU already exists or changed. Open its latest version using Edit.')
        if current is None and data.get('version'):
            raise ValueError('Product changed. Refresh the catalogue and reopen the editor.')
        old_photos = [] if current is None else [p.strip() for p in current[col('photos')].split('|') if p.strip()]
        if any(name not in old_photos for name in kept):
            raise ValueError('Existing photos changed. Reopen the product.')
        names=list(kept)
        media=engine.ROOT/'media'/'uploaded'
        media.mkdir(parents=True,exist_ok=True)
        for body in photos:
            name=hashlib.sha256(body).hexdigest()+'.jpg'
            dest=media/name
            if not dest.exists():
                with tempfile.NamedTemporaryFile(dir=media,delete=False) as tmp:
                    tmp.write(body)
                os.replace(tmp.name,dest)
            if 'uploaded/'+name not in names:
                names.append('uploaded/'+name)
        values['photos']='|'.join(names)
        stamp='_studio_verified_at'
        if stamp not in fields:
            fields.append(stamp)
        for r in rows:
            if not r.get(stamp):
                r[stamp]=str(path.stat().st_mtime)
        if current is None:
            current=dict.fromkeys(fields,'')
            rows.append(current)
        current.update({col(k):v for k,v in values.items()})
        current[stamp]=str(engine.now().timestamp())
        tmp_name=None
        try:
            with tempfile.NamedTemporaryFile(mode='w',dir=path.parent,encoding='utf-8-sig',newline='',delete=False) as f:
                tmp_name=f.name
                writer=csv.DictWriter(f,fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
            os.replace(tmp_name,path)
        finally:
            if tmp_name and os.path.exists(tmp_name):os.unlink(tmp_name)
    return values['name']+' saved.'
