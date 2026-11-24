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
from ipaddress import ip_address
from urllib.parse import urlsplit, urljoin, quote

from PIL import Image, ImageOps, UnidentifiedImageError
import engine

MAX_IMAGE = 5 * 1024 * 1024
MAX_PHOTOS = 6


def public_image(url, redirects=0):
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.port not in (None, 443) or redirects > 3):
        raise ValueError('Use a public HTTPS image URL, or upload the image file.')
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
            connection.request('GET', quote(path, safe="/%?=&:@!$'()*+,;~-"), headers={'Accept':'image/*'})
            response = connection.getresponse()
            if response.status in (301,302,303,307,308) and response.getheader('Location'):
                return public_image(urljoin(url,response.getheader('Location')),redirects+1)
            if response.status != 200:
                raise ValueError('Image host refused the download. Upload the file instead.')
            return response.read(MAX_IMAGE + 1)
    finally:
        connection.close()


def photo_bytes(item):
    if 'url' in item:
        try:
            raw = public_image(item['url'])
        except (OSError, http.client.HTTPException):
            raise ValueError('Could not download this image. Upload a JPG, PNG or WebP file instead.') from None
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
    photos = [photo_bytes(item) for item in new]
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
