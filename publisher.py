"""Instagram API with Facebook Login for Business, using local-file uploads."""
import json
import os
import re
import time
from contextlib import closing
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen, HTTPRedirectHandler, build_opener
from engine import LOCK, db, now, reason, video_path
from agents.qa_agent import verify_job_for_publishing


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def send(url, token, fields=None, file=None):
    headers={'Authorization':'Bearer '+token}
    body=None
    if file is not None:
        body=file.read_bytes()
        headers={'Authorization':'OAuth '+token,'file_size':str(len(body)),'offset':'0','Content-Type':'application/octet-stream'}
    elif fields is not None:
        body=urlencode(fields).encode()
    req=Request(url,data=body,headers=headers)
    try:
        with build_opener(NoRedirect).open(req,timeout=60) as res:
            return json.load(res)
    except HTTPError as e:
        # Avoid leaking URLs, secrets or response data to the browser.
        raise ValueError(f'Meta returned HTTP {e.code}. Check app version, token, permissions and publishing status.') from None


def enabled():
    return os.getenv('PRODX_LIVE')=='1' and bool(os.getenv('PRODX_IG_TOKEN')) and bool(re.fullmatch(r'[0-9]+',os.getenv('PRODX_IG_ID',''))) and bool(re.fullmatch(r'v\d+\.\d+',os.getenv('PRODX_GRAPH_VERSION','')))


def update(c,jid,state,note='',container=None,media=None):
    c.execute('UPDATE jobs SET status=?,note=?,container=COALESCE(?,container),published_id=COALESCE(?,published_id) WHERE id=?',
              (state,note,container,media,jid))
    c.commit()


def run_due():
    from agents.storage import AgentStorage
    storage = AgentStorage()
    if storage.is_global_publishing_stopped():
        return
    with LOCK,closing(db()) as c:
        due=c.execute("SELECT * FROM jobs WHERE status='Scheduled' AND due<=? ORDER BY due",(now().isoformat(),)).fetchall()
        for j in due:
            if j['mode']=='store' and not enabled():continue
            claimed=c.execute("UPDATE jobs SET status='Processing' WHERE id=? AND status='Scheduled'",(j['id'],)).rowcount
            c.commit()
            if not claimed:continue
            publishing=False
            try:
                why=reason(j['mode'],j)
                if not why:
                    why=verify_job_for_publishing(c, j)
                if why:
                    update(c,j['id'],'Held',why);continue
                if j['mode']=='demo':
                    update(c,j['id'],'Simulated','Demo schedule completed locally.');continue
                token=os.environ['PRODX_IG_TOKEN']
                base='https://graph.facebook.com/'+os.environ['PRODX_GRAPH_VERSION']+'/'
                user=base+os.environ['PRODX_IG_ID']
                res=send(user+'/media',token,{'media_type':'REELS','upload_type':'resumable','caption':j['caption']})
                container=str(res['id']);uri=res['uri']
                if not container.isdecimal() or urlsplit(uri).netloc!='rupload.facebook.com' or urlsplit(uri).scheme!='https':
                    raise ValueError('Unexpected upload destination from Meta.')
                update(c,j['id'],'Processing',container=container)
                uploaded=send(uri,token,file=video_path(c, j['id']))
                if uploaded.get('success') is not True:raise ValueError('Meta did not confirm the upload.')
                for _ in range(12):
                    status=send(base+container+'?fields=status_code',token).get('status_code')
                    if status=='FINISHED':break
                    if status in ('ERROR','EXPIRED'):raise ValueError('Meta container '+status.lower())
                    time.sleep(10)
                else:raise ValueError('Video processing timed out. Inspect the container before retrying.')
                why=reason('store',j)
                if not why:
                    why=verify_job_for_publishing(c, j)
                if why:
                    update(c,j['id'],'Held',why);continue
                publishing=True
                update(c,j['id'],'Publishing','Check Instagram before retrying any interrupted publish.')
                result=send(user+'/media_publish',token,{'creation_id':container})
                update(c,j['id'],'Published',media=str(result['id']))
            except Exception as e:
                update(c,j['id'],'Check Instagram' if publishing else 'Held',str(e)[:220])
