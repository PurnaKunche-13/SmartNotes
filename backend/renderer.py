"""Fast deterministic ruled-notebook renderer with safe baselines and pagination."""
from io import BytesIO
from pathlib import Path
import re
from PIL import Image, ImageDraw, ImageFont, features

BASE = Path(__file__).resolve().parent
FONT_PATHS = [BASE/'fonts'/'handwriting.ttf', BASE/'fonts'/'handwriting.otf', Path('C:/Windows/Fonts/segoepr.ttf'), Path('C:/Windows/Fonts/comic.ttf'), Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')]

def font(size):
    for p in FONT_PATHS:
        if p.exists():
            try: return ImageFont.truetype(str(p), size)
            except OSError: pass
    return ImageFont.load_default()

def wrap(draw, text, fnt, width):
    result, line = [], ''
    for word in text.split():
        test = (line+' '+word).strip()
        if draw.textlength(test, font=fnt) <= width:
            line = test
        else:
            if line: result.append(line)
            line = word
            while draw.textlength(line, font=fnt) > width and len(line)>1:
                n=max(1,int(len(line)*width/draw.textlength(line,font=fnt)))
                result.append(line[:n]); line=line[n:]
    if line: result.append(line)
    return result

def render_pages(title, answer, fmt='webp'):
    fmt=fmt.lower()
    if fmt not in ('webp','avif'): raise ValueError('Use webp or avif')
    if fmt=='avif' and not features.check('avif'): raise ValueError('AVIF unsupported by installed Pillow; use WebP')
    W,H=1240,1754
    LEFT,RIGHT,TOP,BOTTOM=130,78,105,90
    LINE=60  # enough room for handwriting ascenders and descenders
    INK=(28,65,145)
    regular, heading = font(31),font(34)
    scratch=ImageDraw.Draw(Image.new('RGB',(W,H)))
    rows=[]
    # Title appears once; it is not repeated from the answer body.
    rows.append((title.strip() or 'Student Notes','title'))
    last_blank=False
    for raw in answer.replace('\r','').split('\n'):
        s=raw.strip()
        if not s:
            if rows and not last_blank: rows.append(('', 'blank'))
            last_blank=True
            continue
        last_blank=False
        s=re.sub(r'^#{1,6}\s*','',s).replace('**','').replace('__','')
        s=re.sub(r'^[-*]\s+','• ',s)
        is_heading=bool(re.match(r'^\d+[.)]\s+.{3,70}$',s)) or bool(re.match(r'^(Conclusion|Introduction|Definition|Meaning|Objectives|Importance|Advantages|Disadvantages|Process|Methods)\b',s,re.I))
        kind='heading' if is_heading else 'text'
        fnt=heading if is_heading else regular
        chunks=wrap(scratch,s,fnt,W-LEFT-RIGHT)
        if kind=='heading' and rows and rows[-1][1]!='blank': rows.append(('', 'blank'))
        for j,chunk in enumerate(chunks): rows.append((chunk,kind if j==0 else 'text'))
    while rows and rows[-1][1]=='blank': rows.pop()

    def new_page():
        im=Image.new('RGB',(W,H),(253,252,248))
        d=ImageDraw.Draw(im)
        for baseline in range(TOP,H-BOTTOM+1,LINE):
            d.line((55,baseline+10,W-55,baseline+10),fill=(200,224,242),width=2)
        d.line((LEFT-30,40,LEFT-30,H-60),fill=(238,167,167),width=2)
        return im,d

    pages=[]; page,draw=new_page(); slot=0
    max_slots=(H-BOTTOM-TOP)//LINE
    i=0
    while i<len(rows):
        text,kind=rows[i]
        if kind=='blank':
            if slot>0 and slot<max_slots-2: slot+=1
            i+=1; continue
        # Keep headings together with at least one following line.
        if slot>=max_slots or (kind in ('heading','title') and slot>=max_slots-1):
            pages.append(page); page,draw=new_page(); slot=0
        fnt=heading if kind in ('heading','title') else regular
        baseline=TOP+slot*LINE
        # Pillow anchor='ls' positions by actual font baseline, avoiding line collisions.
        draw.text((LEFT,baseline),text,font=fnt,fill=INK,anchor='ls')
        if kind in ('heading','title'):
            width=min(draw.textlength(text,font=fnt),W-LEFT-RIGHT)
            draw.line((LEFT,baseline+5,LEFT+width,baseline+5),fill=INK,width=2)
        slot+=1; i+=1
    pages.append(page)
    result=[]
    for im in pages:
        output=BytesIO()
        im.save(output,format=fmt.upper(),quality=82,method=3 if fmt=='webp' else 4)
        result.append(output.getvalue())
    return result
