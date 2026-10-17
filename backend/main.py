from pathlib import Path
import os
import uuid
import time
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field
from ollama import Client
import fitz
from renderer import render_pages
from complete_answer import generate_complete_answer

app = FastAPI(title='AI Student Handwritten Notes')
# Allow Vite on any local development port (e.g. 5173, 5174, 5175).
# For deployment, replace this regex with your actual frontend domain.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in os.getenv('FRONTEND_ORIGINS', '').split(',') if origin.strip()],
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
client = Client(host=os.getenv('OLLAMA_HOST', 'http://localhost:11434'), timeout=240)
MODEL = os.getenv('OLLAMA_MODEL', 'qwen3:4b')
# Temporary in-memory images: restart clears these; not production storage.
images = {}
MAX_PDF_BYTES = 10 * 1024 * 1024

class GenerateRequest(BaseModel):
    question: str = Field(min_length=3, max_length=4000)
    syllabus: str = Field(default='', max_length=18000)
    marks: int = Field(default=10, ge=2, le=20)
    format: str = Field(default='webp', pattern='^(webp|avif)$')

@app.get('/api/health')
def health():
    try:
        models = client.list()
        return {'status': 'ok', 'model': MODEL, 'ollama': 'connected'}
    except Exception as exc:
        return {'status': 'degraded', 'model': MODEL, 'ollama': 'unavailable', 'detail': str(exc)}

@app.post('/api/upload-syllabus')
async def upload_syllabus(file: UploadFile = File(...)):
    if not (file.filename or '').lower().endswith('.pdf'):
        raise HTTPException(400, 'Upload a PDF file')
    data = await file.read(MAX_PDF_BYTES + 1)
    if len(data) > MAX_PDF_BYTES: raise HTTPException(413, 'PDF exceeds 10 MB limit')
    try:
        with fitz.open(stream=data, filetype='pdf') as doc:
            if len(doc) > 100: raise HTTPException(400, 'PDF exceeds 100 pages')
            text = '\n'.join(p.get_text() for p in doc)
    except HTTPException: raise
    except Exception: raise HTTPException(400, 'Could not read PDF')
    if not text.strip():
        raise HTTPException(422, 'Scanned/image-only PDF: text extraction is unavailable; paste the syllabus text instead')
    return {'filename': file.filename, 'syllabus': text[:18000], 'characters': min(len(text),18000)}

@app.post('/api/generate')
def generate(req: GenerateRequest):
    prompt = f'''Write a clear, accurate university examination answer in natural simple English.
Question: {req.question}
Marks: {req.marks}
Syllabus context (use only if relevant): {req.syllabus[:14000] or 'Not supplied'}
Instructions: Give sufficient depth for the marks. Use a definition, numbered subheadings,
short explanations, relevant examples where useful, and a concise conclusion.
Avoid fabricated facts. Output only the final answer, no reasoning or preamble.'''
    try:
        started = time.perf_counter()
        answer = generate_complete_answer(client, MODEL, req.question, req.marks, req.syllabus)
        llm_seconds = round(time.perf_counter() - started, 2)
        rendered = render_pages(req.question, answer, req.format)
        render_seconds = round(time.perf_counter() - started - llm_seconds, 2)
    except Exception as exc:
        raise HTTPException(503, f'Generation failed: {exc}')
    doc_id = uuid.uuid4().hex
    # Cap retained generations to reduce memory growth in this development demo.
    if len(images) >= 12: images.pop(next(iter(images)))
    images[doc_id] = (req.format, rendered)
    return {'id':doc_id, 'question':req.question, 'marks':req.marks, 'answer':answer,
            'pages':[f'/api/images/{doc_id}/{i}' for i in range(len(rendered))], 'format':req.format, 'timings':{'llm_seconds':llm_seconds,'render_seconds':render_seconds}}

@app.get('/api/images/{doc_id}/{index}')
def get_image(doc_id: str, index: int):
    if doc_id not in images: raise HTTPException(404, 'Generation not found')
    fmt, pages = images[doc_id]
    if index < 0 or index >= len(pages): raise HTTPException(404, 'Page not found')
    return Response(pages[index], media_type=f'image/{fmt}', headers={'Content-Disposition': f'inline; filename="notes-{index+1}.{fmt}"'})

# Syllabus -> questions -> incremental answer images
from syllabus_pipeline import create_job, jobs, public_job, load_job, resume_job

class SyllabusJobRequest(BaseModel):
    syllabus: str = Field(min_length=15, max_length=18000)
    marks: int = Field(default=10)
    format: str = Field(default='webp', pattern='^(webp|avif)$')

