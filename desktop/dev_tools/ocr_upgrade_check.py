"""Replay annotated game text through both OCR engines without game input.

Usage: toolkit\python.exe -X utf8 -B dev_tools\ocr_upgrade_check.py
"""
from pathlib import Path
import argparse, json, os, sys, time, unicodedata
import cv2, numpy as np
ROOT=Path(__file__).resolve().parents[1]
os.chdir(ROOT);sys.path.insert(0,str(ROOT))
from module.ocr.ppocr import TextSystem
from module.ocr.sub_ocr import Digit

def normalize(value):return unicodedata.normalize('NFKC',value).replace(' ','')

def main():
 parser=argparse.ArgumentParser()
 parser.add_argument('--output',type=Path)
 args=parser.parse_args()
 fixture=ROOT/'dev_tools/fixtures/ocr_20261117'
 cases=json.loads((fixture/'manifest.json').read_text(encoding='utf-8'))['cases']
 images=[cv2.cvtColor(cv2.imdecode(np.fromfile(str(fixture/case['file']),np.uint8),1),cv2.COLOR_BGR2RGB) for case in cases]
 previous=os.environ.get('OAS_OCR_ENGINE')
 report={}
 try:
  for name,setting in [('legacy','legacy'),('upgraded','v6')]:
   os.environ['OAS_OCR_ENGINE']=setting
   model=TextSystem()
   if setting=='v6':assert model.recognition_engine.startswith('PP-OCRv6'),model.recognition_engine
   model.ocr_lines(images[:1])
   start=time.perf_counter();outputs=model.ocr_lines(images);elapsed=time.perf_counter()-start
   results=[dict(case,text=str(text),score=float(score),correct=normalize(str(text))==normalize(case['expected'])) for case,(text,score) in zip(cases,outputs)]
   report[name]={'engine':model.recognition_engine,'correct':sum(r['correct'] for r in results),'total':len(results),'elapsed_s':elapsed,'cases':results}
  old=report['legacy']['cases'];new=report['upgraded']['cases']
  report['regressions']=[b for a,b in zip(old,new) if a['correct'] and not b['correct']]
  digit=Digit('zero_check','Digit','Default',(0,0,1,1),(0,0,1,1),'')
  assert digit.after_process('00')==0 and digit.after_process('900')==900
  report['passed']=not report['regressions'] and report['upgraded']['correct']>=report['legacy']['correct']
 finally:
  if previous is None:os.environ.pop('OAS_OCR_ENGINE',None)
  else:os.environ['OAS_OCR_ENGINE']=previous
 if args.output:args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps({key:{k:v for k,v in value.items() if k!='cases'} if isinstance(value,dict) else value for key,value in report.items()},ensure_ascii=False))
 return 0 if report['passed'] else 1

if __name__=='__main__':sys.exit(main())
