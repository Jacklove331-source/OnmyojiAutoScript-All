# This Python file uses the following encoding: utf-8

"""双 Agent 自动开发调度器 v2：Claude 总指挥（规划/审核/疑难/辅助），DeepSeek 执行。

状态机：
    PLAN(Claude) → EXECUTE(DeepSeek) → VERIFY(机器) → REVIEW(Claude) → PASS / REWORK / BLOCKED
                                                         └→ 返工用尽 → ESCALATE(Claude Opus 疑难分析) → 新任务卡

v2 相对 v1 的变化（2026-10-28 用户最高指令）：
- Agent A 走**真 Claude**（v1 两个角色都走 claude.CMD = DeepSeek）；按难度选模型
  （规划/疑难/高风险审核 = Opus，常规审核 = Sonnet）；
- 分供应商预算硬上限：总额（到顶 paused_budget，终态）+ 24h 滚动窗口（paused_daily，
  看门狗等窗口释放后自动续跑）；
- 环境故障（代理/VPN/网络）连续 N 次 → paused_env，稍后自动重试，不把任务判失败；
- 执行锁：调度器的 Agent B 与 DSH 里的 DeepSeek 会话（deepseek_b.py）不会同时改主工作区；
- 疑难升级：返工用尽 → Claude Opus 读代码给出具体方案 → 生成带 guidance 的新任务（每任务最多 1 次）；
- Claude 辅助任务（owner=claude，仅限 docs/research、dev_tools/analysis）在独立 worktree 完成，
  机器检查通过后再合并回主分支；
- 实机评估：策略任务被接受后，由调度器在空闲时启动连续 3 局评估（同时只允许一个实机进程）；
- 验收门禁：COMPLETED 之前**重新全量**跑验收（不信任旧 PASS）；
- 人工暂停：在 state/ 下放一个 `PAUSE` 文件 → 安全停在下一轮（paused_manual）。

用法：
    .\\toolkit\\python.exe dev_tools\\dual_agent\\scheduler.py --hours 7
    .\\toolkit\\python.exe dev_tools\\dual_agent\\scheduler.py --dry-run --tasks 1
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

import acceptance  # noqa: E402
import context  # noqa: E402
import safety  # noqa: E402
from cli_agent import AgentError, CliAgent, extract_json_block  # noqa: E402
from config import load_config  # noqa: E402
from state import State, now_text  # noqa: E402

ROLE_A = BASE_DIR / 'roles' / 'agent_a.md'
ROLE_B = BASE_DIR / 'roles' / 'agent_b.md'

MAX_STATUS_REASON = 2000
EXECUTOR_OWNER = 'scheduler'
TERMINAL_STATES = ('completed', 'paused_budget', 'paused_manual')
CLAUDE_AUX_PREFIXES = ('docs/research/', 'dev_tools/analysis/')
AC_VERIFY_ALLOWED = ('AC05', 'AC05S', 'AC08', 'AC09', 'AC10', 'AC10R', 'AC17T')


class BudgetExceeded(RuntimeError):
    def __init__(self, message: str, kind: str = 'total', resume_after: float = 0.0):
        super().__init__(message)
        self.kind = kind
        self.resume_after = resume_after


class EnvironmentPause(RuntimeError):
    pass


class ExecutorBusy(RuntimeError):
    def __init__(self, info: dict):
        super().__init__(f"执行锁被 {info.get('owner')} 占用（任务 {info.get('task_key')}）")
        self.info = info


def log_line(state: State, message: str) -> None:
    stamp = datetime.now().strftime('%H:%M:%S')
    line = f'[{stamp}] {message}'
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode('utf-8', 'replace').decode('ascii', 'replace'), flush=True)
    state.event('log', message=message[:1500])


def text_after(hours: float) -> str:
    return (datetime.now() - timedelta(hours=hours)).strftime('%Y-%m-%d %H:%M:%S')


class Scheduler:
    def __init__(self, config, state: State, dry_run: bool = False):
        self.config = config
        self.state = state
        self.dry_run = dry_run
        self.agent = None if dry_run else CliAgent(config)
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread = None
        self.process_started = time.time()
        self.session_start = str(state.get_meta('session_start', '') or now_text())
        self.started_epoch = float(
            state.get_meta('session_started_epoch', 0) or time.time()
        )
        if not state.get_meta('session_start'):
            state.set_meta('session_start', self.session_start)
            state.set_meta('session_started_epoch', self.started_epoch)
        # 棋局记录用行号基线：开局时间早于会话开始的「在打的那局」也要算数
        if state.get_meta('matches_baseline_lines') is None:
            state.set_meta('matches_baseline_lines', self._matches_lines())

    def _matches_lines(self) -> int:
        matches = self.config.project_root / 'log' / 'chess_matches.jsonl'
        if not matches.exists():
            return 0
        return len([
            line for line in matches.read_text(
                encoding='utf-8', errors='replace').splitlines() if line.strip()
        ])

    def ensure_v2_session(self) -> None:
        """v2 首次启动：开启新会话（预算/验收窗口从此刻算，v1 的花费与证据不再计入）。"""
        if self.state.get_meta('v2_started_at'):
            return
        now = now_text()
        self.state.set_meta('v2_started_at', now)
        self.state.set_meta('v1_session_start', self.session_start)
        self.state.set_meta('session_start', now)
        self.state.set_meta('session_started_epoch', time.time())
        self.state.set_meta('matches_baseline_lines', self._matches_lines())
        self.state.set_meta('chess_runs_started', 0)
        self.session_start = now
        self.started_epoch = time.time()
        self.state.event('v2_session_start', at=now)
        log_line(self.state, f'调度器 v2 会话开始 {now}（预算/实战指标窗口从此刻计）')

    def start_heartbeat_thread(self, interval: float = 30.0) -> None:
        """长调用期间也要有真实心跳；sqlite3 连接不能跨线程共享，心跳线程用自己的连接。"""

        def beat() -> None:
            beat_state = None
            while not self._heartbeat_stop.wait(interval):
                try:
                    if beat_state is None:
                        beat_state = type(self.state)(
                            self.config.state_dir, self.config.project_root
                        )
                    beat_state.heartbeat('running', {
                        'phase': str(beat_state.get_meta('current_phase', '') or 'agent_call'),
                    })
                except Exception:  # noqa: BLE001 - 心跳失败不能影响主流程
                    beat_state = None
                    return

        self._heartbeat_thread = threading.Thread(target=beat, daemon=True)
        self._heartbeat_thread.start()

    def stop_heartbeat_thread(self) -> None:
        self._heartbeat_stop.set()

    def set_phase(self, text: str) -> None:
        self.state.set_meta('current_phase', text[:200])

    # ------------------------------------------------------------- budget
    def budget_snapshot(self) -> dict:
        budget = self.config.budget
        since = self.session_start
        day = max(since, text_after(24))
        total = self.state.run_totals(since=since)
        a_total = self.state.run_totals(since=since, provider='anthropic')
        a_day = self.state.run_totals(since=day, provider='anthropic')
        d_total = self.state.run_totals(since=since, provider='deepseek')
        d_day = self.state.run_totals(since=day, provider='deepseek')
        calls_day = self.state.run_totals(since=day)['calls']
        a_caps = self.config.provider_caps('anthropic')
        d_caps = self.config.provider_caps('deepseek')
        return {
            'cost_cny': round(float(total['cost_cny']), 4),
            'calls': int(total['calls']),
            'calls_24h': int(calls_day),
            'elapsed_h': round((time.time() - self.process_started) / 3600.0, 2),
            'claude_usd': round(float(a_total['cost_usd']), 4),
            'claude_usd_24h': round(float(a_day['cost_usd']), 4),
            'deepseek_cny': round(float(d_total['cost_cny']), 4),
            'deepseek_cny_24h': round(float(d_day['cost_cny']), 4),
            'max_cost': float(budget.get('max_cny_total', 12.0)),
            'max_calls': int(budget.get('max_calls', 160)),
            'max_calls_24h': int(budget.get('max_calls_24h', 10 ** 9)),
            'max_hours': float(budget.get('max_hours', 7.5)),
            'max_claude_usd': float(a_caps.get('max_usd_total', 10 ** 9)),
            'max_claude_usd_24h': float(a_caps.get('max_usd_24h', 10 ** 9)),
            'max_deepseek_cny': float(d_caps.get('max_cny_total', 10 ** 9)),
            'max_deepseek_cny_24h': float(d_caps.get('max_cny_24h', 10 ** 9)),
        }

    def check_budget(self) -> None:
        snap = self.budget_snapshot()
        if snap['cost_cny'] >= snap['max_cost']:
            raise BudgetExceeded(f"总费用上限 ¥{snap['max_cost']} 已达（¥{snap['cost_cny']}）")
        if snap['calls'] >= snap['max_calls']:
            raise BudgetExceeded(f"总调用次数上限 {snap['max_calls']} 已达")
        if snap['claude_usd'] >= snap['max_claude_usd']:
            raise BudgetExceeded(f"Claude 总额上限 ${snap['max_claude_usd']} 已达")
        if snap['deepseek_cny'] >= snap['max_deepseek_cny']:
            raise BudgetExceeded(f"DeepSeek 总额上限 ¥{snap['max_deepseek_cny']} 已达")
        resume = time.time() + 3600
        if snap['claude_usd_24h'] >= snap['max_claude_usd_24h']:
            raise BudgetExceeded(
                f"Claude 24h 上限 ${snap['max_claude_usd_24h']} 已达", 'daily', resume)
        if snap['deepseek_cny_24h'] >= snap['max_deepseek_cny_24h']:
            raise BudgetExceeded(
                f"DeepSeek 24h 上限 ¥{snap['max_deepseek_cny_24h']} 已达", 'daily', resume)
        if snap['calls_24h'] >= snap['max_calls_24h']:
            raise BudgetExceeded(f"24h 调用次数上限 {snap['max_calls_24h']} 已达",
                                 'daily', resume)
        if snap['elapsed_h'] >= snap['max_hours']:
            raise BudgetExceeded(f"单次运行时长上限 {snap['max_hours']}h 已达", 'hours')

    def _pause_for_budget(self, error: BudgetExceeded) -> str:
        if error.kind == 'daily':
            status = 'paused_daily'
            self.state.set_meta('resume_after', error.resume_after)
        elif error.kind == 'hours':
            status = 'paused_limit'
        else:
            status = 'paused_budget'
        self.state.set_meta('status', status)
        self.state.set_meta('pause_reason', str(error))
        self.state.event(status, reason=str(error))
        log_line(self.state, f'暂停（{status}）: {error}')
        return status

    # -------------------------------------------------------------- prompts
    def _role_text(self, path: Path, fallback: str) -> str:
        try:
            return path.read_text(encoding='utf-8').strip()
        except OSError:
            return fallback

    def _acceptance_board(self) -> str:
        rows = {row['ac_id']: row for row in self.state.acceptance_rows()}
        lines = []
        for item in acceptance.acceptance_spec():
            row = rows.get(item['id'])
            mark = 'PASS' if row and row['ok'] else ('FAIL' if row else '未检查')
            detail = str(row['detail'])[:160] if row else ''
            lines.append(f"- [{mark}] {item['id']} {item['title']}"
                         + (f' :: {detail}' if detail and mark != 'PASS' else ''))
        return '\n'.join(lines)

    def _recent_reviews(self, limit: int = 6) -> str:
        rows = self.state.conn.execute(
            'SELECT task_key, verdict, provider, notes FROM reviews '
            'ORDER BY id DESC LIMIT ?', (limit,)
        ).fetchall()
        return '\n'.join(
            f"- {row['task_key']} {row['verdict']}({row['provider']}): "
            f"{str(row['notes'])[:200]}" for row in rows
        ) or '无'

    def plan_prompt(self, force_json: bool = False) -> str:
        digest = context.project_digest(self.config, self.state)
        open_tasks = [f'{task.task_key}({task.phase})' for task in self.state.open_tasks()]
        blocked = [row['task_key'] for row in self.state.conn.execute(
            "SELECT task_key FROM tasks WHERE phase='blocked' ORDER BY id DESC LIMIT 8")]
        job = self.state.get_meta('chess_job')
        role = self._role_text(ROLE_A, '你是 Claude 总指挥，只做决策与审核，不改代码。')
        schema = (
            '{"tasks":[{"id":"T-<简短英文>","title":"<中文>","owner":"deepseek",'
            '"goal":"<要改成什么样>","evidence":"<根因/证据：文件:行号 或 日志行>",'
            '"files":["路径"],"constraints":["..."],"acceptance":"<怎么算做完>",'
            '"verify":["AC08"],"tests":["dev_tools/<本任务新增的测试>.py"],'
            '"max_rework":1,"risk":"low|medium|high"}]}'
        )
        if force_json:
            return (
                '这是重试：上一次调用因为工具调用过多被截断。这次**不要调用任何工具**，'
                '直接基于下面的摘要输出 JSON。\n\n' + digest
                + '\n\n## 验收清单\n' + self._acceptance_board()
                + '\n\n只输出一个 JSON：' + schema
            )
        return (
            f'{role}\n\n{digest}\n\n'
            f'## 验收清单（全部强制，当前状态）\n{self._acceptance_board()}\n\n'
            f'## 进行中任务\n{open_tasks or "无"}\n'
            f'## 最近阻塞任务（不要原样重排）\n{blocked or "无"}\n'
            f'## 最近审核意见\n{self._recent_reviews()}\n'
            f'## 实机评估\n{json.dumps(job, ensure_ascii=False) if job else "空闲（由调度器按需启动连续 3 局评估）"}\n\n'
            '## 现在：规划 1~3 个任务，直接推动未过的验收项\n'
            '1. 先修**测量/证据**问题（指标统计错了，后面的策略判断全是错的），再修行为；\n'
            '2. 一个任务一次改动，能被机器检查；DeepSeek 没有终端，验证由调度器跑；\n'
            f'3. verify 只能填：{", ".join(AC_VERIFY_ALLOWED)}；实战指标（AC06/AC13~AC17）'
            '由调度器的实机评估产出，不要安排「去跑实机」的任务；\n'
            '4. owner 默认 deepseek（业务源码/测试）；owner=claude 只用于联网核实/研究文档'
            '（只能写 docs/research/ 或 dev_tools/analysis/），例如 AC10R 赛季核实；\n'
            '5. 禁止需要人工操作、改游戏/模拟器设置、删文件、改 config/*.json 的任务；\n'
            '6. 工具调用最多 4 次，只读关键文件；拿到信息立刻输出 JSON。\n'
            '输出**只允许**一个 JSON：' + schema
        )

    def execute_prompt(self, task, feedback: str = '') -> str:
        root = self.config.project_root
        card = json.dumps(task.card, ensure_ascii=False, indent=1)
        digest = context.project_digest(self.config, self.state)
        files = []
        for name in (task.card.get('files') or [])[:6]:
            path = root / name
            if path.is_file():
                files.append(f'### {name}\n```\n{context.read_slice(path, 5000)}\n```')
            elif path.is_dir():
                listing = '\n'.join(
                    str(child.relative_to(root))
                    for child in sorted(path.glob('*.py'))[:20]
                )
                files.append(f'### {name}（目录）\n{listing}')
        diff = context.diff_text(root, 6000) or '（当前无未提交改动）'
        role = self._role_text(ROLE_B, '你是 DeepSeek 执行工程师，只负责改代码与写测试。')
        parts = [
            role, '', digest, '',
            f'## 当前任务（Claude 下达）\n```json\n{card}\n```',
            '',
            '## 任务相关源码（片段）',
            '\n\n'.join(files) or '（未指定文件，请自行定位）',
            '',
            '## 当前工作区未提交 diff',
            diff,
        ]
        if task.card.get('guidance'):
            parts += ['', '## Claude 疑难分析给出的具体方案（照此执行）',
                      json.dumps(task.card.get('guidance'), ensure_ascii=False)[:4000]]
        if feedback:
            parts += ['', '## 上次 Claude 审核的返工意见（必须逐条修复）', feedback[:3000]]
        parts += [
            '',
            '## 交付纪律（否则会因回合用尽被判返工）',
            '1. **工具调用总数 ≤ 12 次**；只读任务卡里的文件（最多再补 1 个相邻文件），',
            '   不要 Grep 全仓库、不要遍历 log 目录、不要读大日志原文；',
            '2. 改完**立刻**输出最终 JSON；你没有终端权限，编译/回归/实机验证由调度器执行'
            f'（本次验证项：{task.card.get("verify") or ["AC05S"]}）；',
            '3. 不改 config/*.json、不改 .credentials、不删文件、不改 dev_tools/dual_agent/；',
            '4. 最后一条消息**只输出**一个 JSON（不要贴整段 diff）：',
            '{"summary":"改了什么","files_changed":["..."],'
            '"tests_run":["..."],"notes":"风险/未解决/推翻了哪些前提"}',
        ]
        return '\n'.join(parts)

    def review_prompt(self, task, verify_rows: list[dict], b_summary: dict) -> str:
        digest = context.project_digest(self.config, self.state)
        base_sha = str((task.evidence or {}).get('base_sha') or '')
        # 审核只给 4000 字符 diff：足够看清要点；需要上下文可用 Read 按行号查
        diff = context.diff_text(self.config.project_root, 4000,
                                 since=base_sha or None)
        stat = context.diff_stat(self.config.project_root,
                                 since=base_sha or None)
        history = self.state.reviews_for(task.task_key)
        role = self._role_text(ROLE_A, '你是 Claude 审核员，只审不改。')
        return (
            f'{role}\n\n{digest}\n\n'
            f'## 待审核任务\n```json\n'
            f'{json.dumps(task.card, ensure_ascii=False, indent=1)}\n```\n\n'
            f'## 本任务历史审核\n'
            f'{json.dumps(history[-3:], ensure_ascii=False)[:1500] if history else "无"}\n\n'
            f'## DeepSeek 自述（只作参考，不作证据）\n```json\n'
            f'{json.dumps(b_summary, ensure_ascii=False, indent=1)[:2500]}\n```\n\n'
            f'## 机器验证结果（调度器实际执行）\n```json\n'
            f'{json.dumps(verify_rows, ensure_ascii=False, indent=1)[:3000]}\n```\n\n'
            f'## 本任务改动（相对检查点 {base_sha or "HEAD"}）\n'
            f'diff --stat:\n{stat}\n\n'
            f'```diff\n{diff}\n```\n\n'
            '## 判断标准\n'
            '- 证据只认 diff + 机器验证 + 你自己用 Read 查到的代码；DeepSeek 的文字声明不算；\n'
            '- 你只有 Read/Grep/Glob，不能执行任何东西：结论里不要写「我实跑了」，'
            '运行结果只引用上面的机器验证；\n'
            '- 不要要求 DeepSeek 跑命令/实机：它没有终端，实机证据由调度器产出；\n'
            '- PASS：实现了任务目标核心 + 机器验证全过 + 无危险改动/无回归风险；\n'
            '- REWORK：方向对但没做完/有 bug/漏测试——必须给出**具体**修改点（文件:行号/函数名）；\n'
            '- BLOCKED：需要人工或外部条件，写清原因；\n'
            '- 工具调用最多 3 次。\n'
            '输出**只允许**一个 JSON：\n'
            '{"verdict":"PASS|REWORK|BLOCKED","notes":"给 DeepSeek 的具体意见或结论",'
            '"reason":"判定依据","knowledge":"<可选：一句话已验证结论，供以后复用>"}'
        )

    def escalate_prompt(self, task, verdict: dict, verify_rows: list[dict]) -> str:
        digest = context.project_digest(self.config, self.state)
        base_sha = str((task.evidence or {}).get('base_sha') or '')
        stat = context.diff_stat(self.config.project_root, since=base_sha or None)
        history = self.state.reviews_for(task.task_key)
        return (
            '你是 Claude 总指挥。下面这个任务 DeepSeek 多次修复仍未通过审核，'
            '现在由你亲自做疑难分析：读相关源码（Read/Grep，最多 10 次工具调用），'
            '找出真正根因，给出可以直接照做的方案。不要泛泛而谈。\n\n'
            f'{digest}\n\n## 任务\n```json\n{json.dumps(task.card, ensure_ascii=False, indent=1)}\n```\n'
            f'## 历次审核\n{json.dumps(history, ensure_ascii=False)[:3000]}\n'
            f'## 最后一次审核\n{json.dumps(verdict, ensure_ascii=False)[:1500]}\n'
            f'## 机器验证\n{json.dumps(verify_rows, ensure_ascii=False)[:1500]}\n'
            f'## 当前改动 stat\n{stat}\n\n'
            '输出**只允许**一个 JSON：\n'
            '{"diagnosis":"根因（带文件:行号证据）","decision":"retry|split|drop",'
            '"knowledge":"一句话结论","tasks":[{"id":"T-...","title":"...","goal":"...",'
            '"files":["..."],"guidance":["第1步：在 文件:行号 把 X 改成 Y", "..."],'
            '"constraints":["..."],"verify":["AC08"],"risk":"medium"}]}\n'
            'decision=drop 表示前提不成立/不值得做，tasks 可为空。'
        )

    def aux_prompt(self, task) -> str:
        digest = context.project_digest(self.config, self.state)
        return (
            '你是 Claude（总指挥兼辅助工程师），在**独立 worktree** 里完成一项辅助任务。\n'
            f'只允许新建/修改这些目录下的文件：{", ".join(CLAUDE_AUX_PREFIXES)}；'
            '其他路径一律不要碰（不会被合并）。联网资料只作参考，要写清来源 URL 与核实日期，'
            '与游戏内/仓库数据冲突时以游戏内证据为准并标注。\n\n'
            f'{digest}\n\n## 任务\n```json\n{json.dumps(task.card, ensure_ascii=False, indent=1)}\n```\n'
            '完成后**只输出**一个 JSON：{"summary":"...","files_changed":["..."],'
            '"sources":["url"],"notes":"..."}'
        )

    # --------------------------------------------------------------- agents
    def call_agent(self, role: str, prompt: str, task_key: str = '',
                   mode: str = 'default', prompt_for_retry: str | None = None,
                   tools_override: str | None = None, purpose: str = '',
                   cwd: Path | None = None) -> dict:
        self.check_budget()
        if self.dry_run:
            return {'ok': True, 'text': '{"dry_run":true}', 'usage': {},
                    'cost': 0.0, 'denials': [], 'error': '', 'provider': 'dry',
                    'model': 'dry'}
        started = now_text()
        self.set_phase(f'{role}:{purpose or "call"}:{task_key}')
        kwargs = dict(role=role, task_key=task_key, mode=mode,
                      prompt_for_retry=prompt_for_retry, tools_override=tools_override)
        if purpose:
            kwargs['purpose'] = purpose
        original_root = None
        if cwd is not None:
            original_root = self.config.project_root
            self.config.project_root = cwd
        try:
            result = self.agent.run(prompt, **kwargs)
        finally:
            if original_root is not None:
                self.config.project_root = original_root
        self.state.record_run(task_key, role, started, result)
        provider = str(getattr(result, 'provider', '') or '')
        model = str(getattr(result, 'model', '') or '')
        usage = getattr(result, 'usage', {}) or {}
        log_line(
            self.state,
            f'{role}[{provider}/{model}/{purpose}] ok={result.ok} {result.duration_s}s '
            f'¥{result.cost_cny:.4f} in={usage.get("input_tokens", 0)}'
            f'+cr{usage.get("cache_read_input_tokens", 0)}'
            f'+cw{usage.get("cache_creation_input_tokens", 0)} '
            f'out={usage.get("output_tokens", 0)}'
            + (f' err={str(result.error)[:120]}' if not result.ok else '')
            + (f' denials={len(result.denials)}' if result.denials else '')
        )
        kind = str(getattr(result, 'error_kind', '') or '')
        if result.ok:
            self.state.set_meta('env_failures', 0)
        elif kind == 'env':
            failures = int(self.state.bump_meta('env_failures', 1))
            if failures >= int(self.config.limit('env_failures_before_pause', 3)):
                raise EnvironmentPause(
                    f'{provider} 连续 {failures} 次网络/代理故障：{str(result.error)[:200]}')
        return {
            'ok': result.ok,
            'text': result.text,
            'usage': usage,
            'cost': result.cost_cny,
            'denials': result.denials,
            'error': result.error,
            'provider': provider,
            'model': model,
        }

    # ------------------------------------------------------------- planning
    def plan_tasks(self) -> list:
        if self.dry_run:
            return []
        response = self.call_agent(
            'agent_a', self.plan_prompt(), mode='plan', purpose='plan',
            prompt_for_retry=self.plan_prompt(force_json=True),
        )
        if not response['ok']:
            self.state.event('plan_failed', error=str(response['error'])[:400])
            return []
        payload = extract_json_block(response['text']) or {}
        return self._add_cards(payload.get('tasks') or [], source='plan')

    def _add_cards(self, cards: list, source: str, extra: dict | None = None) -> list:
        added = []
        for card in cards:
            if not isinstance(card, dict) or not card.get('title'):
                continue
            card = dict(card)
            card.setdefault('id', f"T-{int(time.time())}-{len(added)}")
            if self.state.get_task(str(card['id'])) is not None:
                card['id'] = f"{card['id']}-{int(time.time()) % 100000}"
            card.setdefault('max_rework', int(self.config.limit('max_rework', 1)))
            verify = [str(item) for item in (card.get('verify') or ['AC05S'])
                      if str(item) in AC_VERIFY_ALLOWED]
            card['verify'] = verify or ['AC05S']
            owner = str(card.get('owner') or 'deepseek').lower()
            card['owner'] = 'claude' if owner == 'claude' else 'deepseek'
            card['source'] = source
            if extra:
                card.update(extra)
            task = self.state.add_task(card)
            added.append(task)
            self.state.event('task_planned', task_key=task.task_key, title=task.title,
                             owner=card['owner'], source=source)
        log_line(self.state, f'Claude {source} 新增 {len(added)} 个任务: '
                             f'{[task.task_key for task in added]}')
        return added

    # ------------------------------------------------------------ execution
    def execute_task(self, task, feedback: str = '') -> dict:
        response = self.call_agent(
            'agent_b', self.execute_prompt(task, feedback),
            task_key=task.task_key, mode='acceptEdits', purpose='execute',
        )
        payload = extract_json_block(response['text']) or {}
        ok = bool(response['ok'])
        partial = False
        if not ok and 'max_turns' in str(response.get('error') or '').lower():
            changed = context.changed_files(self.config.project_root)
            if changed:
                ok = True
                partial = True
                payload.setdefault(
                    'summary',
                    'partial: DeepSeek 回合用尽但工作区已改动 '
                    f'({len(changed)} 个文件)',
                )
        summary = {
            'ok': ok,
            'partial': partial,
            'text': str(response['text'])[-3000:],
            'summary': payload.get('summary', ''),
            'files_changed': payload.get('files_changed', []),
            'tests_run': payload.get('tests_run', []),
            'notes': payload.get('notes', ''),
            'denials': response['denials'],
            'error': response.get('error', ''),
            'executor': 'deepseek (scheduler agent_b)',
        }
        self.state.update_task(
            task.task_key,
            phase='verifying',
            agent_b_summary=json.dumps(summary, ensure_ascii=False)[:6000],
            attempts=task.attempts + 1,
        )
        return summary

    def verify_task(self, task, root: Path | None = None) -> list[dict]:
        root = root or self.config.project_root
        rows: list[dict] = []
        compile_code, compile_out = safety.run_guarded(
            self.config, [str(self.config.python), '-m', 'compileall',
                          '-q', 'tasks', 'module', 'dev_tools'],
            timeout=300, cwd=root,
        )
        rows.append({'check': 'compileall', 'ok': compile_code == 0,
                     'detail': compile_out[-600:]})
        for ac_id in (task.card.get('verify') or ['AC05S']):
            ac_id = str(ac_id)
            result = None
            if ac_id in acceptance.SCRIPT_AC:
                result = acceptance.check_scripts(self.config, ac_id)
            elif ac_id == 'AC05S':
                result = acceptance.check_static(self.config)
            elif ac_id == 'AC10R':
                original = self.config.project_root
                self.config.project_root = root
                try:
                    result = acceptance.check_season_research(self.config)
                finally:
                    self.config.project_root = original
            if result is None:
                rows.append({'check': ac_id, 'ok': False, 'detail': '未知验收项'})
                continue
            rows.append({'check': ac_id, 'ok': result.ok,
                         'detail': result.detail[:800]})
            self.state.record_verification(
                task.task_key, ac_id, result.ok, result.detail
            )
        rows.extend(self.card_test_rows(task, root))
        return rows

    def card_test_rows(self, task, root: Path) -> list[dict]:
        """任务卡自带的新测试脚本（只允许 dev_tools/*.py，且不能是调度器自身）。"""
        rows: list[dict] = []
        for script in (task.card.get('tests') or [])[:4]:
            script = str(script).replace('\\', '/').strip()
            if not (script.startswith('dev_tools/') and script.endswith('.py')) \
                    or script.startswith('dev_tools/dual_agent/') or '..' in script:
                rows.append({'check': script, 'ok': False, 'detail': '不允许的测试路径'})
                continue
            if not (root / script).exists():
                rows.append({'check': script, 'ok': False, 'detail': '测试脚本不存在'})
                continue
            code, out = safety.run_guarded(
                self.config, [str(self.config.python), script], timeout=900, cwd=root)
            tail = acceptance._tail(out, 2)
            rows.append({'check': script, 'ok': code == 0, 'detail': f'rc={code} {tail}'[:800]})
            self.state.record_verification(task.task_key, script, code == 0, out[-1500:])
        return rows

    def _review_purpose(self, task) -> str:
        card = task.card or {}
        if str(card.get('risk', '')).lower() == 'high' or task.attempts >= 2 \
                or card.get('escalated_from'):
            return 'review_hard'
        return 'review'

    def review_task(self, task, verify_rows: list[dict], b_summary: dict) -> dict:
        if not b_summary.get('ok'):
            return {
                'verdict': 'REWORK',
                'notes': f'DeepSeek 调用失败：{str(b_summary.get("error", ""))[:600]}',
                'reason': 'agent_b_error', 'provider': 'scheduler', 'model': '',
            }
        if self.dry_run:
            return {'verdict': 'PASS', 'notes': 'dry-run', 'reason': 'dry-run',
                    'provider': 'dry', 'model': ''}
        response = self.call_agent(
            'agent_a', self.review_prompt(task, verify_rows, b_summary),
            task_key=task.task_key, mode='plan', purpose=self._review_purpose(task),
        )
        if not response['ok']:
            # 审核调用失败不等于任务失败：按机器验证结果兜底（标记出来，不算 Claude 通过）
            failed = [row for row in verify_rows if not row['ok']]
            return {
                'verdict': 'REWORK' if failed else 'PASS',
                'notes': 'Claude 审核调用失败，按机器验证兜底',
                'reason': str(response['error'])[:300],
                'provider': 'machine_fallback', 'model': '',
            }
        payload = extract_json_block(response['text']) or {}
        verdict = str(payload.get('verdict', '')).upper()
        if verdict not in ('PASS', 'REWORK', 'BLOCKED'):
            verdict = 'REWORK'
        # 机器验证没过却给 PASS → 不采信（防止审核被文字说服）
        if verdict == 'PASS' and any(not row['ok'] for row in verify_rows):
            verdict = 'REWORK'
            payload['notes'] = ('机器验证未通过，不能 PASS：'
                                + json.dumps([row for row in verify_rows if not row['ok']],
                                             ensure_ascii=False)[:800])
        return {
            'verdict': verdict,
            'notes': str(payload.get('notes', ''))[:MAX_STATUS_REASON],
            'reason': str(payload.get('reason', ''))[:800],
            'knowledge': str(payload.get('knowledge', '') or '')[:300],
            'provider': response.get('provider', ''),
            'model': response.get('model', ''),
        }

    # ------------------------------------------------------------ task flow
    def run_task(self, task) -> None:
        if str(task.card.get('owner', 'deepseek')) == 'claude':
            self.run_claude_aux(task)
            return
        ttl = float(self.config.limit('executor_lock_ttl_sec', 10800))
        ok, info = self.state.acquire_lock('executor', EXECUTOR_OWNER, ttl, task.task_key)
        if not ok:
            raise ExecutorBusy(info)
        try:
            # 「交付给审核」的半成品（DSH 会话 record 后没 review / 调度器崩在验证前）：只补审核
            if task.phase == 'verifying' and self._has_summary(task):
                log_line(self.state, f'恢复：{task.task_key} 已执行，直接验证+审核')
                self.finalize_task(task, self._stored_summary(task))
                return
            checkpoint = safety.git_checkpoint(
                self.config, self.state,
                f'chore(agent): checkpoint before {task.task_key}',
            )
            evidence = dict(task.evidence or {})
            if checkpoint.get('sha'):
                evidence['base_sha'] = checkpoint['sha']
                self.state.update_task(
                    task.task_key,
                    evidence=json.dumps(evidence, ensure_ascii=False)[:6000],
                )
                task = self.state.get_task(task.task_key) or task
            feedback = str(evidence.get('feedback', '') or '')
            log_line(self.state, f'执行任务 {task.task_key}: {task.title}')
            b_summary = self.execute_task(task, feedback)
            self.finalize_task(self.state.get_task(task.task_key) or task, b_summary)
            # 每个任务结束就提交：释放执行锁时主工作区是干净的，
            # 下一位执行者（或外部改动检测）看到的未提交改动一定不是本任务的
            last = (self.state.reviews_for(task.task_key) or [{}])[-1]
            safety.git_checkpoint(
                self.config, self.state,
                f'chore(agent): after {task.task_key} '
                f'({last.get("verdict", "?")} by {last.get("model") or last.get("provider") or "?"})',
            )
        finally:
            self.state.release_lock('executor', EXECUTOR_OWNER)

    def _foreign_activity(self) -> list[str]:
        """主工作区里「最近 N 分钟改过、还没提交」的文件 = 有没持锁的执行者在干活。"""
        minutes = float(self.config.limit('foreign_activity_minutes', 10))
        if minutes <= 0:
            return []
        root = self.config.project_root
        cutoff = time.time() - minutes * 60
        ignore = ('dev_tools/dual_agent/state', 'dev_tools/dual_agent/logs', 'log/',
                  'AI_COORD.md')
        recent = []
        for name in context.changed_files(root):
            name = name.strip('"').replace('\\', '/')
            if name.startswith(ignore):
                continue
            path = root / name
            try:
                if path.exists() and path.stat().st_mtime >= cutoff:
                    recent.append(name)
            except OSError:
                continue
        return recent

    def _has_summary(self, task) -> bool:
        row = self.state.conn.execute(
            'SELECT agent_b_summary FROM tasks WHERE task_key = ?', (task.task_key,)
        ).fetchone()
        return bool(row and row['agent_b_summary'])

    def _stored_summary(self, task) -> dict:
        row = self.state.conn.execute(
            'SELECT agent_b_summary FROM tasks WHERE task_key = ?', (task.task_key,)
        ).fetchone()
        try:
            data = json.loads(row['agent_b_summary']) if row else {}
        except (TypeError, ValueError):
            data = {'summary': str(row['agent_b_summary'])[:2000]} if row else {}
        if not isinstance(data, dict):
            data = {}
        data.setdefault('ok', True)
        return data

    def finalize_task(self, task, b_summary: dict) -> None:
        """验证 + 审核 + 状态转移；--review-task / deepseek_b.py review 也走这里。"""
        self.set_phase(f'verify:{task.task_key}')
        verify_rows = self.verify_task(task)
        verdict = self.review_task(task, verify_rows, b_summary)
        self.state.record_review(task.task_key, verdict['verdict'],
                                 verdict.get('provider', ''), verdict.get('model', ''),
                                 verdict.get('notes', ''))
        self.state.event(
            'task_review', task_key=task.task_key,
            verdict=verdict['verdict'], notes=verdict['notes'][:600],
            reviewer=f"{verdict.get('provider')}/{verdict.get('model')}",
            verify=[{'check': row['check'], 'ok': row['ok']} for row in verify_rows],
        )
        current = self.state.get_task(task.task_key)
        evidence = dict(current.evidence if current else {})
        evidence['verify_rows'] = verify_rows
        evidence['reviewed_by'] = f"{verdict.get('provider')}/{verdict.get('model')}"
        if verdict['verdict'] == 'PASS':
            self.state.update_task(
                task.task_key, phase='accepted', verdict='PASS',
                review_note=verdict['notes'],
                evidence=json.dumps(evidence, ensure_ascii=False)[:6000],
            )
            self.state.add_knowledge(task.task_key, task.title, 'PASS',
                                     verdict.get('knowledge') or verdict['notes'][:300])
            self.state.bump_meta('accepted_since_series', 1)
            log_line(self.state, f'任务 {task.task_key} 通过 Claude 审核')
            return
        if verdict['verdict'] == 'BLOCKED':
            self.state.update_task(
                task.task_key, phase='blocked', verdict='BLOCKED',
                review_note=verdict['notes'] or verdict['reason'],
                evidence=json.dumps(evidence, ensure_ascii=False)[:6000],
            )
            log_line(self.state, f'任务 {task.task_key} 被阻塞: {verdict["reason"][:200]}')
            return
        # rework_left = 还允许返工几次：先判断再扣减（max_rework=1 = 真给 1 次机会）
        allowed = int(current.rework_left if current else 0)
        evidence['feedback'] = (
            f"{verdict['notes']}\n\n验证结果："
            f"{json.dumps(verify_rows, ensure_ascii=False)[:1500]}"
        )
        if allowed <= 0:
            self.state.update_task(
                task.task_key, phase='blocked', rework_left=0,
                verdict='REWORK_EXHAUSTED', review_note=verdict['notes'],
                evidence=json.dumps(evidence, ensure_ascii=False)[:6000],
            )
            log_line(self.state, f'任务 {task.task_key} 返工次数用尽 → Claude 疑难升级')
            self.escalate(self.state.get_task(task.task_key) or task, verdict, verify_rows)
            return
        self.state.update_task(
            task.task_key, phase='pending', rework_left=allowed - 1,
            verdict='REWORK', review_note=verdict['notes'],
            evidence=json.dumps(evidence, ensure_ascii=False)[:6000],
        )
        log_line(self.state, f'任务 {task.task_key} 需要返工（剩余 {allowed - 1} 次）')

    def escalate(self, task, verdict: dict, verify_rows: list[dict]) -> list:
        """Claude 介入疑难：读代码找根因，产出带具体步骤的新任务（每个根任务最多 N 次）。"""
        root = str(task.card.get('escalated_from') or task.task_key)
        limit = int(self.config.limit('max_escalations_per_task', 1))
        used = int(float(self.state.get_meta(f'escalations:{root}', 0) or 0))
        if task.card.get('escalated_from') or used >= limit or self.dry_run:
            log_line(self.state, f'{task.task_key} 不再升级（已升级 {used}/{limit}）')
            return []
        self.state.set_meta(f'escalations:{root}', used + 1)
        try:
            response = self.call_agent(
                'agent_a', self.escalate_prompt(task, verdict, verify_rows),
                task_key=task.task_key, mode='plan', purpose='escalate',
            )
        except BudgetExceeded:
            raise
        if not response['ok']:
            self.state.event('escalate_failed', task_key=task.task_key,
                             error=str(response['error'])[:300])
            return []
        payload = extract_json_block(response['text']) or {}
        diagnosis = str(payload.get('diagnosis', ''))[:1500]
        decision = str(payload.get('decision', 'retry'))
        self.state.event('task_escalated', task_key=task.task_key, decision=decision,
                         diagnosis=diagnosis[:600])
        self.state.add_knowledge(task.task_key, task.title, f'ESCALATE:{decision}',
                                 str(payload.get('knowledge') or diagnosis)[:300])
        if decision == 'drop':
            self.state.update_task(task.task_key, review_note=f'Claude 疑难分析：放弃。{diagnosis}')
            return []
        cards = []
        for index, card in enumerate(payload.get('tasks') or []):
            if not isinstance(card, dict):
                continue
            card = dict(card)
            card['id'] = f'{root}-E{used + 1}{chr(97 + index) if index else ""}'
            card['escalated_from'] = root
            card['diagnosis'] = diagnosis
            card['max_rework'] = 1
            cards.append(card)
        return self._add_cards(cards, source='escalate')

    # ------------------------------------------------------ claude aux task
    def _aux_worktree(self) -> Path:
        return Path(str(self.config.raw.get('claude_aux_worktree')
                        or 'D:/3D/YYS2/oas-wt-claude-aux'))

    def _git(self, cwd: Path, *args: str, timeout: int = 300) -> tuple[int, str]:
        return safety.run_guarded(self.config, ['git', *args], timeout=timeout, cwd=cwd)

    def run_claude_aux(self, task) -> None:
        """Claude 辅助任务：独立 worktree 编辑 → 路径白名单 + 机器检查 → 合并回主分支。"""
        root = self.config.project_root
        aux = self._aux_worktree()
        branch = 'claude/aux'
        main_branch = context.git(root, 'rev-parse', '--abbrev-ref', 'HEAD').strip()
        if not aux.exists():
            code, out = self._git(root, 'worktree', 'add', '-B', branch, str(aux), 'HEAD')
            if code != 0:
                self._block(task, f'无法创建 Claude worktree: {out[-300:]}')
                return
        else:
            code, out = self._git(aux, 'merge', '--no-edit', main_branch)
            if code != 0:
                self._git(aux, 'merge', '--abort')
                self._block(task, f'Claude worktree 同步主分支冲突: {out[-300:]}')
                return
        base = context.git(aux, 'rev-parse', 'HEAD').strip()
        log_line(self.state, f'Claude 辅助任务 {task.task_key}（worktree {aux}）')
        response = self.call_agent('agent_a', self.aux_prompt(task), task_key=task.task_key,
                                   mode='acceptEdits', purpose='aux', cwd=aux)
        self.state.update_task(task.task_key, attempts=task.attempts + 1,
                               agent_b_summary=json.dumps(
                                   {'executor': 'claude aux', 'text': str(response['text'])[-2000:]},
                                   ensure_ascii=False)[:6000])
        self._git(aux, 'add', '-A')
        self._git(aux, 'commit', '-m', f'claude(aux): {task.task_key} {task.title[:40]}')
        changed = [line.strip() for line in context.git(
            aux, 'diff', '--name-only', base, 'HEAD').splitlines() if line.strip()]
        outside = [name for name in changed
                   if not name.replace('\\', '/').startswith(CLAUDE_AUX_PREFIXES)]
        if not response['ok'] or not changed or outside:
            reason = ('越界改动: ' + ', '.join(outside[:5])) if outside else (
                '没有产出文件' if not changed else f"调用失败: {response['error']}")
            self.state.record_review(task.task_key, 'REWORK', 'machine', '', reason)
            self._block(task, reason)
            return
        rows = self.verify_task(task, root=aux)
        if any(not row['ok'] for row in rows):
            self.state.record_review(task.task_key, 'REWORK', 'machine', '',
                                     json.dumps(rows, ensure_ascii=False)[:800])
            self._block(task, '机器检查未过: ' + json.dumps(
                [row for row in rows if not row['ok']], ensure_ascii=False)[:600])
            return
        ttl = float(self.config.limit('executor_lock_ttl_sec', 10800))
        ok, info = self.state.acquire_lock('executor', EXECUTOR_OWNER, ttl, task.task_key)
        if not ok:
            raise ExecutorBusy(info)
        try:
            safety.git_checkpoint(self.config, self.state,
                                  f'chore(agent): checkpoint before merge {task.task_key}')
            code, out = self._git(root, 'merge', '--no-ff', '-m',
                                  f'merge(claude-aux): {task.task_key}', branch)
            if code != 0:
                self._git(root, 'merge', '--abort')
                self._block(task, f'合并冲突，已中止合并: {out[-300:]}')
                return
        finally:
            self.state.release_lock('executor', EXECUTOR_OWNER)
        self.state.record_review(task.task_key, 'PASS', 'machine', '',
                                 f'merged {changed[:5]}')
        self.state.update_task(task.task_key, phase='accepted', verdict='MERGED',
                               review_note=f'Claude 辅助产出已合并: {changed[:5]}',
                               evidence=json.dumps({'verify_rows': rows, 'files': changed},
                                                   ensure_ascii=False)[:6000])
        self.state.add_knowledge(task.task_key, task.title, 'MERGED',
                                 f'Claude 辅助产出: {", ".join(changed[:3])}')
        log_line(self.state, f'Claude 辅助任务 {task.task_key} 已合并: {changed[:5]}')

    def _block(self, task, reason: str) -> None:
        self.state.update_task(task.task_key, phase='blocked', verdict='BLOCKED',
                               review_note=reason[:MAX_STATUS_REASON])
        log_line(self.state, f'任务 {task.task_key} 阻塞: {reason[:200]}')

    # -------------------------------------------------------- chess job
    def _series_started_24h(self) -> int:
        stamps = self.state.get_meta('chess_series_starts') or []
        cutoff = time.time() - 86400
        return len([stamp for stamp in stamps if float(stamp) >= cutoff])

    def maybe_start_chess_job(self) -> None:
        """空闲时启动连续 N 局评估：只有代码有新进展、验收需要实战证据时才跑。"""
        if self.dry_run or self.state.get_meta('chess_job'):
            return
        rows = {row['ac_id']: row['ok'] for row in self.state.acceptance_rows()}
        need = [ac for ac in ('AC06', 'AC13', 'AC14', 'AC15', 'AC16', 'AC17')
                if not rows.get(ac)]
        if not need:
            return
        first = not self.state.get_meta('chess_series_starts')
        progressed = int(float(self.state.get_meta('accepted_since_series', 0) or 0)) > 0
        if not (first or progressed):
            return
        if self._series_started_24h() >= int(self.config.safety.get('max_chess_series_24h', 4)):
            return
        lock = self.state.lock_info('executor')
        if lock and lock.get('owner') != EXECUTOR_OWNER:
            return  # DSH 的 DeepSeek 会话正在干活（可能在用模拟器），不抢
        if not safety.claim_chess_slot(self.config, self.state):
            return
        stamp = datetime.now().strftime('%m%d_%H%M%S')
        log_path = Path('D:/3D/YYS2/_recovery_070208') / f'chess_eval_{stamp}.log'
        games = str(self.config.safety.get('chess_series_games', 3))
        limit = str(self.config.safety.get('chess_series_time', '04:00:00'))
        args = [str(self.config.python), 'dev_tools/chess_series_test.py',
                'oas3', games, limit, '--log', str(log_path)]
        try:
            creation = (subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
                        if os.name == 'nt' else 0)
            process = subprocess.Popen(
                args, cwd=str(self.config.project_root),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=creation,
            )
        except OSError as error:
            log_line(self.state, f'实机评估启动失败: {error}')
            return
        sha = context.git(self.config.project_root, 'rev-parse', '--short', 'HEAD').strip()
        self.state.set_meta('chess_job', {
            'pid': process.pid, 'log': str(log_path), 'started': now_text(),
            'sha': sha, 'games': games, 'need': need,
            'deadline_epoch': time.time() + float(
                self.config.safety.get('chess_run_timeout_sec', 18000)),
        })
        starts = list(self.state.get_meta('chess_series_starts') or [])
        starts.append(time.time())
        self.state.set_meta('chess_series_starts', starts[-20:])
        self.state.set_meta('accepted_since_series', 0)
        self.state.event('chess_job_started', pid=process.pid, log=str(log_path), sha=sha)
        log_line(self.state, f'启动实机评估 {games} 局 pid={process.pid} sha={sha} log={log_path}')

    def poll_chess_job(self) -> None:
        job = self.state.get_meta('chess_job')
        if not job:
            return
        pid = int(job.get('pid') or 0)
        alive = self._pid_alive(pid)
        deadline = float(job.get('deadline_epoch') or 0)
        if alive and time.time() < deadline:
            return
        if alive:
            self._kill_pid(pid)
            self.state.event('chess_job_killed', pid=pid, reason='timeout')
        result = acceptance.check_chess_series(self.config, self.state)
        self.state.event('chess_job_finished', pid=pid, ok=result.ok,
                         detail=result.detail[:400])
        log_line(self.state, f'实机评估结束 ok={result.ok} {result.detail[:200]}')
        self.state.set_meta('chess_job', None)
        self.state.set_meta('metrics_checked_epoch', 0)  # 立刻刷新实战指标

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            completed = subprocess.run(
                ['tasklist', '/FI', f'PID eq {pid}', '/NH'],
                capture_output=True, text=True, encoding='utf-8',
                errors='replace', timeout=30,
            )
            return str(pid) in (completed.stdout or '')
        except (OSError, subprocess.SubprocessError):
            return False

    @staticmethod
    def _kill_pid(pid: int) -> None:
        try:
            subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'],
                           capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            pass

    # ----------------------------------------------------------- main loop
    def _pause_requested(self) -> bool:
        return (self.state.dir / 'PAUSE').exists()

    def _idle(self, seconds: float, reason: str) -> None:
        self.set_phase(f'idle:{reason}')
        self.state.heartbeat('running', {'phase': f'idle:{reason}'})
        end = time.time() + seconds
        while time.time() < end:
            if self._pause_requested():
                return
            time.sleep(min(15.0, max(0.0, end - time.time())))

    def _verifying_from_dsh(self):
        """DSH 会话 record 过、但执行锁已释放/过期的任务（没人审核）→ 由调度器补审核。"""
        lock = self.state.lock_info('executor')
        for task in self.state.open_tasks():
            if task.phase == 'verifying' and self._has_summary(task):
                if lock and lock.get('task_key') == task.task_key \
                        and lock.get('owner') != EXECUTOR_OWNER:
                    continue
                return task
        return None

    def loop(self, max_iterations: int = 400) -> str:
        """返回最终状态（completed / paused_* / blocked）。"""
        status = str(self.state.get_meta('status', 'pending') or 'pending')
        if status == 'completed':
            log_line(self.state, '状态已是 completed，直接退出')
            return 'completed'
        self.ensure_v2_session()
        self.state.set_meta('status', 'running')
        self.state.set_meta('resume_after', 0)
        self.state.bump_meta('resume_events', 1)
        self.start_heartbeat_thread()
        self.state.event('scheduler_start', session_start=self.session_start,
                         budget=self.budget_snapshot())
        consecutive_failures = int(self.state.get_meta('consecutive_failures', 0) or 0)
        max_failures = int(self.config.limit('max_consecutive_failures', 3))
        max_tasks = int(self.config.limit('max_tasks', 40))
        done_this_run = 0
        empty_plans = 0

        for _iteration in range(1, max_iterations + 1):
            task = None
            if self._pause_requested():
                self.state.set_meta('status', 'paused_manual')
                self.state.event('paused_manual', reason='PAUSE file')
                log_line(self.state, '检测到 PAUSE 文件 → 安全暂停（删除该文件后重启看门狗即可继续）')
                return 'paused_manual'
            try:
                self.check_budget()
            except BudgetExceeded as error:
                return self._pause_for_budget(error)

            self.state.heartbeat('running')
            self.poll_chess_job()

            if done_this_run >= max_tasks:
                self.state.set_meta('status', 'paused_limit')
                log_line(self.state, f'本次运行已处理 {done_this_run} 个任务，交给看门狗重启续跑')
                return 'paused_limit'

            try:
                lock = self.state.lock_info('executor')
                dsh_busy = bool(lock) and lock.get('owner') != EXECUTOR_OWNER
                if not dsh_busy:
                    task = self._verifying_from_dsh() or self.state.next_task()
                else:
                    # DeepSeek 会话占着主工作区：只做不改主工作区的事（Claude 辅助任务）
                    for candidate in self.state.open_tasks():
                        if str(candidate.card.get('owner')) == 'claude' \
                                and candidate.phase == 'pending':
                            task = candidate
                            break

                if task is None:
                    acceptance.snapshot(self.config, self.state, quick=True)
                    if acceptance.all_machine_passed(self.config, self.state):
                        self.set_phase('final_gate')
                        passed, missing = acceptance.final_gate(self.config, self.state)
                        if passed:
                            self.finish()
                            return 'completed'
                        log_line(self.state, f'全量复核未过: {missing}，继续规划')
                    self.maybe_start_chess_job()
                    if dsh_busy:
                        self._idle(180, f"等待 {lock.get('owner')} 释放执行锁")
                        continue
                    open_count = len(self.state.open_tasks())
                    planned = self.plan_tasks() if open_count == 0 else []
                    if not planned and open_count == 0:
                        empty_plans += 1
                        if self.state.get_meta('chess_job'):
                            self._idle(300, '等待实机评估产出证据')
                            continue
                        if empty_plans >= 2:
                            minutes = float(self.config.limit('no_task_pause_minutes', 45))
                            self.state.set_meta('status', 'blocked')
                            self.state.set_meta('resume_after', time.time() + minutes * 60)
                            self.state.event('blocked', reason='无可执行任务')
                            log_line(self.state, f'无可执行任务，{minutes:.0f} 分钟后由看门狗重试')
                            return 'blocked'
                    else:
                        empty_plans = 0
                    continue

                if str(task.card.get('owner', 'deepseek')) != 'claude':
                    foreign = self._foreign_activity()
                    if foreign:
                        # 主工作区有人在改：先做不碰主工作区的 Claude 辅助任务，没有再等
                        alt = next((item for item in self.state.open_tasks()
                                    if str(item.card.get('owner')) == 'claude'
                                    and item.phase == 'pending'), None)
                        if alt is None:
                            log_line(self.state, f'主工作区有未持锁的新改动 {foreign[:4]}，'
                                                 '等待对方提交/拿锁后再派活')
                            self._idle(150, 'foreign edits')
                            continue
                        log_line(self.state, f'主工作区有未持锁改动 {foreign[:2]}，'
                                             f'先做 Claude 辅助任务 {alt.task_key}')
                        task = alt
                self.run_task(task)
                done_this_run += 1
            except BudgetExceeded as error:
                return self._pause_for_budget(error)
            except EnvironmentPause as error:
                minutes = float(self.config.limit('env_pause_minutes', 20))
                self.state.set_meta('status', 'paused_env')
                self.state.set_meta('resume_after', time.time() + minutes * 60)
                self.state.set_meta('pause_reason', str(error))
                self.state.set_meta('env_failures', 0)
                self.state.event('paused_env', reason=str(error))
                log_line(self.state, f'环境故障暂停 {minutes:.0f} 分钟: {error}')
                return 'paused_env'
            except ExecutorBusy as error:
                log_line(self.state, str(error))
                self._idle(120, 'executor busy')
                continue
            except (AgentError, safety.SafetyError) as error:
                consecutive_failures += 1
                self.state.set_meta('consecutive_failures', consecutive_failures)
                self.state.event('task_error', task_key=getattr(task, 'task_key', ''),
                                 error=str(error)[:600])
                log_line(self.state, f'任务异常 {getattr(task, "task_key", "")}: {error}')
                if consecutive_failures >= max_failures:
                    self.state.set_meta('status', 'blocked')
                    self.state.set_meta('resume_after', time.time() + 1800)
                    return 'blocked'
                continue

            updated = self.state.get_task(task.task_key)
            if updated and updated.phase == 'accepted':
                consecutive_failures = 0
                self.state.set_meta('consecutive_failures', 0)
            elif updated and updated.phase != 'pending':
                consecutive_failures += 1
                self.state.set_meta('consecutive_failures', consecutive_failures)
            if updated and updated.phase == 'pending' and updated.attempts >= 4:
                # 防无限循环：同一任务尝试 4 次仍未结束 → 阻塞（并已由 finalize 触发过升级）
                self.state.update_task(
                    task.task_key, phase='blocked', rework_left=0, verdict='FAILED_LOOP',
                    review_note=f'尝试 {updated.attempts} 次仍未通过，自动跳过',
                )
                log_line(self.state, f'任务 {task.task_key} 尝试次数过多，标记 blocked 后继续')
            if consecutive_failures >= max_failures:
                log_line(self.state, f'连续 {consecutive_failures} 个任务未通过，'
                                     '计数清零后继续（单个难题不终止整轮）')
                consecutive_failures = 0
                self.state.set_meta('consecutive_failures', 0)

            acceptance.snapshot(self.config, self.state, quick=True)
            self.state.heartbeat('running')

        self.state.set_meta('status', 'paused_limit')
        log_line(self.state, '达到迭代上限，交给看门狗重启续跑')
        return 'paused_limit'

    # -------------------------------------------------------------- finish
    def finish(self) -> None:
        client_pending = acceptance.human_pending()
        tag = f"stable-v2-{datetime.now().strftime('%Y%m%d-%H%M')}"
        checkpoint = safety.git_checkpoint(self.config, self.state,
                                           'chore(agent): final acceptance passed', tag=tag)
        self.state.set_meta('status', 'completed')
        self.state.set_meta('completed_at', now_text())
        self.state.event('completed', human_pending=client_pending, tag=checkpoint.get('tag'))
        self.write_final_report(client_pending)
        report = acceptance.check_final_report(self.config, self.state)
        self.state.record_acceptance(report.ac_id, report.title, report.ok, report.detail)
        log_line(self.state, f'全部强制验收项全量复核通过 → completed（tag {checkpoint.get("tag")}）')

    def write_final_report(self, human_pending: list[dict]) -> None:
        rows = self.state.acceptance_rows()
        totals = self.state.run_totals(since=self.session_start)
        claude = self.state.run_totals(since=self.session_start, provider='anthropic')
        deepseek = self.state.run_totals(since=self.session_start, provider='deepseek')
        lines = [
            '# 双 Agent 自动开发（调度器 v2）：最终报告', '',
            f'- 生成时间: {now_text()}',
            f'- v2 会话开始: {self.session_start}',
            f"- Claude: {claude['calls']} 次调用，${claude['cost_usd']}",
            f"- DeepSeek: {deepseek['calls']} 次调用，¥{deepseek['cost_cny']}",
            f"- Token: input={totals['tokens_input']} cache_read={totals['tokens_cached']} "
            f"cache_write={totals['tokens_cache_write']} output={totals['tokens_output']}",
            '', '## 机器验收（完成前全量复核）', '',
            '| 项 | 结果 | 说明 |', '| --- | --- | --- |',
        ]
        for row in rows:
            lines.append(
                f'| {row["ac_id"]} {row["title"]} | {"PASS" if row["ok"] else "FAIL"} | '
                f'{str(row["detail"]).replace("|", "/")[:300]} |'
            )
        lines += ['', '## 任务流水（token / 费用 / 返工）', '',
                  '| 任务 | 状态 | 判定 | 调用 | ¥Claude | ¥DeepSeek | 返工 |',
                  '| --- | --- | --- | --- | --- | --- | --- |']
        for row in self.state.task_cost_table(200):
            lines.append(
                f"| {row['task_key']} | {row['phase']} | {row['verdict'] or '-'} | "
                f"{row['calls']} | {row['a_cny']:.2f} | {row['b_cny']:.2f} | {row['reworks']} |"
            )
        compare = self.state.dir / 'strategy_compare.md'
        if compare.exists():
            lines += ['', '## 策略对照', '', compare.read_text(encoding='utf-8')]
        lines += ['', '## 需要人工复核', '']
        lines += [f'- {item["id"]}: {item["title"]}' for item in human_pending]
        lines += [
            '', '## 剩余限制',
            '- 名次提升无法由脚本证明，只给过程指标对照（人工判断 H1）。',
            '- 电脑休眠/关机/断网期间不运行，靠 state.db 持久化恢复。',
        ]
        report = self.state.dir / 'FINAL_REPORT.md'
        report.write_text('\n'.join(lines), encoding='utf-8')
        log_line(self.state, f'最终报告已写入 {report}')


def main() -> int:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description='OAS 双 Agent 自动开发调度器 v2')
    parser.add_argument('--dry-run', action='store_true', help='不调用模型，只跑状态机')
    parser.add_argument('--tasks', type=int, default=0, help='最多执行多少个任务后退出')
    parser.add_argument('--once', action='store_true', help='只跑一轮循环')
    parser.add_argument('--hours', type=float, default=0, help='覆盖本次运行时长上限')
    parser.add_argument('--review-task', default='',
                        help='对指定任务只跑 verify+review（崩溃/中断后恢复用）')
    args = parser.parse_args()

    config = load_config()
    if args.hours:
        config.raw['budget']['max_hours'] = float(args.hours)
    state = State(config.state_dir, config.project_root)
    scheduler = Scheduler(config, state, dry_run=args.dry_run)

    log_line(state, '=== 调度器 v2 启动 ===')
    log_line(state, f'预算: {json.dumps(scheduler.budget_snapshot(), ensure_ascii=False)}')
    if scheduler.agent:
        log_line(state, f'CLI: A={scheduler.agent.clis["agent_a"]} B={scheduler.agent.clis["agent_b"]}')

    try:
        if args.review_task:
            task = state.get_task(args.review_task)
            if task is None:
                log_line(state, f'找不到任务 {args.review_task}')
                return 1
            log_line(state, f'恢复审核 {task.task_key}')
            scheduler.finalize_task(task, scheduler._stored_summary(task))
        elif args.tasks:
            scheduler.ensure_v2_session()
            for _ in range(args.tasks):
                task = state.next_task()
                if task is None:
                    planned = scheduler.plan_tasks()
                    if not planned:
                        break
                    task = planned[0]
                scheduler.run_task(task)
        elif args.once:
            task = state.next_task()
            if task is not None:
                scheduler.run_task(task)
        else:
            status = scheduler.loop()
            log_line(state, f'=== 调度器退出，状态={status} ===')
    except KeyboardInterrupt:
        state.set_meta('status', 'paused_manual')
        log_line(state, '收到中断，保存状态后退出')
    finally:
        scheduler.stop_heartbeat_thread()
        state.release_lock('executor', EXECUTOR_OWNER)
        state.heartbeat('stopped')
        state.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
