from __future__ import annotations

from pathlib import Path
from datetime import timedelta
import hashlib,re
from .store import account_key,task_key,moment,CalendarStore

HEADER=re.compile(r'^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+)\s*\|.*?\|\s*(INFO|WARNING|ERROR|CRITICAL|DEBUG)\s*\|\s*(.*)$')
START=re.compile(r'Scheduler: Start task `([^`]+)`')
END=re.compile(r'Scheduler: End task `([^`]+)`')
DELAY=re.compile(r'Delay task `([^`]+)`')

def log_records(lines):
    current=None
    for line_number,line in enumerate(lines,1):
        match=HEADER.match(line)
        if match:
            if current:yield current
            current={'at':match[1],'level':match[2],'text':match[3],'line':line_number}
        elif current:current['text']+=' '+line.strip()
    if current:yield current

def import_logs(root,account,*,store=None,days=7,at=None):
    root=Path(root);account=account_key(account);journal=store or CalendarStore(root/'log/task_calendar/calendar.sqlite3')
    earliest=moment(at)-timedelta(days=max(1,min(int(days),31)));added=0;observed=0
    for path in sorted((root/'log').glob('*_'+account+'.txt')):
        if not path.name.endswith('_'+account+'.txt') or path.stat().st_size>40_000_000:continue
        try:
            date=moment(path.name[:10])
            if date.date()<earliest.date():continue
        except ValueError:continue
        active=None
        with path.open(encoding='utf-8',errors='replace') as file:
            for record in log_records(file):
                text=record['text'];start=START.search(text)
                if start:
                    active={'task':task_key(start[1]),'at':record['at'],'line':record['line'],'success':None,'error':False}
                    continue
                if active is None:continue
                if record['level'] in ('ERROR','CRITICAL'):active['error']=True
                delay=DELAY.search(text)
                if delay and task_key(delay[1])==active['task']:
                    compact=re.sub(r'\s+','',text[delay.end():])
                    decision=re.search(r'success=(True|False)(?:[,)]|$)',compact)
                    if decision:active['success']=decision[1]=='True'
                end=END.search(text)
                if end:
                    if task_key(end[1])==active['task']:
                        observed+=1
                        if active['success'] is True and not active['error'] and moment(record['at'])>=earliest:
                            digest=hashlib.sha256((account+'|'+active['task']+'|'+active['at']+'|'+record['at']).encode()).hexdigest()
                            added+=journal.import_completed(account,active['task'],active['at'],record['at'],'log-'+digest,
                                {'file':path.name,'start_line':active['line'],'end_line':record['line'],'schedule_success':True})
                    active=None
    return {'observed_ended_runs':observed,'imported_completed':added,'source':'log','days':days}
