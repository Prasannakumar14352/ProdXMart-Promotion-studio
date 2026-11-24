import argparse
import csv
import html
import json
import os
import re
import socket
import threading
import time
import webbrowser
from contextlib import closing
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs
from engine import ROOT, LOCK, batch, catalogue, db, decide, directory, earnings, now, records, remake, video_path
from publisher import run_due, enabled
from product_editor import details, photo_bytes, save, store_photos

ASSETS={'/':('index.html','text/html'),'/ui.js':('ui.js','text/javascript'),'/editor.js':('editor.js','text/javascript'),'/style.css':('style.css','text/css')}


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass

    def respond(self,body,kind='application/json',code=200,headers=None):
        if isinstance(body,(list,dict)): body=json.dumps(body,ensure_ascii=False).encode()
        elif isinstance(body,str):body=body.encode()
        self.send_response(code)
        self.send_header('Content-Type',kind)
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Cache-Control','no-store')
        self.send_header('Content-Length',str(len(body)))
        for k,v in (headers or {}).items():self.send_header(k,v)
        self.end_headers()
        try:self.wfile.write(body)
        except (BrokenPipeError,ConnectionResetError):pass

    def authorized_host(self):
        return self.headers.get('Host')==f'127.0.0.1:{self.server.server_port}'

    def do_GET(self):
        if not self.authorized_host():return self.respond('Local access only','text/plain',403)
        u=urlsplit(self.path)
        try:
            if u.path in ASSETS:
                name,mime=ASSETS[u.path];return self.respond((ROOT/name).read_bytes(),mime+'; charset=utf-8')
            if u.path=='/product':
                return self.respond(details(parse_qs(u.query).get('sku',[''])[0]))
            if u.path=='/photo':
                params=parse_qs(u.query)
                p=catalogue(params['mode'][0])[params['sku'][0]]
                index=int(params.get('index',['0'])[0])
                if not 0<=index<len(p['files']):return self.respond('Photo missing','text/plain',404)
                from PIL import Image,ImageOps
                from io import BytesIO
                with Image.open(p['files'][index]) as image:
                    thumb=ImageOps.exif_transpose(image).convert('RGB')
                    thumb.thumbnail((220,220))
                    result=BytesIO();thumb.save(result,'JPEG',quality=82)
                return self.respond(result.getvalue(),'image/jpeg')
            if u.path=='/state':
                mode=parse_qs(u.query).get('mode',['demo'])[0]
                with closing(db()) as c:
                    jobs=[dict(j) for j in c.execute('SELECT id,sku,name,caption,link,status,due,note,published_id,container FROM jobs WHERE mode=? ORDER BY created DESC',(mode,))]
                products=[dict(sku=p['sku'],name=p['name'],product_url=p['product_url'],stock=p['stock'],category=p['category'],price=float(p['price']),has_photo=bool(p['files'] and p['files'][0].is_file()),margin=float(p['margin']),issues=p['issues']) for p in catalogue(mode).values()]
                start=(now()+timedelta(days=1)).replace(hour=19,minute=0,second=0,microsecond=0)
                traffic={}
                for row in records(directory(mode)/'traffic.csv',['campaign','sessions']):
                    key=row['campaign']; value=int(row['sessions'])
                    if not key or value<0 or key in traffic:raise ValueError('Enter one nonnegative session total per campaign.')
                    traffic[key]=value
                return self.respond(dict(jobs=jobs,products=products,earnings=earnings(mode),traffic=traffic,enabled=enabled(),start=start.strftime('%Y-%m-%dT%H:%M')))
            if u.path=='/shop-links':
                mode=parse_qs(u.query).get('mode',['demo'])[0];directory(mode)
                current=catalogue(mode);links=[];seen=set()
                from engine import signature
                with closing(db()) as c:
                    for j in c.execute("SELECT * FROM jobs WHERE mode=? AND status IN ('Scheduled','Published','Simulated') ORDER BY created DESC",(mode,)):
                        p=current.get(j['sku'])
                        if not p or p['issues'] or j['sku'] in seen or j['snapshot']!=signature(p):continue
                        links.append('<p><a href="'+html.escape(j['link'],quote=True)+'">'+html.escape(j['name'])+'</a></p>')
                        seen.add(j['sku'])
                heading='Sample product links' if mode=='demo' else 'Shop the Reels'
                if not links: return self.respond('Approve an eligible draft before exporting links.','text/plain',400)
                page='<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>ProdXMart</title><style>body{font:18px system-ui;background:#f8f8f2;color:#173c38;max-width:36em;margin:3em auto;padding:1em}a{display:block;background:#125c54;color:white;padding:1em;border-radius:.5em}</style><b>ProdXMart</b><h1>'+heading+'</h1>'+''.join(links)
                return self.respond(page,'text/html; charset=utf-8',headers={'Content-Disposition':'attachment; filename="shop-the-reels.html"'})
            vid=re.fullmatch(r'/video/([0-9a-f]{16})\.mp4',u.path)
            if vid:
                with LOCK, closing(db()) as con:
                    f = video_path(con, vid[1])
                    if not f.is_file():return self.respond('Missing reel','text/plain',404)
                    body = f.read_bytes()
                return self.respond(body,'video/mp4')
            return self.respond('Unknown path','text/plain',404)
        except Exception as e:self.respond({'error':str(e)},code=400)

    def do_POST(self):
        if not self.authorized_host() or self.headers.get('Origin')!='http://'+self.headers.get('Host','') or self.headers.get('Content-Type')!='application/json':
            return self.respond({'error':'Open the dashboard locally.'},code=403)
        try:
            n=int(self.headers.get('Content-Length',0))
            limit=44*1024*1024 if self.path=='/product' else 25000
            if not 0<n<limit:raise ValueError('Invalid request.')
            data=json.loads(self.rfile.read(n))
            mode=data['mode'];directory(mode)
            if self.path=='/store-photos' and mode=='store':
                return self.respond(store_photos(data['url'],data.get('sku',''),data.get('name','')))
            if self.path=='/product-photo' and mode=='store':
                return self.respond(photo_bytes({'url':data['url']}),'image/jpeg')
            if self.path=='/product':
                if mode!='store':raise ValueError('Switch to My store to edit products.')
                msg=save(data)
            elif self.path=='/generate':msg=batch(mode)
            elif self.path=='/remake':msg=remake(mode,data['id'])
            elif self.path=='/decide':msg=decide(mode,data['action'],data['ids'],data.get('start',''))
            else:return self.respond({'error':'Unknown action'},code=404)
            return self.respond({'message':msg})
        except Exception as e:self.respond({'error':str(e)},code=400)


