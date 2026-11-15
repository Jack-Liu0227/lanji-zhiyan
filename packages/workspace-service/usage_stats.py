"""Persistent request metering. No prompt contents or credentials are stored."""
import json
from contextlib import closing
import service_settings
import logging
import math
from pathlib import Path
import sqlite3
import uuid
import model_pricing
from datetime import datetime, timedelta, timezone


def normalize(raw, api):
    cached = raw.get('cache_read_input_tokens', raw.get('prompt_cache_hit_tokens', raw.get('cachedContentTokenCount',
        (raw.get('input_tokens_details') or raw.get('prompt_tokens_details') or {}).get('cached_tokens'))))
    written = raw.get('cache_creation_input_tokens', (raw.get('prompt_tokens_details') or {}).get('cache_creation_input_tokens'))
    prompt = int(raw.get('input_tokens',raw.get('prompt_tokens',raw.get('promptTokenCount',0))) or 0)
    reasoning = (raw.get('output_tokens_details') or raw.get('completion_tokens_details') or {}).get('reasoning_tokens',raw.get('thoughtsTokenCount'))
    output = int(raw.get('output_tokens',raw.get('completion_tokens',raw.get('candidatesTokenCount',0))) or 0)
    if api == 'anthropic-messages':prompt += int(cached or 0) + int(written or 0)
    if api == 'google-generative-ai':output += int(reasoning or 0)
    return {'prompt':prompt,'completion':output,'total':prompt+output,'cached':cached,
            'cache_write':written,'reasoning':reasoning,
            'exact':any(raw.get(key) is not None for key in ('input_tokens','prompt_tokens','promptTokenCount')) and any(raw.get(key) is not None for key in ('output_tokens','completion_tokens','candidatesTokenCount'))}


def context_sizes(messages, tools):
    counts={'system':0,'conversation':0,'tool_results':0,'tools':len(json.dumps(tools,ensure_ascii=False)) if tools else 0,'images':0}
    for message in messages:
        key='system' if message.get('role')=='system' else 'tool_results' if message.get('role')=='tool' else 'conversation'
        value=message.get('content') or ''
        if isinstance(value,list):
            counts['images']+=sum(part.get('type')=='image_url' for part in value)
            value='\n'.join(part.get('text','') for part in value)
        counts[key]+=len(str(value))
    return counts


def profile(db, value):
    if not value:return value
    context=dict(getattr(db,'usage_context',{}) or {})
    run=getattr(db,'asset_run',None) or {}
    context.setdefault('project_id',run.get('project_id',''))
    context.setdefault('run_id',run.get('id',''))
    return {**value,'_usage_context':{**context,'db_path':str(db.path)}}


def record(profile, messages, tools, duration_ms, usage, status, attempt, first_token_ms=None):
    context=profile.get('_usage_context') or {};path=context.get('db_path')
    if not path or path==':memory:' or not Path(path).is_file():return
    exact=usage.get('exact',False)
    values=[uuid.uuid4().hex,datetime.now(timezone.utc).isoformat(),
            *[str(context.get(key) or '') for key in ('project_id','owner_id','assistant_id','run_id')],
            str(profile.get('id') or ''),model_pricing.provider_for(profile),
            str(profile.get('model') or ''),status,attempt,
            usage.get('prompt') if exact else None,usage.get('completion') if exact else None,
            usage.get('cached'),usage.get('reasoning'),usage.get('cache_write'),duration_ms,first_token_ms,
            json.dumps(context_sizes(messages,tools))]
    try:
        with closing(sqlite3.connect(path,timeout=5)) as connection:
            connection.execute('INSERT INTO model_requests VALUES ('+','.join('?' for _ in values)+')',values)
            connection.commit()
    except sqlite3.Error:
        logging.getLogger(__name__).exception('Unable to persist model metering')


