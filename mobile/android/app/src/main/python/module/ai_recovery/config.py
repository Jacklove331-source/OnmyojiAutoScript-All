# This Python file uses the following encoding: utf-8

"""AI 自愈层配置。

配置来源（优先级从高到低）：
1. 环境变量（少量开关：OAS_AI_ENABLED / OAS_AI_MODE / OAS_AI_MODEL / DEEPSEEK_API_KEY）
2. ``config/ai_recovery.yaml``（该路径被 .gitignore 的 ``config/*.yaml`` 覆盖，不进 Git）
3. 内置默认值（默认关闭）

密钥只从环境变量或本地密钥文件读取；``AiRecoveryConfig`` 的 repr/日志里永远不出现密钥。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - 仓库自带环境有 PyYAML
    yaml = None

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = ROOT / 'config' / 'ai_recovery.yaml'
DEFAULT_KEY_FILE = ROOT / 'config' / 'ai_recovery.key'
# Android：App 私有 files 目录由 Flutter 设置页写入，APK 内的 config/ 只读。
_ANDROID_FILES = Path('/data/user/0/com.oas.oasx/files')
if _ANDROID_FILES.is_dir():
    DEFAULT_CONFIG_PATH = _ANDROID_FILES / 'ai_recovery.yaml'
    DEFAULT_KEY_FILE = _ANDROID_FILES / 'ai_recovery.key'


def _env_bool(name: str, default: bool | None = None) -> bool | None:
    raw = os.environ.get(name)
    if raw is None or raw == '':
        return default
    return raw.strip().lower() in {'1', 'true', 'yes', 'on', 'y'}


def _env_str(name: str, default: str | None = None) -> str | None:
    raw = os.environ.get(name)
    return raw if raw not in (None, '') else default


@dataclass
class ApiConfig:
    base_url: str = 'https://api.deepseek.com'
    model: str = 'deepseek-flash'
    reasoning_effort: str = 'low'
    timeout_s: float = 75.0
    max_retries: int = 2
    max_tokens: int = 900
    temperature: float = 0.0
    image_max_side: int = 960
    image_quality: int = 68
    # 未知界面才发全屏；已知按钮异常优先发目标区域
    send_full_screen: bool = True
    # 供脱敏：截图顶部状态栏（时间/电量）裁掉，避免无关信息进入 API
    redact_top_px: int = 24
    key_env: str = 'DEEPSEEK_API_KEY'
    key_file: str = 'config/ai_recovery.key'


@dataclass
class BudgetConfig:
    per_incident_calls: int = 2
    per_hour_calls: int = 12
    per_day_calls: int = 60
    per_day_cny: float = 3.0
    # 价格表（元 / 百万 token），保守取整
    price_input_cny_per_mtok: float = 2.0
    price_cached_input_cny_per_mtok: float = 0.5
    price_output_cny_per_mtok: float = 8.0


@dataclass
class DetectorConfig:
    # 任务默认超时（秒）。任务名匹配不到时用 default。
    task_timeouts_s: dict[str, float] = field(default_factory=lambda: {
        'default': 300.0,
        'Restart': 300.0,
        'Chess': 300.0,
        'WantedQuests': 240.0,
        'DraftDuel': 300.0,
        'AreaBoss': 240.0,
    })
    # 画面在同一页面完全静止多久算“无进展”
    stale_frame_s: float = 90.0
    # 未知页面持续多久触发（加载/战斗会有动画，不会触发）
    unknown_page_s: float = 45.0
    # 同一坐标点击多少次无变化算“点击无效”
    repeated_click_n: int = 4
    repeated_click_window_s: float = 30.0
    # 两个页面来回跳多少次算点击循环
    page_loop_n: int = 8
    page_loop_window_s: float = 90.0
    # 同一异常指纹的冷却时间，防止反复接管
    cooldown_s: float = 180.0
    # 同一异常指纹最多接管次数（进程内）
    max_incidents_per_key: int = 3
    # 两帧相似度阈值（0~1，越大越像）
    similar_ratio: float = 0.985
    # 判定“画面发生了明显变化”的阈值
    changed_ratio: float = 0.02


@dataclass
class SafetyConfig:
    max_actions: int = 4
    max_click_per_point: int = 2
    max_incident_seconds: float = 45.0
    verify_wait_s: float = 1.2
    verify_retries: int = 2
    # 点击后画面变化低于该比例视为“没生效”
    verify_change_ratio: float = 0.012
    # 屏幕边缘安全边距（像素）
    edge_margin_px: int = 12
    # 禁点矩形（归一化 x,y,w,h）：默认避开右上角充值/公告、右下角菜单区
    forbidden_rects: list = field(default_factory=lambda: [
        [0.72, 0.0, 0.28, 0.10],
        [0.90, 0.86, 0.10, 0.14],
    ])
    # 目标附近出现这些文字时禁止点击（充值/消费/删除/退游等不可逆操作）
    forbidden_keywords: list[str] = field(default_factory=lambda: [
        '充值', '支付', '购买', '兑换', '删除', '分解', '出售', '退出游戏', '确认退出',
        '注销', '解绑', '修改密码', '设置', '恢复出厂', '清除数据',
    ])
    # 目标点周围多大范围内做文字安全扫描（像素）
    danger_scan_radius_px: int = 90
    # 危险文字附近仍允许点击的安全措辞（取消购买/关闭弹窗这类）
    safe_keywords: list[str] = field(default_factory=lambda: [
        '取消', '关闭', '返回', '放弃', '不用', '以后', '否', '不了', '稍后',
    ])
    allow_goto_page: bool = True
    # 低置信度直接停手（绝不盲点）
    min_confidence: float = 0.60
    # 高风险判定一律不执行
    allow_high_risk: bool = False


@dataclass
class KnowledgeConfig:
    enabled: bool = True
    min_success_rate: float = 0.80
    max_age_days: float = 14.0
    # 哈希相似度（汉明距离）阈值，越小越严
    max_ham_distance: int = 12
    path: str = 'log/ai_recovery/knowledge.jsonl'


@dataclass
class TemplateConfig:
    enabled: bool = True
    dir: str = 'assets/ai_templates'
    min_match_score: float = 0.88
    min_negative_margin: float = 0.06
    max_false_positive: float = 0.02
    min_positive_samples: int = 1
    # 默认只写候选库，不覆盖正式图色资源；自动模式下通过验证才 apply
    auto_apply_to_source: bool = False
    apply_to_source_in_auto: bool = False
    # 允许的单模板最大历史版本数
    max_versions: int = 8


@dataclass
class AiRecoveryConfig:
    enabled: bool = False
    mode: str = 'observe'  # observe | auto
    api: ApiConfig = field(default_factory=ApiConfig)
    budget: BudgetConfig = field(default_factory=BudgetConfig)
    detector: DetectorConfig = field(default_factory=DetectorConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    knowledge: KnowledgeConfig = field(default_factory=KnowledgeConfig)
    templates: TemplateConfig = field(default_factory=TemplateConfig)
    config_name: str = 'oas'
    source_path: str = ''
    # 不参与 asdict 的运行时密钥
    api_key: str = field(default='', repr=False, compare=False)

    # ---------- 序列化 ----------

    def public_dict(self) -> dict:
        data = asdict(self)
        data.pop('api_key', None)
        data['api']['key_file'] = self.api.key_file
        data['has_api_key'] = bool(self.api_key)
        return data

    @property
    def auto(self) -> bool:
        return self.mode.lower() == 'auto'

    @property
    def task_timeouts(self) -> dict[str, float]:
        return self.detector.task_timeouts_s

    def timeout_for(self, task: str) -> float:
        return float(self.task_timeouts.get(task, self.task_timeouts.get('default', 300.0)))

    def resolve_api_key(self) -> str:
        """按 环境变量 → Android/本地密钥文件 的顺序取密钥；永不打印内容。"""
        value = os.environ.get(self.api.key_env, '').strip()
        if value:
            return value
        key_file = Path(self.api.key_file)
        candidates: list[Path] = []
        if key_file.is_absolute():
            candidates.append(key_file)
        else:
            candidates.append(DEFAULT_KEY_FILE)
            cfg_p = config_path()
            candidates.append(cfg_p.parent / key_file.name)
            candidates.append(cfg_p.parent.parent / key_file)
            candidates.append(ROOT / key_file)
        for cand in candidates:
            try:
                if cand.exists():
                    for line in cand.read_text(encoding='utf-8').splitlines():
                        line = line.strip()
                        if line and not line.startswith('#'):
                            return line
            except OSError:
                continue
        return ''

    # ---------- 加载 ----------

    @classmethod
    def from_dict(cls, data: dict, *, config_name: str = 'oas', source_path: str = '') -> 'AiRecoveryConfig':
        cfg = cls(config_name=config_name, source_path=source_path)
        if not isinstance(data, dict):
            return cfg
        cfg.enabled = bool(data.get('enabled', cfg.enabled))
        cfg.mode = str(data.get('mode', cfg.mode)).lower().strip() or 'observe'
        _merge_dataclass(cfg.api, data.get('api') or {})
        _merge_dataclass(cfg.budget, data.get('budget') or {})
        _merge_dataclass(cfg.detector, data.get('detector') or {})
        _merge_dataclass(cfg.safety, data.get('safety') or {})
        _merge_dataclass(cfg.knowledge, data.get('knowledge') or {})
        _merge_dataclass(cfg.templates, data.get('templates') or {})
        cfg.normalize()
        return cfg

    def normalize(self) -> None:
        """把配置限制在安全范围，防止手改 yaml 造成危险值。"""
        self.mode = self.mode if self.mode in ('observe', 'auto') else 'observe'
        self.api.timeout_s = max(5.0, min(float(self.api.timeout_s), 300.0))
        self.api.max_retries = max(0, min(int(self.api.max_retries), 4))
        self.api.max_tokens = max(200, min(int(self.api.max_tokens), 4000))
        self.api.image_max_side = max(480, min(int(self.api.image_max_side), 1600))
        self.api.image_quality = max(40, min(int(self.api.image_quality), 90))
        self.budget.per_incident_calls = max(0, min(int(self.budget.per_incident_calls), 6))
        self.budget.per_hour_calls = max(0, min(int(self.budget.per_hour_calls), 120))
        self.budget.per_day_calls = max(0, min(int(self.budget.per_day_calls), 600))
        self.budget.per_day_cny = max(0.0, float(self.budget.per_day_cny))
        self.safety.max_actions = max(1, min(int(self.safety.max_actions), 8))
        self.safety.max_click_per_point = max(1, min(int(self.safety.max_click_per_point), 3))
        self.safety.min_confidence = max(0.0, min(float(self.safety.min_confidence), 1.0))
        self.safety.edge_margin_px = max(0, min(int(self.safety.edge_margin_px), 80))
        self.detector.cooldown_s = max(0.0, float(self.detector.cooldown_s))
        self.detector.stale_frame_s = max(10.0, float(self.detector.stale_frame_s))
        self.detector.unknown_page_s = max(5.0, float(self.detector.unknown_page_s))
        self.templates.min_match_score = max(0.5, min(float(self.templates.min_match_score), 0.99))
        self.templates.max_false_positive = max(0.0, min(float(self.templates.max_false_positive), 0.5))
        if not isinstance(self.safety.forbidden_rects, list):
            self.safety.forbidden_rects = []


def _merge_dataclass(target, values: dict) -> None:
    if not isinstance(values, dict):
        return
    for key, value in values.items():
        if not hasattr(target, key) or key.startswith('_'):
            continue
        current = getattr(target, key)
        if isinstance(current, dict) and isinstance(value, dict):
            current.update(value)
        elif isinstance(current, list) and isinstance(value, list):
            setattr(target, key, value)
        elif isinstance(current, bool):
            setattr(target, key, bool(value))
        elif isinstance(current, int) and not isinstance(current, bool) and isinstance(value, (int, float)):
            setattr(target, key, int(value))
        elif isinstance(current, float) and isinstance(value, (int, float)):
            setattr(target, key, float(value))
        elif isinstance(current, str):
            setattr(target, key, str(value))


_cached: dict[str, tuple[float, AiRecoveryConfig]] = {}


def config_path() -> Path:
    return Path(os.environ.get('OAS_AI_CONFIG') or DEFAULT_CONFIG_PATH)


def load_config(*, config_name: str = 'oas', path: Path | None = None, force: bool = False) -> AiRecoveryConfig:
    """加载配置；同一路径 + mtime 内缓存，避免每帧解析 YAML。"""
    target = Path(path) if path else config_path()
    try:
        mtime = target.stat().st_mtime if target.exists() else 0.0
    except OSError:
        mtime = 0.0
    cache_key = f'{target}|{config_name}|{os.environ.get("OAS_AI_ENABLED", "")}|{os.environ.get("OAS_AI_MODE", "")}'
    cached = _cached.get(cache_key)
    if cached and not force and cached[0] == mtime:
        return cached[1]

    data: dict[str, Any] = {}
    if target.exists():
        try:
            if yaml is not None:
                loaded = yaml.safe_load(target.read_text(encoding='utf-8'))
                if isinstance(loaded, dict):
                    data = loaded
        except Exception:  # noqa: BLE001 - 配置坏掉时退回默认值，不能影响任务
            data = {}
    cfg = AiRecoveryConfig.from_dict(data, config_name=config_name, source_path=str(target))

    enabled_env = _env_bool('OAS_AI_ENABLED')
    if enabled_env is not None:
        cfg.enabled = enabled_env
    mode_env = _env_str('OAS_AI_MODE')
    if mode_env:
        cfg.mode = mode_env.strip().lower()
    model_env = _env_str('OAS_AI_MODEL')
    if model_env:
        cfg.api.model = model_env.strip()
    base_env = _env_str('OAS_AI_BASE_URL')
    if base_env:
        cfg.api.base_url = base_env.strip()
    cfg.normalize()
    cfg.api_key = cfg.resolve_api_key()
    _cached[cache_key] = (mtime, cfg)
    return cfg


def get_config(config_name: str = 'oas') -> AiRecoveryConfig:
    return load_config(config_name=config_name)


def write_default_config(path: Path | None = None, *, enabled: bool = False) -> Path:
    """在首次需要时生成一份带注释的默认配置（不覆盖已存在的文件）。"""
    target = Path(path) if path else config_path()
    if target.exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    template = f"""# OAS AI 智能自愈层配置（DeepSeek）
# 本文件被 .gitignore 覆盖，不会提交；密钥请放在环境变量 DEEPSEEK_API_KEY
# 或同目录 ai_recovery.key（单行，勿提交）。
enabled: {str(enabled).lower()}
mode: observe        # observe=只分析不点击；auto=允许执行安全动作
api:
  base_url: https://api.deepseek.com
  model: deepseek-flash
  reasoning_effort: low   # low 更省 token；疑难界面可临时改 high
  timeout_s: 75
  max_retries: 2
  max_tokens: 900
  image_max_side: 960
  image_quality: 68
  send_full_screen: true
budget:
  per_incident_calls: 2
  per_hour_calls: 12
  per_day_calls: 60
  per_day_cny: 3.0
detector:
  stale_frame_s: 90
  unknown_page_s: 45
  repeated_click_n: 4
  cooldown_s: 180
safety:
  max_actions: 4
  max_click_per_point: 2
  min_confidence: 0.6
knowledge:
  enabled: true
templates:
  enabled: true
  auto_apply_to_source: false
"""
    target.write_text(template, encoding='utf-8')
    return target
