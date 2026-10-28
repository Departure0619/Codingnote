"""Local image OCR and conservative URL/text provenance candidates. No fetching."""
import base64
import binascii
import io
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
from urllib.parse import urlsplit
from fastapi import HTTPException
from . import models

ROOT=Path(__file__).resolve().parents[1]
OCR_LOCK=threading.Lock()


def recognize_source(url='',text=''):
    url=url.strip(); platform=''; original=''; basis=[]
    if url:
        parsed=urlsplit(url)
        if parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('来源链接须为无凭据的HTTP(S)地址。')
        host=parsed.hostname.lower(); path=parsed.path.rstrip('/')
        try: local=ipaddress.ip_address(host).is_global is False
        except ValueError: local=host in {'localhost'} or host.endswith('.localhost')
        if local: raise ValueError('来源识别不接受本机或内网地址。')
        rules=[('洛谷','luogu.com.cn',r'/problem/(P\d+)'),
               ('PTA','pintia.cn',r'/problem-sets/(\d+)/problems/(\d+)'),
               ('牛客','nowcoder.com',r'/practice/([a-fA-F0-9]{32})'),
               ('牛客','nowcoder.com',r'/acm/contest/(\d+)/([A-Za-z0-9]+)'),
               ('LeetCode','leetcode.cn',r'/problems/([a-z0-9-]+)'),
               ('LeetCode','leetcode.com',r'/problems/([a-z0-9-]+)'),
               ('Codeforces','codeforces.com',r'/contest/(\d+)/problem/([A-Za-z0-9]+)'),
               ('Codeforces','codeforces.com',r'/problemset/problem/(\d+)/([A-Za-z0-9]+)')]
        for name,domain,pattern in rules:
            if host==domain or host.endswith('.'+domain):
                platform=name; match=re.fullmatch(pattern,path)
                if match: original='/'.join(match.groups())
                basis=['域名精确匹配 '+domain, '规范路径匹配' if match else '路径不受支持，题号留空']
                if match: break
    if not platform and text:
        for word,name in [('洛谷','洛谷'),('PTA','PTA'),('牛客','牛客'),('LeetCode','LeetCode'),('Codeforces','Codeforces')]:
            if word in text:
                platform=name; basis=['OCR/文本中明确出现平台名称 '+word]
                if name=='洛谷':
                    m=re.search(r'\bP\d{4,}\b',text)
                    if m: original=m.group()
                break
    return {'url':url,'platform':platform,'original_id':original,'basis':basis or ['无可靠匹配，请手动补充'],
            'confirmed':False,'notice':'来源候选待用户确认；未访问第三方网页或验证题干。'}


def ocr_available():
    folder=ROOT/'.local/ocr'
    required=['rapidocr_onnxruntime/main.py','rapidocr_onnxruntime/config.yaml',
        'rapidocr_onnxruntime/models/ch_PP-OCRv4_det_infer.onnx',
        'rapidocr_onnxruntime/models/ch_PP-OCRv4_rec_infer.onnx',
        'rapidocr_onnxruntime/models/ch_ppocr_mobile_v2.0_cls_infer.onnx',
        'onnxruntime/__init__.py','cv2/__init__.py','numpy/__init__.py']
    return all((folder/name).is_file() for name in required)


def read_image(encoded):
    from PIL import Image,UnidentifiedImageError
    try:
        data=base64.b64decode(encoded,validate=True)
        if len(data)>5*1024*1024: raise ValueError('图片超过5MB。')
        with Image.open(io.BytesIO(data)) as im:
            if im.format not in {'PNG','JPEG','WEBP'} or getattr(im,'n_frames',1)!=1:
                raise ValueError('只支持单帧PNG/JPEG/WebP图片。')
            if min(im.size)<30 or max(im.size)>6000 or im.width*im.height>12000000:
                raise ValueError('图片尺寸须为30～6000像素且总像素不超过1200万。')
            im.verify()
        with Image.open(io.BytesIO(data)) as im:
            clean=im.convert('RGB'); out=io.BytesIO(); clean.save(out,format='PNG')
        return out.getvalue()
    except (binascii.Error,UnidentifiedImageError,OSError,Image.DecompressionBombError):
        raise ValueError('图片内容损坏、格式不支持或不是有效图片。') from None


def recognize_image(encoded):
    clean=read_image(encoded)
    if not ocr_available(): raise HTTPException(503,'本地OCR未安装；手动导入仍可用。按项目requirements-ocr.txt安装，不使用云OCR。')
    if not OCR_LOCK.acquire(blocking=False): raise HTTPException(429,'正在识别另一张图片，请稍后重试。')
    try:
        # OCR needs no service credentials, proxy settings, or provider configuration.
        # Read only named OS runtime variables instead of copying the parent environment.
        env={name:os.environ[name] for name in ('SYSTEMROOT','WINDIR','TEMP','TMP') if name in os.environ}
        env.update(OMP_NUM_THREADS='2',OPENBLAS_NUM_THREADS='2')
        result=subprocess.run([sys.executable,'-I',str(Path(__file__).with_name('ocr_worker.py'))],input=clean,
            capture_output=True,timeout=45,env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode: raise HTTPException(503,'本地OCR引擎未能完成识别；可手动录入。未保存原图。')
        value=json.loads(result.stdout)
        if (not isinstance(value,dict) or not isinstance(value.get('text'),str)
                or not isinstance(value.get('lines'),list) or len(value['lines'])>1000):
            raise ValueError('Invalid OCR worker output')
        for line in value['lines']:
            if (not isinstance(line,dict) or not isinstance(line.get('text'),str)
                    or type(line.get('confidence')) not in {float,int}
                    or not math.isfinite(line['confidence']) or not 0<=line['confidence']<=1):
                raise ValueError('Invalid OCR line')
        text=value['text'][:30000]; lines=[s.strip() for s in text.splitlines() if s.strip()]
        if not lines: raise HTTPException(422,'未识别出可靠文字，请裁剪或更换清晰截图；未创建题目。')
        title=next((s for s in lines if s not in {'PTA','牛客','洛谷','LeetCode','Codeforces'}),lines[0])[:160]
        return {'text':text,'title':title,'statement':text,'source':recognize_source('',text),
                'lines':value['lines'],'engine':'RapidOCR 1.4.4 / PP-OCRv4 / CPU',
                'confirmed':False,'image_retained':False,'notice':'识别是草稿，标题/题意/样例需逐字核对，尤其是空格、缩进与数学符号；没有补写缺失内容。'}
    except subprocess.TimeoutExpired:
        raise HTTPException(503,'OCR识别超时，已停止本地进程；请缩小截图或手动导入。') from None
    except (OSError,ValueError,KeyError,TypeError):
        raise HTTPException(503,'本地OCR引擎未能完成识别；可手动录入。未保存原图。') from None
    finally: OCR_LOCK.release()


def install(app,store):
    @app.get('/api/import/status')
    def status(): return {'ocr_available':ocr_available(),'local_only':True,'image_retained':False}

    @app.post('/api/import/source')
    def source(value:models.SourceRequest):
        candidate=recognize_source(value.url,value.text)
        with store.connect() as db:
            rows=db.execute('''SELECT t.id,t.title FROM tasks t JOIN task_sources s ON s.task_id=t.id
                WHERE s.source_platform=? AND s.original_id=?''',(candidate['platform'],candidate['original_id'])).fetchall() if candidate['original_id'] else []
        return {**candidate,'duplicates':[dict(r) for r in rows]}

    @app.post('/api/import/ocr')
    def ocr(value:models.OCRRequest): return recognize_image(value.image_base64)
