import argparse
import csv
import html
import json
import os
from pathlib import Path
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
from agents.ollama_client import OllamaClient, OllamaConfig
from agents.orchestrator import MasterOrchestrator, build_default_orchestrator
from agents.marketing_agent import create_campaign_drafts_for_items
from agents.credentials import SecureCredentialStore
from agents.social_agent import (
    approve_social_post,
    confirm_social_post_published,
    edit_social_post,
    publish_or_prepare_social_post,
    reconcile_social_approvals,
    reject_social_post,
    schedule_social_post,
)

ASSETS={
    '/':('index.html','text/html'),
    '/ui.js':('ui.js','text/javascript'),
    '/editor.js':('editor.js','text/javascript'),
    '/agents.js':('agents.js','text/javascript'),
    '/style.css':('style.css','text/css'),
    '/css/tokens.css':('css/tokens.css','text/css'),
    '/css/components.css':('css/components.css','text/css'),
    '/css/layout.css':('css/layout.css','text/css'),
    '/js/ui_core.js':('js/ui_core.js','text/javascript'),
    '/js/overview.js':('js/overview.js','text/javascript'),
    '/js/agent_views.js':('js/agent_views.js','text/javascript'),
    '/js/phase4_studio.js':('js/phase4_studio.js','text/javascript'),
    '/js/phase5_publishing.js':('js/phase5_publishing.js','text/javascript'),
    '/vendor/lucide.min.js':('assets/vendor/lucide.min.js','text/javascript'),
    '/vendor/chart.umd.min.js':('assets/vendor/chart.umd.min.js','text/javascript'),
}

_ORCH_LOCK = threading.Lock()
_ORCHESTRATOR = None


