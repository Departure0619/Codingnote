"""Short-lived CPU OCR; reads sanitized PNG on stdin, never stores it."""
import json
from pathlib import Path
import socket
import sys


def deny_network(*_args, **_kwargs):
    raise OSError('Local OCR worker does not permit network access')


socket.socket.connect=deny_network
socket.socket.connect_ex=deny_network
socket.create_connection=deny_network
socket.getaddrinfo=deny_network
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'.local/ocr'))
from rapidocr_onnxruntime import RapidOCR
import cv2
import numpy as np
cv2.setNumThreads(2)
engine=RapidOCR(intra_op_num_threads=2,inter_op_num_threads=1,det_use_cuda=False,rec_use_cuda=False,
                cls_use_cuda=False,det_use_dml=False,rec_use_dml=False,cls_use_dml=False)
data=sys.stdin.buffer.read(40000000)
image=cv2.imdecode(np.frombuffer(data,dtype=np.uint8),cv2.IMREAD_COLOR)
results,_=engine(image)
lines=[{'text':str(item[1]),'confidence':round(float(item[2]),4)} for item in (results or [])][:1000]
sys.stdout.buffer.write(json.dumps({'text':'\n'.join(x['text'] for x in lines),'lines':lines},ensure_ascii=False).encode('utf8'))