@app.post('/api/syllabus-jobs')
def start_syllabus_job(req: SyllabusJobRequest):
    if req.marks not in (2,5,10):
        raise HTTPException(422, 'Choose 2, 5 or 10 marks')
    if len(req.syllabus.strip()) < 15:
        raise HTTPException(422, 'Enter at least 15 characters of syllabus')
    jid = create_job(req.syllabus, req.marks, req.format, MODEL, os.getenv('OLLAMA_HOST','http://localhost:11434'))
    return {'id':jid,'status':'queued'}

class TopicJobRequest(BaseModel):
    topic: str = Field(min_length=3, max_length=500)
    marks: int = Field(default=10)
    format: str = Field(default='webp', pattern='^(webp|avif)$')

@app.post('/api/topic-jobs')
def start_topic_job(req: TopicJobRequest):
    if len(req.topic.strip()) < 3 or req.marks not in (2,5,10):
        raise HTTPException(422, 'Enter a topic of at least 3 characters and choose 2, 5 or 10 marks')
    jid = create_job(req.topic.strip(), req.marks, req.format, MODEL,
                     os.getenv('OLLAMA_HOST','http://localhost:11434'), mode='topic')
    return {'id':jid,'status':'queued'}

@app.get('/api/syllabus-jobs/{jid}')
def get_syllabus_job(jid: str):
    if load_job(jid) is None: raise HTTPException(404,'Job not found')
    return public_job(jid)

@app.get('/api/syllabus-jobs/{jid}/images/{question_index}/{page_index}')
def syllabus_image(jid: str, question_index: int, page_index: int):
    job=load_job(jid)
    if job is None: raise HTTPException(404,'Job not found')
    found=next((r for r in job['results'] if r['index']==question_index),None)
    if not found or page_index < 0 or page_index >= len(found['pages']): raise HTTPException(404,'Page not found')
    fmt=job['format']
    return Response(found['pages'][page_index],media_type=f'image/{fmt}',headers={'Content-Disposition':f'inline; filename="question-{question_index+1}-page-{page_index+1}.{fmt}"'})

@app.post('/api/syllabus-jobs/{jid}/resume')
def resume_syllabus_job(jid: str):
    if not resume_job(jid, MODEL, os.getenv('OLLAMA_HOST','http://localhost:11434')):
        raise HTTPException(404,'Job not found')
    return {'id':jid,'status':'queued'}

@app.get('/api/syllabus-jobs/{jid}/download')
def download_syllabus_images(jid: str):
    from io import BytesIO
    from zipfile import ZipFile, ZIP_DEFLATED
    job=load_job(jid)
    if job is None: raise HTTPException(404,'Job not found')
    buf=BytesIO()
    with ZipFile(buf,'w',ZIP_DEFLATED) as z:
        for r in job['results']:
            for i, blob in enumerate(r['pages']):
                z.writestr(f'question-{r["index"]+1:03d}-page-{i+1}.{job["format"]}',blob)
    return Response(buf.getvalue(),media_type='application/zip',headers={'Content-Disposition':f'attachment; filename="notes-{jid[:8]}.zip"'})


@app.get('/api/syllabus-jobs/{jid}/pdf')
def download_handwritten_pdf(jid: str):
    """Build a combined PDF from finished handwritten images, in question order."""
    from io import BytesIO
    from PIL import Image as PILImage
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    job = load_job(jid)
    if job is None:
        raise HTTPException(404, 'Job not found')
    completed = sorted((r for r in job['results'] if not r.get('error') and r.get('pages')), key=lambda r:r['index'])
    if not completed:
        raise HTTPException(409, 'No handwritten answers finished yet; wait for the first question')
    out = BytesIO()
    pdf = canvas.Canvas(out, pagesize=A4, pageCompression=1)
    pdf.setTitle('Handwritten Exam Questions and Answers')
    pw, ph = A4
    for result in completed:
        for blob in result['pages']:
            with PILImage.open(BytesIO(blob)) as im:
                image = im.convert('RGB')
                # Use JPEG for compact PDF size; full page keeps the notebook design.
                image_bytes = BytesIO()
                image.save(image_bytes, 'JPEG', quality=86, optimize=True)
                image_bytes.seek(0)
                from reportlab.lib.utils import ImageReader
                pdf.drawImage(ImageReader(image_bytes), 0, 0, width=pw, height=ph)
            pdf.showPage()
    pdf.save()
    suffix = 'complete' if job['status'] == 'complete' else 'in-progress'
    return Response(out.getvalue(), media_type='application/pdf', headers={
        'Content-Disposition': f'attachment; filename="handwritten-answers-{suffix}-{jid[:8]}.pdf"',
        'Cache-Control': 'no-store',
    })
