from pathlib import Path
import argparse,json,sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from module.task_calendar.backfill import import_logs
from module.task_calendar.service import report

def main():
    parser=argparse.ArgumentParser(description='OAS task completion calendar')
    parser.add_argument('account');parser.add_argument('--import-logs',action='store_true');parser.add_argument('--days',type=int,default=7)
    parser.add_argument('--week');args=parser.parse_args()
    if args.import_logs:print(json.dumps(import_logs(ROOT,args.account,days=args.days),ensure_ascii=False))
    value=report(args.account,args.week)
    print(json.dumps(value,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