def report(db, query):
    def param(key,default=''):return str((query.get(key) or [default])[0])
    days=max(1,min(366,int(param('days','30'))))
    now=datetime.now().astimezone()
    start=now-timedelta(days=1) if days==1 else (now-timedelta(days=days-1)).replace(hour=0,minute=0,second=0,microsecond=0)
    clauses=['created_at>=?'];args=[start.astimezone(timezone.utc).isoformat()]
    for key in ('model','provider','project_id','status'):
        if param(key):clauses.append(key+'=?');args.append(param(key))
    rows=[dict(row) for row in db.db.execute('SELECT * FROM model_requests WHERE '+' AND '.join(clauses)+' ORDER BY created_at DESC',args)]
    names={row['id']:row['title'] for row in db.db.execute('SELECT id,title FROM chats')}
    projects={row['id']:row['title'] for row in db.db.execute('SELECT id,title FROM research_tasks')}
    if param('q').strip():
        terms=param('q').casefold().split()
        rows=[row for row in rows if all(term in ' '.join([row['model'],row['provider'],names.get(row['owner_id'],''),projects.get(row['project_id'],''),row['status']]).casefold() for term in terms)]
    prices=service_settings.read(db,'usage_prices')
    profiles={row['id']:dict(row) for row in db.db.execute('SELECT id,name,model,base_url,api FROM model_profiles')}
    def resolve(identifier,provider,model):
        manual=prices.get(identifier)
        if manual and (not manual.get('model') or (manual['model']==model and manual.get('provider')==provider)):
            return {**manual,'currency':manual.get('currency','USD'),'origin':'manual'}
        price=model_pricing.lookup(provider,model)
        return {**price,'origin':'catalog'} if price else None
    groups={};model_groups={}
    daily={(start.date()+timedelta(days=offset)).isoformat():{'tokens':0,'requests':0,'metered':0} for offset in range((now.date()-start.date()).days+1)}
    totals={'requests':len(rows),'failed':0,'input':0,'output':0,'cached':0,'reasoning':0,'metered':0,'cached_reported':0,'reasoning_reported':0,'priced':0,'cost':0,'costs':{},'unpriced':{}}
    for row in rows:
        row['context']=json.loads(row.pop('context_json'));row['title']=names.get(row['owner_id'],row['owner_id'] or '独立调用')
        row['project_title']=projects.get(row['project_id'],'未关联项目')
        profile=profiles.get(row['profile_id'],{})
        # Old records stored a provider ID or wire; only use a still-matching profile.
        provider=row['provider']
        if provider not in {*model_pricing.SOURCES,'custom'} and profile.get('model')==row['model']:
            provider=model_pricing.provider_for(profile)
        price=resolve(row['profile_id'],provider,row['model'])
        row['price_origin']=(price or {}).get('origin','unknown')
        row.update(model_pricing.estimate(row,price))
        if row['cost'] is not None:
            cost=totals['costs'].setdefault(row['currency'],{'min':0,'max':0})
            cost['min']+=row['cost_min'];cost['max']+=row['cost'];totals['priced']+=1
            if row['currency']=='USD':totals['cost']+=row['cost']
        else:totals['unpriced'][row['cost_reason']]=totals['unpriced'].get(row['cost_reason'],0)+1
        metered=row['prompt_tokens'] is not None and row['output_tokens'] is not None
        totals['failed']+=row['status']!='ok';totals['metered']+=metered
        totals['cached_reported']+=row['cached_tokens'] is not None
        totals['reasoning_reported']+=row['reasoning_tokens'] is not None
        for key,field in [('input','prompt_tokens'),('output','output_tokens'),('cached','cached_tokens'),('reasoning','reasoning_tokens')]:totals[key]+=row[field] or 0
        day=datetime.fromisoformat(row['created_at']).astimezone().strftime('%Y-%m-%d')
        token_count=(row['prompt_tokens'] or 0)+(row['output_tokens'] or 0)
        bucket=daily.setdefault(day,{'tokens':0,'requests':0,'metered':0})
        bucket['tokens']+=token_count;bucket['requests']+=1;bucket['metered']+=metered
        key=row['owner_id'] or row['run_id'] or row['id']
        group=groups.setdefault(key,{'id':key,'title':row['title'],'project_title':row['project_title'],'requests':0,'tokens':0,'cached':0,'cached_reported':0,'metered':0,'duration_ms':0,'last_at':row['created_at']})
        group['requests']+=1;group['tokens']+=(row['prompt_tokens'] or 0)+(row['output_tokens'] or 0);group['cached']+=row['cached_tokens'] or 0;group['duration_ms']+=row['duration_ms']
        group['metered']+=metered
        group['cached_reported']+=row['cached_tokens'] is not None
        group=model_groups.setdefault((provider,row['model']),{'provider':provider,'model':row['model'],'requests':0,'tokens':0,'metered':0})
        group['requests']+=1;group['tokens']+=token_count;group['metered']+=metered
    limit=100;pages=max(1,math.ceil(len(rows)/limit));page=min(pages,max(1,int(param('page','1'))))
    profile_prices=[]
    for item in profiles.values():
        provider=model_pricing.provider_for(item)
        price=resolve(item['id'],provider,item['model'])
        profile_prices.append({'id':item['id'],'name':item['name'],'model':item['model'],'provider':provider,
                               'price':model_pricing.effective(price) if price else None})
    return {'totals':totals,'daily':[{'date':date,**value} for date,value in sorted(daily.items())],
            'requests':rows[(page-1)*limit:page*limit],'page':page,'pages':pages,
            'sessions':sorted(groups.values(),key=lambda group:group['tokens'],reverse=True)[:200],
            'models':[dict(row) for row in db.db.execute('SELECT DISTINCT model,provider FROM model_requests ORDER BY model')],
            'prices':prices,'profile_prices':profile_prices,'price_catalog':model_pricing.MODEL_PRICES,
            'price_checked_at':model_pricing.CHECKED_AT,'price_sources':model_pricing.SOURCES,
            'model_totals':sorted(model_groups.values(),key=lambda item:item['tokens'],reverse=True),
            'currency':'USD','days':days}


def route(method,path,body,query,db):
    if not path.startswith('/api/usage'):return None
    if method=='GET' and path=='/api/usage':return report(db,query)
    if method=='POST' and path=='/api/usage/prices':
        profile_id=str(body.get('profile_id') or '');profile=db.model(profile_id)
        prices=service_settings.read(db,'usage_prices')
        if body.get('reset'):
            prices.pop(profile_id,None)
        else:
            currency=body.get('currency','USD')
            if currency not in ('USD','CNY'):raise ValueError('币种仅支持 USD 或 CNY')
            values={key:float(body[key]) if body.get(key) not in ('',None) else None for key in ('input','output','cached','cache_write')}
            if values['input'] is None or values['output'] is None or any(value is not None and (not math.isfinite(value) or value<0) for value in values.values()):raise ValueError('输入和输出价格必填，单价须为有限的非负数')
            prices[profile_id]={**values,'currency':currency,'model':profile['model'],'provider':model_pricing.provider_for(profile)}
        service_settings.write(db,'usage_prices',prices)
        return {'ok':True}
    raise ValueError('不支持的用量统计操作')
