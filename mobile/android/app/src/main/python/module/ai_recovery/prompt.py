# This Python file uses the following encoding: utf-8

"""发送给 DeepSeek 的提示词。

原则：
- 短、结构化、可复用（system 稳定，命中 prompt cache）；
- 只放必要上下文，日志摘要限长；
- 明确安全边界与 JSON schema，要求低置信度时停手。
"""

from __future__ import annotations

import json

SYSTEM_PROMPT = """你是《阴阳师》手游界面的视觉诊断器，服务于 OAS 自动化脚本的异常恢复层。
你会收到一张当前游戏截图和结构化上下文。只输出一个 JSON 对象，不要输出 Markdown、解释或多余文字。

JSON 字段（全部必填，除标注可选）：
- page: 字符串，当前界面名称（中文，如"庭院""百鬼棋局大厅""式神录""退出游戏确认框"）
- page_known: 布尔，该界面是否属于上下文给出的已知页面类型
- page_category: 枚举 main/lobby/battle/battle_prepare/battle_result/dialog/loading/shop/task/login/unknown
- cause: 字符串，卡住的最可能原因（<=120字）
- confidence: 0~1，你对 page+cause 的置信度
- risk: low/medium/high，执行建议动作的风险
- actions: 数组，0~3 个按顺序执行的动作；不确定时给空数组
  每个动作: {"type":"click|wait|back|close_popup|goto_page", "x":0~1, "y":0~1, "text":"按钮文字", "wait_ms":0~15000, "page_key":"", "reason":"<=60字"}
  坐标 x/y 是相对整张截图的归一化坐标（左上角 0,0，右下角 1,1）。
- expected_change: 执行后应看到的页面变化（<=80字）
- needs_human: 布尔，是否应停止自动操作等待人工
- template_update: 可选，{"needed":bool,"name":"英文模板名","region":{"x":0~1,"y":0~1,"w":0~1,"h":0~1},"note":"说明"}
  只有当你清楚看到某个可复用按钮/图标、且能给出紧贴其外框的 region 时才 needed=true。

安全规则（违反会导致整条结果被丢弃）：
1. 绝不建议点击充值、支付、购买、兑换、删除、分解、出售、账号设置、退出游戏、注销等不可逆或消费类按钮。
2. 不确定界面或按钮含义时：needs_human=true，actions=[]。
3. 不要连续重复点击同一点；一次只给少量动作，由执行器每次点击后重新截图验证。
4. 不要建议滑动、长按、输入、多指操作；这些不在白名单内。
5. 坐标必须落在按钮可见区域内，避开屏幕最边缘。
输出必须是合法 json。"""


def build_user_text(context: dict) -> str:
    """把上下文压成紧凑 JSON；限制每个字段长度，避免 token 浪费。"""
    payload = {
        'task': _short(context.get('task'), 40),
        'config': _short(context.get('config'), 20),
        'mode': context.get('mode', 'observe'),
        'anomaly': _short(context.get('anomaly'), 40),
        'anomaly_detail': _short(context.get('anomaly_detail'), 200),
        'detected_page': _short(context.get('page'), 60),
        'page_known_by_oas': bool(context.get('page_known_by_oas')),
        'view': context.get('view', 'full_screen'),
        'resolution': context.get('resolution'),
        'game_version': _short(context.get('game_version'), 30),
        'state_machine': _short(context.get('state_machine'), 120),
        'recent_actions': [_short(item, 60) for item in (context.get('recent_actions') or [])][-8:],
        'frame_history': [_short(item, 40) for item in (context.get('frame_history') or [])][-6:],
        'attempted_recoveries': [_short(item, 80) for item in (context.get('attempted_recoveries') or [])][-5:],
        'known_pages': [_short(item, 40) for item in (context.get('known_pages') or [])][:24],
        'kb_hint': _short(context.get('kb_hint'), 160),
        'error': _short(context.get('error'), 200),
    }
    return (
        '分析这张《阴阳师》截图并给出恢复动作。上下文：\n'
        + json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
        + '\n只输出 json。'
    )


def _short(value, limit: int) -> str:
    if value is None:
        return ''
    text = str(value).replace('\n', ' ').strip()
    return text[:limit]