def get_orchestrator():
    global _ORCHESTRATOR
    with _ORCH_LOCK:
        if _ORCHESTRATOR is None:
            _ORCHESTRATOR = build_default_orchestrator()
        _ORCHESTRATOR.start()
        return _ORCHESTRATOR


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
            if u.path=='/api/overview':
                orch = getattr(self.server, 'orchestrator', None) or get_orchestrator()
                params=parse_qs(u.query)
                mode=params.get('mode',['store'])[0];directory(mode)
                date_range=params.get('range',['month'])[0]
                return self.respond(build_overview_payload(orch, mode=mode, date_range=date_range))
            if u.path=='/api/system/integrations':
                orch = getattr(self.server, 'orchestrator', None) or get_orchestrator()
                params=parse_qs(u.query)
                mode=params.get('mode',['store'])[0];directory(mode)
                return self.respond(build_system_integrations_payload(orch, mode=mode))
            if u.path=='/state':
                mode=parse_qs(u.query).get('mode',['demo'])[0]
                with closing(db()) as c:
                    jobs=[dict(j) for j in c.execute('SELECT id,sku,name,caption,link,status,due,note,published_id,container FROM jobs WHERE mode=? ORDER BY created DESC',(mode,))]
                products=[dict(sku=p['sku'],name=p['name'],product_url=p['product_url'],stock=p['stock'],category=p['category'],price=float(p['price']),cost=float(p['cost']),fees=float(p['fees']),media_ok=(p.get('media_ok','').lower()=='yes'),photo_count=len(p['files']),has_photo=bool(p['files'] and p['files'][0].is_file()),margin=float(p['margin']),issues=p['issues']) for p in catalogue(mode).values()]
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
            orch = getattr(self.server, 'orchestrator', None) or get_orchestrator()
            if u.path=='/agents/state':
                params=parse_qs(u.query)
                mode=params.get('mode',['store'])[0];directory(mode)
                return self.respond(orch.get_state(mode=mode))
            if u.path=='/agents/history':
                params=parse_qs(u.query)
                mode=params.get('mode',['store'])[0];directory(mode)
                agent_id=params.get('agent_id',[''])[0] or None
                run_id=params.get('run_id',[''])[0] or None
                limit=int(params.get('limit',['40'])[0])
                runs=orch.storage.list_runs(agent_id=agent_id,mode=mode,limit=limit)
                logs=orch.storage.list_logs(run_id=run_id,agent_id=agent_id,limit=120)
                return self.respond({'runs':runs,'logs':logs})
            if u.path=='/agents/catalog/status':
                params=parse_qs(u.query)
                mode=params.get('mode',['store'])[0];directory(mode)
                from engine import config as studio_config
                freshness=orch.storage.get_catalog_freshness_summary(mode=mode,max_age_hours=studio_config()['maximum_catalogue_age_hours'])
                staged=orch.storage.get_staged_catalog_batch(mode)
                staged_meta={'source':staged['source'],'item_count':staged['item_count'],'pages_count':staged['pages_count'],'staged_at':staged['staged_at']} if staged else None
                return self.respond({'mode':mode,'freshness':freshness,'items':orch.storage.list_catalog_sync_items(mode=mode),'staged':staged_meta})
            if u.path=='/agents/marketing/campaigns':
                params=parse_qs(u.query)
                mode=params.get('mode',['store'])[0];directory(mode)
                return self.respond({
                    'mode':mode,
                    'campaigns':orch.storage.list_marketing_campaigns(mode=mode,limit=15),
                    'video_handoff':orch.storage.get_latest_handoff('video_agent',mode=mode),
                })
            if u.path=='/agents/video/productions':
                params=parse_qs(u.query)
                mode=params.get('mode',['store'])[0];directory(mode)
                return self.respond({
                    'mode':mode,
                    'productions':orch.storage.list_video_productions(mode=mode,limit=20),
                    'qa_reports':orch.storage.list_qa_reports(mode=mode,limit=20),
                    'qa_handoff':orch.storage.get_latest_handoff('qa_agent',mode=mode),
                })
            if u.path=='/agents/social/state':
                params=parse_qs(u.query)
                mode=params.get('mode',['store'])[0];directory(mode)
                reconcile_social_approvals(orch.storage,mode)
                return self.respond({
                    'mode':mode,
                    'posts':orch.storage.list_social_posts(mode=mode,limit=100),
                    'settings':orch.storage.get_social_settings(),
                    'credentials':SecureCredentialStore().list_status(),
                })
            pkg_match=re.fullmatch(r'/agents/social/package/([0-9a-f]+)\.zip',u.path)
            if pkg_match:
                params=parse_qs(u.query)
                mode=params.get('mode',['store'])[0];directory(mode)
                if orch.storage.is_global_publishing_stopped():
                    return self.respond('Global publishing stop is active.','text/plain',403)
                reconcile_social_approvals(orch.storage,mode)
                post=orch.storage.get_social_post(pkg_match[1],mode=mode)
                if not post:return self.respond('Package not found','text/plain',404)
                if post['status'] not in ('Approved','Scheduled'):
                    return self.respond('Explicit human approval is required before downloading publishing package.','text/plain',403)
                updated=publish_or_prepare_social_post(orch.storage,mode=mode,post_id=post['id'],adapter_mode='manual_package')
                from pathlib import Path
                pkg_file=Path(updated['package_path'])
                if not pkg_file.is_file():return self.respond('Package missing','text/plain',404)
                return self.respond(pkg_file.read_bytes(),'application/zip',headers={'Content-Disposition':f'attachment; filename="{post["id"]}_{post["platform"]}.zip"'})
            return self.respond('Unknown path','text/plain',404)
        except Exception as e:self.respond({'error':str(e)},code=400)

    def do_POST(self):
        if not self.authorized_host() or self.headers.get('Origin')!='http://'+self.headers.get('Host','') or self.headers.get('Content-Type')!='application/json':
            return self.respond({'error':'Open the dashboard locally.'},code=403)
        try:
            n=int(self.headers.get('Content-Length',0))
            limit=44*1024*1024 if self.path=='/product' else (8*1024*1024 if self.path in ('/agents/catalog/stage','/agents/run') else 25000)
            if not 0<n<limit:raise ValueError('Invalid request.')
            data=json.loads(self.rfile.read(n))
            mode=data.get('mode','store') if self.path.startswith('/agents/') else data['mode']
            directory(mode)
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
            elif self.path=='/agents/config':
                orch = getattr(self.server, 'orchestrator', None) or get_orchestrator()
                cfg = orch.update_config(data)
                return self.respond({'message':'Agent configuration saved.','config':cfg})
            elif self.path=='/agents/ollama/test':
                orch = getattr(self.server, 'orchestrator', None) or get_orchestrator()
                saved = orch.get_config()
                base_url = data.get('ollama_base_url') or saved['ollama_base_url']
                model = data.get('default_model') or saved['default_model']
                if getattr(self.server, 'orchestrator', None) is not None and base_url == saved['ollama_base_url'] and model == saved['default_model']:
                    result = orch.test_ollama_connection()
                else:
                    client = orch.ollama_factory(OllamaConfig(base_url=base_url, model=model, timeout_seconds=5.0))
                    result = client.test_connection(model=model)
                return self.respond(result)
            elif self.path=='/agents/toggle':
                orch = getattr(self.server, 'orchestrator', None) or get_orchestrator()
                updated = orch.set_agent_enabled(data['agent_id'], bool(data['enabled']))
                state_word = 'enabled' if updated['enabled'] else 'disabled'
                return self.respond({'message':f"{data['agent_id']} {state_word}.",'agent':updated})
            elif self.path=='/agents/model':
                orch = getattr(self.server, 'orchestrator', None) or get_orchestrator()
                updated = orch.set_agent_model(data['agent_id'], str(data.get('model_override','')))
                return self.respond({'message':'Agent model updated.','agent':updated})
            elif self.path=='/agents/run':
                orch = getattr(self.server, 'orchestrator', None) or get_orchestrator()
                run = orch.enqueue_run(data['agent_id'], mode=mode, payload=data.get('payload') or {}, trigger='manual')
                return self.respond({'message':f"Queued {data['agent_id']}.",'run':run})
            elif self.path=='/agents/stop':
                orch = getattr(self.server, 'orchestrator', None) or get_orchestrator()
                run = orch.stop_run(data['run_id'])
                return self.respond({'message':'Stop requested.','run':run})
            elif self.path=='/agents/retry':
                orch = getattr(self.server, 'orchestrator', None) or get_orchestrator()
                run = orch.retry_run(data['run_id'])
                return self.respond({'message':f"Retry queued for {run['agent_id']}.",'run':run})
            elif self.path=='/agents/catalog/stage':
                orch = getattr(self.server, 'orchestrator', None) or get_orchestrator()
                source = str(data.get('source','extension')).strip().lower()
                staged = orch.storage.stage_catalog_batch(mode, source, data)
                return self.respond({'message':f"Staged {staged['item_count']} product(s) across {staged['pages_count']} page(s).",'staged':staged})
            elif self.path=='/agents/catalog/clear':
                orch = getattr(self.server, 'orchestrator', None) or get_orchestrator()
                orch.storage.clear_staged_catalog_batch(mode)
                return self.respond({'message':'Cleared staged catalog batch.'})
            elif self.path=='/agents/marketing/regenerate':
                orch = getattr(self.server, 'orchestrator', None) or get_orchestrator()
                cid = str(data.get('campaign_id','')).strip()
                existing = orch.storage.get_marketing_campaign(cid, mode=mode)
                if not existing:
                    raise ValueError('Marketing campaign not found.')
                payload = {
                    'campaign_type': data.get('campaign_type', existing['campaign_type']),
                    'language': data.get('language', existing['language']),
                    'season_theme': data.get('season_theme', existing['season_theme']),
                    'category': data.get('category', existing['category']),
                    'skus': [data['sku']] if data.get('sku') else existing['selected_skus'],
                    'max_products': len(existing['selected_skus']) or 3,
                    'regenerate_campaign_id': cid,
                }
                run = orch.enqueue_run('marketing_agent', mode=mode, payload=payload, trigger='manual')
                return self.respond({'message':f'Regenerating marketing campaign {cid}.','run':run})
            elif self.path=='/agents/marketing/create-drafts':
                orch = getattr(self.server, 'orchestrator', None) or get_orchestrator()
                cid = str(data.get('campaign_id','')).strip()
                existing = orch.storage.get_marketing_campaign(cid, mode=mode)
                if not existing:
                    raise ValueError('Marketing campaign not found.')
                result = create_campaign_drafts_for_items(mode=mode, campaign=existing['campaign'], skus=data.get('skus'))
                if result['created_job_ids']:
                    orch.storage.link_marketing_campaign_jobs(cid, mode, result['created_job_ids'])
                return self.respond({
                    'message':f"Created {len(result['created_job_ids'])} Reel draft(s) in Reel queue.",
                    **result,
                })
            elif self.path=='/agents/video/regenerate':
                orch = getattr(self.server, 'orchestrator', None) or get_orchestrator()
                pid = str(data.get('production_id','')).strip()
                existing = orch.storage.get_video_production(pid, mode=mode)
                if not existing:
                    raise ValueError('Video production not found.')
                payload = {
                    'skus': data.get('skus') or existing['skus'],
                    'duration_seconds': data.get('duration_seconds', existing['duration_seconds']),
                    'language': data.get('language', existing['language']),
                    'voiceover_mode': data.get('voiceover_mode', 'auto'),
                    'recorded_audio_path': data.get('recorded_audio_path', ''),
                    'duck_music': bool(data.get('duck_music', True)),
                    'regenerate_production_id': pid,
                }
                run = orch.enqueue_run('video_agent', mode=mode, payload=payload, trigger='manual')
                return self.respond({'message':f'Regenerating video {pid}.','run':run})
            elif self.path=='/agents/qa/review':
                orch = getattr(self.server, 'orchestrator', None) or get_orchestrator()
                updated = orch.storage.record_human_qa_review(
                    production_id=str(data.get('production_id','')).strip(),
                    mode=mode,
                    decision=str(data.get('decision','')).strip().lower(),
                    checklist=data.get('checklist') or {},
                    note=str(data.get('note','')).strip(),
                )
                return self.respond({'message':f"Human QA review saved ({updated['human_review_status']}).",'production':updated})
            elif self.path=='/agents/social/approve':
                orch = getattr(self.server, 'orchestrator', None) or get_orchestrator()
                post = approve_social_post(orch.storage, mode=mode, post_id=str(data.get('post_id','')).strip(), note=str(data.get('note','')).strip())
                return self.respond({'message':f"Approved {post['platform']} post {post['id']}.",'post':post})
            elif self.path=='/agents/social/reject':
                orch = getattr(self.server, 'orchestrator', None) or get_orchestrator()
                post = reject_social_post(orch.storage, mode=mode, post_id=str(data.get('post_id','')).strip(), reason=str(data.get('reason','')).strip())
                return self.respond({'message':f"Rejected post {post['id']} (moved to Draft).",'post':post})
            elif self.path=='/agents/social/edit':
                orch = getattr(self.server, 'orchestrator', None) or get_orchestrator()
                post = edit_social_post(orch.storage, mode=mode, post_id=str(data.get('post_id','')).strip(), updates=data)
                return self.respond({'message':f"Updated post {post['id']} ({post['status']}).",'post':post})
            elif self.path=='/agents/social/regenerate':
                orch = getattr(self.server, 'orchestrator', None) or get_orchestrator()
                run = orch.enqueue_run('social_agent', mode=mode, payload={'regenerate_post_id': str(data.get('post_id','')).strip()}, trigger='manual')
                return self.respond({'message':'Regenerating social post…','run':run})
            elif self.path=='/agents/social/schedule':
                orch = getattr(self.server, 'orchestrator', None) or get_orchestrator()
                post = schedule_social_post(orch.storage, mode=mode, post_id=str(data.get('post_id','')).strip(), scheduled_at=str(data.get('scheduled_at','')).strip())
                return self.respond({'message':f"Scheduled post {post['id']}.",'post':post})
            elif self.path=='/agents/social/publish':
                orch = getattr(self.server, 'orchestrator', None) or get_orchestrator()
                post = publish_or_prepare_social_post(orch.storage, mode=mode, post_id=str(data.get('post_id','')).strip(), adapter_mode=str(data.get('adapter_mode','manual_package')))
                return self.respond({'message':'Manual publishing package prepared. Confirm with actual post URL once published.','post':post})
            elif self.path=='/agents/social/confirm-published':
                orch = getattr(self.server, 'orchestrator', None) or get_orchestrator()
                post = confirm_social_post_published(
                    orch.storage, mode=mode, post_id=str(data.get('post_id','')).strip(),
                    published_url=str(data.get('published_url','')).strip(),
                    external_post_id=str(data.get('external_post_id','')).strip(),
                )
                return self.respond({'message':f"Confirmed published URL for {post['id']}.",'post':post})
            elif self.path=='/agents/social/global-stop':
                orch = getattr(self.server, 'orchestrator', None) or get_orchestrator()
                settings = orch.storage.set_global_publishing_stopped(bool(data.get('stopped')), str(data.get('reason','')).strip())
                word = 'STOPPED' if settings['global_publishing_stopped'] else 'resumed'
                return self.respond({'message':f'Global publishing {word}.','settings':settings})
            elif self.path=='/agents/social/credentials':
                store = SecureCredentialStore()
                creds = data.get('credentials') or {}
                if not isinstance(creds, dict):
                    raise ValueError('credentials must be an object.')
                for k, v in creds.items():
                    store.set_credential(str(k), str(v or ''))
                return self.respond({'message':'Credentials encrypted and saved.','credentials':store.list_status()})
            else:return self.respond({'error':'Unknown action'},code=404)
            return self.respond({'message':msg})
        except Exception as e:self.respond({'error':str(e)},code=400)


