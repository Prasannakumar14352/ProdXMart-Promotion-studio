import csv
import hashlib
import json
import os
import sqlite3
import threading
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl
from creative import ROOT, render

IST = timezone(timedelta(hours=5, minutes=30))
LOCK = threading.RLock()
DATA = ROOT/'data'
OUT = DATA/'reels'
OUT.mkdir(parents=True, exist_ok=True)
now = lambda: datetime.now(IST)
HEADERS = 'sku name category price cost fees stock feature_te product_url photos media_ok'.split()


def config():
    cfg = json.loads((ROOT/'settings.json').read_text(encoding='utf-8-sig'))
    if not 1 <= cfg['weekly_slots'] <= 7 or min(cfg['minimum_stock'], cfg['maximum_catalogue_age_hours']) < 1:
        raise ValueError('Check slot, age and stock limits in settings.json.')
    return cfg


def directory(mode):
    if mode not in ('demo','store'):
        raise ValueError('Unknown workspace.')
    if mode == "store":
        from product_editor import prepare_store
        prepare_store()
    return ROOT/mode


def db():
    con = sqlite3.connect(DATA/'studio.sqlite',timeout=20)
    con.row_factory = sqlite3.Row
    con.execute('''CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY, mode TEXT, week TEXT, sku TEXT, name TEXT, snapshot TEXT,
        caption TEXT, link TEXT, status TEXT, due TEXT DEFAULT '', note TEXT DEFAULT '',
        container TEXT DEFAULT '', published_id TEXT DEFAULT '', created TEXT)''')
    return con


def records(path, required, mapping=None):
    with path.open(encoding='utf-8-sig', newline='') as handle:
        reader=csv.DictReader(handle)
        fields=reader.fieldnames or []
        mapping=mapping or {}
        missing=[k for k in required if mapping.get(k,k) not in fields]
        if missing:
            raise ValueError(f'{path.name} needs columns: '+', '.join(missing))
        for n,row in enumerate(reader,2):
            if None in row:
                raise ValueError(f'{path.name}, row {n}: quote cells containing commas.')
            if any((v or '').strip() for v in row.values()):
                item = {k:(row.get(mapping.get(k,k)) or '').strip() for k in required}
                if row.get('_studio_verified_at'):
                    item['_studio_verified_at'] = row['_studio_verified_at']
                yield item


def amount(raw):
    n=Decimal(raw)
    if not n.is_finite() or n<0 or n!=n.quantize(Decimal('.01')):
        raise ValueError('Amounts must be nonnegative, with up to two decimal places.')
    return n


def catalogue(mode):
    base=directory(mode); cfg=config(); items={}
    for p in records(base/'catalogue.csv',HEADERS,cfg['catalogue_columns'] if mode=='store' else {}):
        sku=p['sku']
        if not sku or sku in items or not p['name']:
            raise ValueError(f'Missing name or duplicate/empty SKU: {sku}')
        for key in ('price','cost','fees'):
            p[key]=amount(p[key])
        p['stock']=int(p['stock'])
        if p['stock']<0: raise ValueError(f'Negative stock for {sku}')
        url=urlsplit(p['product_url'])
        allowed=('prodxmart.com','www.prodxmart.com') if mode=='store' else ('example.com',)
        if url.scheme!='https' or url.hostname not in allowed or url.username or url.password:
            raise ValueError(f'{sku} needs a product URL on the store domain.')
        p['margin']=p['price']-p['cost']-p['fees']
        media=(ROOT/'media' if mode=='store' else base).resolve()
        paths=[(media/s.strip()).resolve() for s in p['photos'].split('|') if s.strip()]
        if any(media not in f.parents for f in paths): raise ValueError(f'Invalid photo path for {sku}')
        p['files']=paths
        issues=[]
        if cfg['category'] and mode=='store' and p['category'].casefold()!=cfg['category'].casefold(): issues.append('Outside category')
        if p['stock']<cfg['minimum_stock']: issues.append('Low stock')
        if p['margin']<Decimal(str(cfg['minimum_margin_rupees'])): issues.append('Low margin')
        if p['media_ok'].lower()!='yes': issues.append('Media permission missing')
        if not paths or not all(f.is_file() for f in paths): issues.append('Photo missing')
        if not p['feature_te']:
            p['feature_te']='మరిన్ని ఫొటోలు, వివరాలు స్టోర్‌లో చూడండి'
        if mode == 'store':
            verified_at = float(p.get('_studio_verified_at', (base/'catalogue.csv').stat().st_mtime))
            age = now().timestamp() - verified_at
            if age < -300 or age > cfg['maximum_catalogue_age_hours'] * 3600:
                issues.append('Reconfirm price and stock')
        p['issues']=issues
        items[sku]=p
    return items


def signature(p):
    parts=[p['name'],p['category'],str(p['price'].quantize(Decimal('.01'))),p['feature_te'],p['product_url']]
    for f in p['files']:
        parts.append(hashlib.sha256(f.read_bytes()).hexdigest())
    return hashlib.sha256(json.dumps(parts,ensure_ascii=False).encode()).hexdigest()


def tracking(url, jid):
    u=urlsplit(url)
    args=[(k,v) for k,v in parse_qsl(u.query,keep_blank_values=True) if not k.startswith('utm_')]
    args.extend({'utm_source':'instagram','utm_medium':'organic_social','utm_campaign':jid}.items())
    return urlunsplit(u._replace(query=urlencode(args)))


