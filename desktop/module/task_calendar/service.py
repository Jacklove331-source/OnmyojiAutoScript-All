from __future__ import annotations

from datetime import datetime,timedelta
from pathlib import Path
import json
from .catalog import tasks
from .store import CalendarStore,account_key,moment,now

ROOT=Path(__file__).resolve().parents[2]

def report(account,week=None,*,root=None,store=None,at=None):
    root=Path(root or ROOT).resolve();account=account_key(account)
    config_file=root/'config'/f'{account}.json'
    if not config_file.is_file():raise FileNotFoundError('账号配置不存在')
    config=json.loads(config_file.read_text(encoding='utf-8-sig'))
    translations={}
    for filename in [root/'module/config/i18n/zh-CN.json',root/'assets/i18n/zh-CN.json']:
        if filename.exists():translations.update(json.loads(filename.read_text(encoding='utf-8-sig')))
    current=moment(at);day=moment(week).date() if week else current.date()
    monday=day-timedelta(days=day.weekday());start=moment(datetime.combine(monday,datetime.min.time()))
    end=start+timedelta(days=7);days=[(start+timedelta(days=i)).date().isoformat() for i in range(7)]
    journal=store or CalendarStore(root/'log/task_calendar/calendar.sqlite3')
    records=journal.records(account,start,end);rows=tasks(config,translations)
    for row in rows:
        own=[r for r in records if r['task']==row['id']]
        cells=[]
        for date in days:
            observed=[r for r in own if moment(r['finished_at'] or r['started_at']).date().isoformat()==date]
            completed=[r for r in observed if r['outcome']=='completed']
            last=observed[-1] if observed else None
            cells.append({'date':date,'done':bool(completed),'count':len(completed),
                'last_state':last['outcome'] if last else 'pending',
                'finished_at':completed[-1]['finished_at'] if completed else None,
                'source':completed[-1]['source'] if completed else None})
        row['days']=cells;row['week_count']=sum(c['count'] for c in cells)
        today=next((c for c in cells if c['date']==current.date().isoformat()),None)
        row['done']=row['week_count']>0 if row['period']=='weekly' else bool(today and today['done'])
    enabled=[r for r in rows if r['enabled']]
    resources={currency:sum(any(x['currency']==currency for x in r['rewards']) and not r['done'] for r in enabled)
               for currency in ['jade','ticket']}
    return {'version':1,'config':account,'timezone':'Asia/Shanghai','week_start':monday.isoformat(),
       'week_end':(end-timedelta(days=1)).date().isoformat(),'today':current.date().isoformat(),'generated_at':current.isoformat(timespec='seconds'),
       'days':days,'rows':rows,'summary':{'enabled':len(enabled),'completed':sum(r['done'] for r in enabled),
       'jade_remaining':resources['jade'],'ticket_remaining':resources['ticket']},
       'note':'按北京时间自然日/自然周展示；☑为OAS明确成功结束的执行记录。奖励标签提示来源，不统计未经核实的实际到账。'}
