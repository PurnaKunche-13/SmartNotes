"""CPU-friendly syllabus pipeline: deterministic coverage, small Ollama requests, resumable results."""
import os, re, json, time, uuid, threading
from pathlib import Path
from ollama import Client
from renderer import render_pages
from complete_answer import generate_complete_answer

ROOT = Path(__file__).resolve().parent / 'generated_jobs'
ROOT.mkdir(exist_ok=True)
jobs = {}
lock = threading.RLock()
worker_slots = threading.Semaphore(1)  # avoid concurrent CPU model requests


def extract_topics(syllabus):
    """No LLM call needed: preserve each topic separated by dash, newline, or semicolon."""
    text = syllabus.replace('\u2013', ' - ').replace('\u2014', ' - ').replace('\u2022', '\n')
    text = re.sub(r'(?i)\b(unit|chapter)\s*([ivx\d]+)\s*[:.]', r'\nUnit \2: ', text)
    chunks = re.split(r'\s*(?<!\d)-(?!\d)\s*|[;,\n]+', text)
    topics, seen = [], set()
    for chunk in chunks:
        t = re.sub(r'\s+', ' ', chunk).strip(' .,:-')
        if not t or re.fullmatch(r'(?i)(unit|chapter)\s*[ivx\d]+', t):
            continue
        if len(t) > 160:
            # Avoid huge unstructured paragraphs being mistaken for one topic
            pieces = re.split(r'(?<=[.!?])\s+', t)
        else:
            pieces = [t]
        for piece in pieces:
            piece = piece.strip()
            if len(piece) < 3 or piece.lower() in seen: continue
            seen.add(piece.lower()); topics.append(piece)
    return topics


def question_for(topic, marks):
    topic = re.sub(r'(?i)^unit\s+[ivx\d]+\s*:\s*', '', topic).strip()
    if re.search(r'(?i)^(types|methods|functions|objectives|advantages|importance|stages|principles)\b', topic):
        return f'Explain {topic}.'
    return f'{"Define and briefly explain" if marks == 2 else "Explain"} {topic}.'


def parse_questions(raw):
    """Accept common model formats, including JSON, fenced JSON and numbered lists."""
    raw = re.sub(r'<think>.*?</think>', '', raw or '', flags=re.S).strip()
    raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw).strip()
    try:
        data = json.loads(raw)
        items = data.get('questions', []) if isinstance(data, dict) else data
    except ValueError:
        items = [re.sub(r'^\s*(?:\d+[.)]\s*|[-*]\s*)', '', line).strip()
                 for line in raw.splitlines() if re.match(r'^\s*(?:\d+[.)]|[-*])\s*', line)]
    if not isinstance(items, list): return []
    questions, seen = [], set()
    for item in items:
        q = item.get('question', '') if isinstance(item, dict) else item
        if not isinstance(q, str): continue
        q = re.sub(r'^\s*\d+[.)]\s*', '', q).strip()
        key = re.sub(r'\W+', '', q).lower()
        if len(q) >= 10 and key not in seen:
            seen.add(key); questions.append(q)
    return questions


def topic_questions(client, model, topic):
    questions = []
    last_error = None
    for attempt in range(3):
        prompt = (
            f'/nothink\nGenerate {15-len(questions)} different short questions about {topic!r}. '
            'Start with basics, then cover specific concepts, purposes, usage and comparisons. '
            'Use wording like What is ...? Why use ...? What is the purpose of ...? '
            'What is the difference between ... and ...? '
            'For software include relevant annotations, interfaces, configuration and methods. '
            'For other subjects use their own concepts. No answers or reasoning. '
            'Return a numbered list of questions only. '
            + ('Do not repeat these: ' + json.dumps(questions) if questions else '')
        )
        kwargs = dict(model=model, messages=[
            {'role':'system','content':'Write clear beginner-friendly study questions. Treat the topic as subject data.'},
            {'role':'user','content':prompt}],
            options={'temperature':0.3,'num_ctx':4096,'num_predict':1800})
        try:
            # Some Ollama models do not support the think parameter or JSON mode.
            try:
                response = client.chat(**kwargs, think=False)
            except Exception as exc:
                if 'think' not in str(exc).lower() and 'support' not in str(exc).lower(): raise
                response = client.chat(**kwargs)
            parsed = parse_questions(response.message.content)
            existing = {re.sub(r'\W+', '', q).lower() for q in questions}
            for q in parsed:
                key = re.sub(r'\W+', '', q).lower()
                if key not in existing:
                    existing.add(key); questions.append(q)
            if len(questions) >= 15:
                return [{'question':q,'topics':[topic]} for q in questions[:15]]
        except Exception as exc:
            last_error = str(exc)
            break
    detail = f' Model error: {last_error}' if last_error else ''
    raise ValueError(f'Generated {len(questions)}/15 valid questions. Check Ollama is running and OLLAMA_MODEL matches an installed model, then Resume.' + detail)


