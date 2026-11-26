from pathlib import Path
from fastapi import APIRouter,HTTPException,Query,Response
from module.task_calendar.service import report

calendar_app=APIRouter(prefix='/calendar',tags=['calendar'])

@calendar_app.get('/{config_name}')
def calendar_report(config_name:str,response:Response,week:str|None=Query(default=None,pattern=r'^\d{4}-\d{2}-\d{2}$')):
    try:
        result=report(config_name,week)
        response.headers['Cache-Control']='no-store'
        return result
    except FileNotFoundError as error:raise HTTPException(status_code=404,detail=str(error)) from None
    except ValueError as error:raise HTTPException(status_code=400,detail='账号或日程日期无效') from None
    except Exception as error:raise HTTPException(status_code=503,detail='日程暂不可用，请稍后刷新') from None