def build_overview_payload(orch, *, mode: str, date_range: str = 'month') -> dict:
    from decimal import Decimal
    import shutil
    from agents.social_agent import reconcile_social_approvals
    base = directory(mode)
    items = catalogue(mode)
    products = list(items.values())
    ready_products = [p for p in products if not p['issues']]
    attention_products = [p for p in products if p['issues']]

    issue_counts = {}
    for p in attention_products:
        for iss in p['issues']:
            issue_counts[iss] = issue_counts.get(iss, 0) + 1

    recommended = sorted(
        ready_products,
        key=lambda p: (-p['margin'], -p['stock'], p['sku']),
    )[:6]
    recommended_list = [
        {
            'sku': p['sku'],
            'name': p['name'],
            'category': p['category'],
            'price': float(p['price']),
            'cost': float(p['cost']),
            'margin': float(p['margin']),
            'stock': p['stock'],
            'photo_count': len(p['files']),
            'has_photo': bool(p['files'] and p['files'][0].is_file()),
            'product_url': p['product_url'],
        }
        for p in recommended
    ]

    # Financial aggregates from actual CSV files without inventing records.
    order_ids = set()
    delivered_units = 0
    returned_units = 0
    gross_sales = Decimal(0)
    refunds = Decimal(0)
    product_cost = Decimal(0)
    order_fees = Decimal(0)
    campaigns_units = {}
    campaigns_sales = {}
    heads = 'order_id line_id sku delivered_units returned_units sales refunds net_product_cost fees campaign'.split()
    for r in records(base / 'orders.csv', heads):
        oid = r['order_id']
        order_ids.add(oid)
        q, ret = int(r['delivered_units']), int(r['returned_units'])
        s = Decimal(r['sales'])
        f = Decimal(r['refunds'])
        c = Decimal(r['net_product_cost'])
        g = Decimal(r['fees'])
        delivered_units += q
        returned_units += ret
        gross_sales += s
        refunds += f
        product_cost += c
        order_fees += g
        camp = r['campaign'] or 'Unattributed'
        campaigns_units[camp] = campaigns_units.get(camp, 0) + (q - ret)
        campaigns_sales[camp] = float(Decimal(str(campaigns_sales.get(camp, 0))) + (s - f))

    ad_spend = Decimal(0)
    other_expenses = Decimal(0)
    expense_by_type = {}
    for row in records(base / 'expenses.csv', ['reference', 'type', 'amount']):
        amt = Decimal(row['amount'])
        etype = (row['type'] or 'Other').strip()
        expense_by_type[etype] = float(Decimal(str(expense_by_type.get(etype, 0))) + amt)
        if 'advert' in etype.lower() or 'ad' == etype.lower():
            ad_spend += amt
        else:
            other_expenses += amt

    net_sales = gross_sales - refunds
    total_expenses = product_cost + order_fees + ad_spend + other_expenses
    operating_profit = net_sales - total_expenses
    margin_pct = round((float(operating_profit) / float(net_sales) * 100.0), 1) if net_sales > 0 else 0.0

    with closing(db()) as c:
        jobs = [dict(j) for j in c.execute('SELECT id,sku,name,status,due,note,created FROM jobs WHERE mode=? ORDER BY created DESC', (mode,))]

    reconcile_social_approvals(orch.storage, mode)
    social_posts = orch.storage.list_social_posts(mode=mode, limit=100)
    video_prods = orch.storage.list_video_productions(mode=mode, limit=25)
    runs = orch.storage.list_runs(mode=mode, limit=12)

    pending_social = sum(1 for p in social_posts if p['status'] in ('Draft', 'Awaiting Approval'))
    pending_qa = sum(1 for v in video_prods if v['qa_status'] == 'passed' and v['human_review_status'] == 'pending')
    pending_approvals = pending_social + pending_qa

    scheduled_promotions = (
        sum(1 for j in jobs if j['status'] == 'Scheduled')
        + sum(1 for p in social_posts if p['status'] == 'Scheduled')
    )
    published_promotions = (
        sum(1 for j in jobs if j['status'] in ('Published', 'Simulated'))
        + sum(1 for p in social_posts if p['status'] == 'Published')
    )

    upcoming = []
    for p in social_posts:
        if p['status'] in ('Scheduled', 'Approved'):
            upcoming.append({
                'id': p['id'],
                'title': f"{p['platform'].replace('_', ' ').title()} · {p['sku']}",
                'sku': p['sku'],
                'status': p['status'],
                'due': p['scheduled_at'] or p['approved_at'],
                'source': 'social',
            })
    for j in jobs:
        if j['status'] in ('Scheduled', 'Draft'):
            upcoming.append({
                'id': j['id'],
                'title': j['name'],
                'sku': j['sku'],
                'status': j['status'],
                'due': j['due'] or j['created'],
                'source': 'reel_queue',
            })
    upcoming.sort(key=lambda x: x['due'] or '', reverse=False)

    # Integration health from real environment/config state.
    ollama_cfg = orch.get_config()
    ff_ok = bool(shutil.which('ffmpeg'))
    if not ff_ok:
        try:
            import creative
            ff_ok = Path(creative.ffmpeg()).is_file()
        except Exception:
            ff_ok = False
    staged = orch.storage.get_staged_catalog_batch(mode)
    social_settings = orch.storage.get_social_settings()

    return {
        'mode': mode,
        'range': date_range,
        'app_version': '2.0.0',
        'kpis': {
            'total_products': len(products),
            'ready_products': len(ready_products),
            'ready_pct': round((len(ready_products) / len(products) * 100.0), 1) if products else 0.0,
            'attention_products': len(attention_products),
            'pending_approvals': pending_approvals,
            'scheduled_promotions': scheduled_promotions,
            'published_promotions': published_promotions,
            'orders': len(order_ids),
            'delivered_units': delivered_units,
            'returned_units': returned_units,
            'gross_sales': float(gross_sales),
            'refunds': float(refunds),
            'net_sales': float(net_sales),
            'total_expenses': float(total_expenses),
            'operating_profit': float(operating_profit),
            'profit_margin_pct': margin_pct,
        },
        'financial_breakdown': {
            'gross_sales': float(gross_sales),
            'refunds': float(refunds),
            'net_sales': float(net_sales),
            'product_cost': float(product_cost),
            'order_fees': float(order_fees),
            'ad_spend': float(ad_spend),
            'other_expenses': float(other_expenses),
            'operating_profit': float(operating_profit),
            'expense_by_type': expense_by_type,
        },
        'campaigns': [
            {
                'campaign': k,
                'units': campaigns_units[k],
                'net_sales': campaigns_sales.get(k, 0.0),
            }
            for k in campaigns_units
        ],
        'recommended_products': recommended_list,
        'attention_breakdown': [
            {'issue': k, 'count': v}
            for k, v in sorted(issue_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
        'upcoming_promotions': upcoming[:6],
        'agent_activity': runs[:6],
        'active_agent_count': sum(1 for r in runs if r['status'] in ('queued', 'running')),
        'integration_health': {
            'ollama': {'label': 'Ollama AI', 'status': 'configured', 'detail': f"{ollama_cfg['default_model']} @ {ollama_cfg['ollama_base_url']}"},
            'ffmpeg': {'label': 'FFmpeg Engine', 'status': 'ready' if ff_ok else 'missing', 'detail': '1080×1920 H.264/AAC ready' if ff_ok else 'FFmpeg not found'},
            'factori_bridge': {'label': 'Factori Bridge', 'status': 'ready' if staged else 'standby', 'detail': f"{staged['item_count']} staged" if staged else 'Extension bridge ready'},
            'instagram': {'label': 'Instagram API', 'status': 'connected' if enabled() else 'disconnected', 'detail': 'Live configured' if enabled() else 'Manual / local mode'},
            'factori_api': {'label': 'Factori Official API', 'status': 'pending', 'detail': 'Pending official API access'},
            'global_publishing': {'label': 'Publishing Gate', 'status': 'paused' if social_settings['global_publishing_stopped'] else 'manual', 'detail': 'Global Stop Active' if social_settings['global_publishing_stopped'] else 'Human approval required'},
        },
    }


def build_system_integrations_payload(orch, *, mode: str):
    """Build real integration, Factori checklist, config, and SQLite database metadata."""
    import shutil
    from engine import config as studio_config

    cfg = studio_config()
    ollama_cfg = orch.get_config()
    ff_path = shutil.which('ffmpeg') or ''
    ffprobe_path = shutil.which('ffprobe') or ''
    if not ff_path:
        try:
            import creative
            ff_path = str(creative.ffmpeg())
            ffprobe_path = str(creative.ffprobe())
        except Exception:
            ff_path = ''
    ff_ready = bool(ff_path and Path(ff_path).is_file())

    ext_version = 'Not found'
    manifest_file = ROOT / 'factori-bridge' / 'manifest.json'
    if manifest_file.is_file():
        try:
            ext_version = json.loads(manifest_file.read_text(encoding='utf-8')).get('version', 'Unknown')
        except Exception:
            ext_version = 'Unreadable'
    staged = orch.storage.get_staged_catalog_batch(mode)

    checklist = []
    checklist_file = ROOT / 'FACTORI_API_CHECKLIST.md'
    if checklist_file.is_file():
        for raw_line in checklist_file.read_text(encoding='utf-8').splitlines():
            stripped = raw_line.strip()
            if stripped.startswith('- [ ] '):
                checklist.append({'text': stripped[6:].strip(), 'checked': False})
            elif stripped.startswith('- [x] ') or stripped.startswith('- [X] '):
                checklist.append({'text': stripped[6:].strip(), 'checked': True})

    cred_status = SecureCredentialStore().list_status()
    social_settings = orch.storage.get_social_settings()

    db_file = ROOT / 'data' / 'studio.sqlite'
    db_size_bytes = db_file.stat().st_size if db_file.is_file() else 0
    table_counts = {}
    with closing(db()) as c:
        for tbl in ('jobs', 'agent_runs', 'catalog_sync_items', 'marketing_campaigns', 'video_productions', 'qa_reports', 'social_posts'):
            try:
                table_counts[tbl] = c.execute(f'SELECT COUNT(*) FROM {tbl}').fetchone()[0]
            except Exception:
                table_counts[tbl] = 0

    return {
        'mode': mode,
        'integrations': {
            'ollama': {
                'name': 'Ollama Local AI',
                'status': 'configured',
                'base_url': ollama_cfg.get('ollama_base_url'),
                'model': ollama_cfg.get('default_model'),
                'execution_mode': ollama_cfg.get('execution_mode', 'manual'),
            },
            'ffmpeg': {
                'name': 'FFmpeg Video & Audio Engine',
                'status': 'ready' if ff_ready else 'missing',
                'ffmpeg_path': ff_path,
                'ffprobe_path': ffprobe_path,
            },
            'factori_bridge': {
                'name': 'Factori Bridge Chrome Extension',
                'status': 'ready' if staged else 'standby',
                'version': ext_version,
                'staged_items': staged['item_count'] if staged else 0,
                'staged_pages': staged['pages_count'] if staged else 0,
            },
            'instagram_facebook': {
                'name': 'Meta Instagram & Facebook Official API',
                'status': 'connected' if enabled() else 'not_configured',
                'credentials': {k: v for k, v in cred_status.items() if k.startswith('meta_')},
            },
            'youtube': {
                'name': 'YouTube Data API v3 (Shorts)',
                'status': 'connected' if cred_status.get('youtube_oauth_token', {}).get('configured') else 'not_configured',
                'credentials': {k: v for k, v in cred_status.items() if k.startswith('youtube_')},
            },
            'factori_official_api': {
                'name': 'Factori Official API',
                'status': 'pending',
                'checklist': checklist,
            },
        },
        'safe_config': {
            'weekly_slots': cfg.get('weekly_slots', []),
            'minimum_stock': cfg.get('minimum_stock', 0),
            'maximum_catalogue_age_hours': cfg.get('maximum_catalogue_age_hours', 0),
            'ollama_base_url': ollama_cfg.get('ollama_base_url'),
            'default_model': ollama_cfg.get('default_model'),
            'execution_mode': ollama_cfg.get('execution_mode', 'manual'),
            'max_retries': ollama_cfg.get('max_retries', 2),
            'request_timeout_seconds': ollama_cfg.get('request_timeout_seconds', 120),
            'global_publishing_stopped': social_settings.get('global_publishing_stopped', False),
            'global_stop_reason': social_settings.get('global_stop_reason', ''),
        },
        'database': {
            'path': str(db_file),
            'size_bytes': db_size_bytes,
            'table_counts': table_counts,
        },
    }


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
        orch = get_orchestrator()
        with ThreadingHTTPServer(('127.0.0.1',args.port),Handler) as server:
            server.orchestrator = orch
            try:
                url=f'http://127.0.0.1:{server.server_port}'
                print('ProdXMart Studio:',url,flush=True)
                threading.Thread(target=worker,daemon=True).start()
                if not args.no_browser:webbrowser.open(url)
                server.serve_forever()
            except KeyboardInterrupt:
                print('\nProdXMart Studio stopped.',flush=True)
            finally:
                orch.stop()