def worker():
    while True:
        try:run_due()
        except Exception as e:print('Queue:',e,flush=True)
        time.sleep(30)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--resolve',metavar='JOB_ID',help='Resolve an interrupted post after inspecting the Instagram account.')
    parser.add_argument('--outcome',choices=['published','unpublished'])
    parser.add_argument('--media-id')
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--no-browser',action='store_true')
    parser.add_argument('--generate',choices=['demo','store'],help='Generate this week’s missing drafts, then exit.')
    parser.add_argument('--due',action='store_true',help='Process due items once and exit.')
    args=parser.parse_args()
    if args.resolve:
        if not args.outcome or (args.outcome=='published' and not (args.media_id or '').isdecimal()):
            parser.error('Supply --outcome and, for a confirmed published post, --media-id.')
        with closing(db()) as c,c:
            n=c.execute("UPDATE jobs SET status=?,published_id=?,note=? WHERE id=? AND mode='store' AND status IN ('Processing','Publishing','Check Instagram')",('Published' if args.outcome=='published' else 'Draft',args.media_id or '','Manually reconciled.',args.resolve)).rowcount
            if not n:parser.error('No matching interrupted store post.')
    elif args.generate:print(batch(args.generate))
    elif args.due:run_due()
    else:
        with ThreadingHTTPServer(('127.0.0.1',args.port),Handler) as server:
            try:
                url=f'http://127.0.0.1:{server.server_port}'
                print('ProdXMart Studio:',url,flush=True)
                threading.Thread(target=worker,daemon=True).start()
                if not args.no_browser:webbrowser.open(url)
                server.serve_forever()
            except KeyboardInterrupt:
                print('\nProdXMart Studio stopped.',flush=True)