def batch(mode):
    with LOCK, closing(db()) as con, con:
        # Reserve this batch in a transaction before rendering to avoid duplicate work.
        con.execute('BEGIN IMMEDIATE')
        week=now().strftime('%G-W%V')
        jobs=con.execute('SELECT sku,status FROM jobs WHERE mode=? AND week=?',(mode,week)).fetchall()
        count=sum(j['status'] not in ('Skipped','Held') for j in jobs)
        seen={j['sku'] for j in jobs if j['status']!='Held'}
        products=catalogue(mode)
        recent=dict(con.execute('SELECT sku,MAX(created) FROM jobs WHERE mode=? GROUP BY sku',(mode,)).fetchall())
        candidates=sorted((p for p in products.values() if not p['issues'] and p['sku'] not in seen),
                          key=lambda p:(recent.get(p['sku'],''),-p['margin'],-p['stock'],p['sku']))
        created=[]
        try:
            for p in candidates[:max(0, config()['weekly_slots']-count)]:
                jid=uuid.uuid4().hex[:16]
                target=OUT/(jid+'.mp4');created.append(target)
                sample=ROOT/'preview'/(p['sku']+'.mp4')
                # Demo cache is for the shipped, unchanged creative only.
                manifest=ROOT/'preview'/'index.json'
                if mode=='demo' and sample.exists() and manifest.exists() and json.loads(manifest.read_text()).get(p['sku'])==signature(p):
                    import shutil
                    shutil.copyfile(sample,target)
                else:
                    render(p,target,mode=='demo')
                caption=f"{p['name']} · ₹{p['price']:,.2f}\n{p['feature_te']}\nవివరాల కోసం prodxmart.com చూడండి."
                if mode=='demo':caption='[Sample preview]\n'+caption
                con.execute('INSERT INTO jobs(id,mode,week,sku,name,snapshot,caption,link,status,created) VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (jid,mode,week,p['sku'],p['name'],signature(p),caption,tracking(p['product_url'],jid),'Draft',now().isoformat()))
            con.commit()
        except BaseException:
            for f in created: f.unlink(missing_ok=True)
            raise
        return f'{len(created)} new drafts. {len(candidates)} eligible candidates available before selection.'


def reason(mode,job):
    p=catalogue(mode).get(job['sku'])
    if not p: return 'Product removed from current catalogue.'
    if p['issues']: return ', '.join(p['issues'])
    if signature(p)!=job['snapshot']: return 'Price, product information or photos changed. Generate a new draft.'
    if mode=='store':
        age=datetime.now().timestamp()-float(p.get('_studio_verified_at', (directory(mode)/'catalogue.csv').stat().st_mtime))
        if age< -300 or age>config()['maximum_catalogue_age_hours']*3600:
            return 'Stock and price need confirmation. Review this product and save it again, or import a fresh snapshot.'
    return ''


def decide(mode,action,ids,start):
    with LOCK,closing(db()) as c,c:
        c.execute('BEGIN IMMEDIATE')
        if action not in ('approve','skip'): raise ValueError('Unknown action.')
        if action=='approve':
            date=datetime.fromisoformat(start).replace(tzinfo=IST)
            if date<=now():raise ValueError('Choose a future first posting time in India.')
        for jid in dict.fromkeys(ids):
            j=c.execute('SELECT * FROM jobs WHERE mode=? AND id=?',(mode,jid)).fetchone()
            if not j or j['status'] not in ('Draft','Scheduled'):raise ValueError('The queue changed. Refresh your view.')
            due='';note='';state='Skipped'
            if action=='approve':
                note=reason(mode,j)
                state='Held' if note else 'Scheduled'
                if not note:
                    due=date.isoformat();date+=timedelta(days=2)
            c.execute('UPDATE jobs SET status=?,due=?,note=? WHERE id=?',(state,due,note,jid))
    return 'Queue updated.'


def earnings(mode):
    base=directory(mode); ids=set(); orders=set(); units=0; sales=cost=fees=spend=Decimal(0); campaigns={}
    heads='order_id line_id sku delivered_units returned_units sales refunds net_product_cost fees campaign'.split()
    for r in records(base/'orders.csv',heads):
        key=(r['order_id'],r['line_id'])
        if not all(key) or key in ids or not r['sku']:raise ValueError('Each order line needs a unique order ID + line ID and SKU.')
        ids.add(key);orders.add(key[0])
        q,ret=int(r['delivered_units']),int(r['returned_units'])
        s,f,c,g=[amount(r[k]) for k in ('sales','refunds','net_product_cost','fees')]
        if not 0<=ret<=q or f>s:raise ValueError('Check returned units or refunds in orders.csv.')
        units+=q-ret;sales+=s-f;cost+=c;fees+=g
        key=r['campaign'] or 'Unattributed'
        campaigns[key]=campaigns.get(key,0)+q-ret
    seen=set()
    for row in records(base/'expenses.csv',['reference','type','amount']):
        if not row['reference'] or row['reference'] in seen:raise ValueError('Duplicate or empty expense reference.')
        seen.add(row['reference']);spend+=amount(row['amount'])
    return dict(orders=len(orders),units=units,sales=float(sales),profit=float(sales-cost-fees-spend),campaigns=campaigns)