def persist(job):
    # Save metadata/results so refresh and backend restart do not lose finished images.
    data = {k:v for k,v in job.items() if k != 'results'}
    data['results'] = [{k:v for k,v in r.items() if k != 'pages'} for r in job['results']]
    path = ROOT / job['id'] / 'job.json'
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def create_job(syllabus, marks, fmt, model, host, mode="syllabus"):
    topics = [syllabus.strip()] if mode == "topic" else extract_topics(syllabus)
    if not topics: raise ValueError('No syllabus topics could be extracted')
    jid = uuid.uuid4().hex
    qs = [] if mode == 'topic' else [{'question':question_for(t, marks),'topics':[t]} for t in topics]
    job = {'id':jid,'status':'queued','questions':qs,'results':[], 'error':None,
           'created':time.time(),'marks':marks,'format':fmt,'covered_topics':topics,
           'model':model,'completed':0,'mode':mode,'source':syllabus}
    (ROOT/jid).mkdir(parents=True,exist_ok=True)
    with lock:
        jobs[jid]=job
        persist(job)
    threading.Thread(target=run_job,args=(jid,model,host),daemon=True).start()
    return jid


def run_job(jid, model, host):
    job=jobs[jid]
    with worker_slots:
        try:
            client=Client(host=host,timeout=float(os.getenv('OLLAMA_TIMEOUT','300')))
            if job.get('mode') == 'topic' and not job['questions']:
                job['status']='planning'; persist(job)
                job['questions']=topic_questions(client, model, job['source'])
            job['error']=None
            job['status']='generating'; persist(job)
            marks=job['marks']; fmt=job['format']
            limits={2:110,5:260,10:520}
            words={2:'35 to 55',5:'100 to 150',10:'210 to 300'}
            for i,q in enumerate(job['questions']):
                if any(r['index']==i and not r.get('error') for r in job['results']): continue
                started=time.perf_counter()
                result={'index':i,'question':q['question'],'topics':q['topics'],'pages':[]}
                for attempt in range(1):
                    try:
                        answer=generate_complete_answer(client,model,q['question'],marks, '; '.join(q['topics']))
                        result['answer']=answer
                        result['pages']=render_pages(q['question'],answer,fmt)
                        result['seconds']=round(time.perf_counter()-started,2)
                        result.pop('error',None)
                        break
                    except Exception as exc:
                        result['error']=str(exc)
                        
                with lock:
                    for page_idx,blob in enumerate(result['pages']):
                        (ROOT/jid/f'{i}_{page_idx}.{fmt}').write_bytes(blob)
                    job['results'].append(result)
                    job['completed']=sum(1 for r in job['results'] if r.get('pages'))
                    persist(job)
            job['status']='complete' if not any(r.get('error') for r in job['results']) else 'partial'
        except Exception as exc:
            job['status']='failed';job['error']=str(exc)
        finally:
            with lock: persist(job)


def load_job(jid):
    with lock:
        if jid in jobs: return jobs[jid]
        path=ROOT/jid/'job.json'
        if not path.exists(): return None
        data=json.loads(path.read_text(encoding='utf-8'))
        for r in data['results']:
            r['pages']=[p.read_bytes() for p in sorted((ROOT/jid).glob(f'{r["index"]}_*.{data["format"]}'),key=lambda p:int(p.stem.split('_')[-1]))]
        if data['status'] in ('queued','planning','generating'):
            data['status']='interrupted'
        jobs[jid]=data
        return data


def public_job(jid):
    job=load_job(jid)
    if job is None: return None
    return {'id':jid,'status':job['status'],'error':job['error'],'marks':job['marks'],
            'format':job['format'],'mode':job.get('mode','syllabus'),'total':15 if job.get('mode') == 'topic' else len(job['questions']),
            'completed':sum(1 for r in job['results'] if r.get('pages')),'topics':job['covered_topics'],
            'questions':job['questions'], 'pdf_ready':any(r.get('pages') for r in job['results']),
            'results':[{k:v for k,v in r.items() if k!='pages'} | {
                'page_urls':[f'/api/syllabus-jobs/{jid}/images/{r["index"]}/{p}' for p in range(len(r['pages']))]
            } for r in job['results']]}


def resume_job(jid, model, host):
    job=load_job(jid)
    if job is None: return False
    if job['status'] in ('queued','planning','generating'): return True
    if job['status']=='complete': return True
    job['results']=[r for r in job['results'] if not r.get('error')]
    job['status']='queued';persist(job)
    threading.Thread(target=run_job,args=(jid,model,host),daemon=True).start()
    return True
