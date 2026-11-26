from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
import logging
from module.exception import TaskEnd
from .store import CalendarStore,account_key,task_key

ROOT=Path(__file__).resolve().parents[2]
_active=ContextVar('oas_calendar_run',default=None)
_logger=logging.getLogger('oas.task_calendar')

class RunReceipt:
    def __init__(self,account,task,store):
        self.account=account_key(account);self.task=task_key(task);self.store=store
        self.decision=None;self.explicit=False;self.run_id=None
        try:self.run_id=store.begin(self.account,self.task)
        except Exception as error:_logger.warning('日程开始记录失败：%s',type(error).__name__)

    def finish(self,error=None):
        clean=error is None or isinstance(error,TaskEnd)
        if not clean:state='cancelled' if isinstance(error,(SystemExit,KeyboardInterrupt)) else 'failed'
        elif self.decision is False:state='deferred'
        elif self.decision is True or self.explicit:state='completed'
        else:state='unconfirmed'
        if self.run_id:
            try:self.store.finish(self.run_id,state,evidence={'schedule_success':self.decision,
                'explicit_completion':self.explicit,'error_type':type(error).__name__ if error else None})
            except Exception as failure:_logger.warning('日程结果记录失败：%s',type(failure).__name__)

def observe_schedule(account,task,success):
    active=_active.get()
    if active and active.account==str(account) and active.task==task_key(task) and type(success) is bool:
        active.decision=success

def mark_completed(task):
    """For terminal task paths that schedule a custom time instead of success=True."""
    active=_active.get()
    if active and active.task==task_key(task):active.explicit=True;active.decision=True

@contextmanager
def track_task(account,task,*,store=None):
    try:
        receipt=RunReceipt(account,task,store or CalendarStore(ROOT/'log/task_calendar/calendar.sqlite3'))
    except Exception as error:
        _logger.warning('日程记录不可用，原任务继续执行：%s',type(error).__name__)
        receipt=None
    token=_active.set(receipt)
    try:
        yield receipt
    except BaseException as error:
        if receipt:receipt.finish(error)
        raise
    else:
        if receipt:receipt.finish()
    finally:_active.reset(token)
