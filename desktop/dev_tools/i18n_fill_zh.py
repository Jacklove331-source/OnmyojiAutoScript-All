# This Python file uses the following encoding: utf-8

"""补齐 module/config/i18n/zh-CN.json 里的中文（任务名 + 配置说明）。"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
I18N = ROOT / 'module/config/i18n/zh-CN.json'

TASK_NAMES = {
    'AutoCheckinBigGod': '大神签到',
    'BudokaiTournament': '斗技大会',
    'Chess': '百鬼棋局',
    'DraftDuel': '协同对弈',
    'FrogChallenge': '青蛙瓷器挑战赛',
    'GuguArtStudio': '咕咕画室',
    'GuildActivityMonitor': '寮活动监控',
    'IbukiArena': '伊吹之擂',
    'LBS': '线下庆典',
    'MartialArts': '演武',
    'OtherWorldTwilight': '彼世逢魔',
    'WeeklyPurchase': '功勋商店',
}

HELP = {
    # 自动阵容
    'auto_lineup_enable_help': '自动阵容总开关：任务开始前自动在游戏「阵容助手」里选好阵容并应用。',
    'auto_lineup_mode_help': 'official=官方阵容（游戏自带御魂，逐套试算，缺式神/御魂自动跳过）；ai=按你的式神池、御魂库存和联网资料算一套新阵容。',
    'auto_lineup_team_group_help': '新建/保存的队伍统一放到这个分组（默认「阵容」），方便和手动配置区分。',
    'auto_lineup_assistant_dungeon_help': '可选，指定阵容助手要筛选的副本，格式如「御魂/八岐大蛇/神罚」；留空则用任务内置映射。',
    'auto_lineup_source_help': '阵容来源优先级：official=官方、community=社区、mine=我的，按顺序取第一个可用的。',
    'auto_lineup_strategy_help': '选择策略：stable=稳定优先、fast=效率优先、fit=练度适配优先。',
    'auto_lineup_match_help': '匹配方式：dungeon=按副本匹配、team=按队伍匹配。',
    'auto_lineup_apply_help': '应用方式：preset=只切队伍预设、assistant=只走游戏阵容助手、both=两者都做。',
    'auto_lineup_overwrite_help': '同名队伍已存在时是否覆盖；关闭则自动加后缀另存。',
    'auto_lineup_roster_file_help': '式神池数据文件路径，默认 config/roster.json（由式神池扫描生成）。',
    'auto_lineup_min_score_help': '阵容最低评分阈值，低于该分数不采用。',
    'auto_lineup_data_file_help': '阵容知识库文件路径（式神/御魂/阵容数据）。',
    'auto_lineup_web_url_help': '联网攻略/版本资料的抓取地址；留空则只用本地知识库。',
    'auto_lineup_web_cache_help': '联网资料缓存小时数，过期后重新抓取。',
    'auto_lineup_default_group_help': '默认预设分组编号（兼容旧配置使用）。',
    'auto_lineup_default_team_help': '默认预设队伍编号（兼容旧配置使用）。',
    'auto_lineup_ignore_task_config_help': '自动阵容生效时，忽略任务里自带的「预设队伍」和「任务前切换御魂」，统一由自动阵容决定。',
    # 式神池扫描
    'roster_scan_enable_help': '每周自动扫描一次式神池（式神、等级、技能、御魂速度等），结果写入文件供自动阵容使用。',
    'roster_scan_weekday_help': '在星期几执行扫描（1=周一 … 7=周日）。',
    'roster_scan_time_help': '扫描开始时间。',
    'roster_scan_last_help': '上次扫描时间（自动记录，不需要手动改）。',
    'roster_scan_output_help': '扫描结果输出路径，默认 config/roster.json。',
    'roster_scan_rarities_help': '要记录的稀有度范围：SSR/SR/R/SP。',
    'roster_scan_level_help': '是否记录式神等级。',
    'roster_scan_skills_help': '是否记录技能等级（影响阵容强度判断）。',
    'roster_scan_speed_help': '是否记录速度（影响配速判断）。',
    'roster_scan_pages_help': '每轮扫描的页数，页数越多越慢但更完整。',
    'roster_scan_capture_help': '是否保存截图作为扫描证据，便于事后核对。',
    # 协同对弈
    'draft_duel_role_help': '本账号在协同对弈里的身份：leader=队长（负责两端操作、按轮数推进），member=队员（跟随队长节奏）；选 unassigned 时只做大厅检查。',
    'draft_duel_partner_config_help': '搭档那台设备的 OAS 配置名（例如另一台是 oas3 就填 oas3），用于两边共享设置、同步进度。',
    'draft_duel_teammate_name_help': '搭档在游戏里的昵称，用于组队邀请和配对确认。',
    'draft_duel_friend_tab_help': '从哪个好友列表邀请搭档：好友/最近/寮友/跨区，默认跨区。',
    'draft_duel_challenge_limit_help': '要完成多少「完整轮」：达到 6 胜或 2 败、双方结算并返回后才算 1 轮，不按单场计数。',
    'draft_duel_limit_time_help': '单次运行的时间上限，超时会保存断点，下次接着跑未完成的轮。',
    'draft_duel_coin_budget_help': '历史字段，当前版本不用它限制开轮，保留仅为兼容旧配置。',
    'draft_duel_free_only_help': '历史字段，旧版只跑免费次；当前版本不再生效。',
    'draft_duel_partner_wait_seconds_help': '等待搭档上线/进队的最长秒数，超时结束本次任务并记录原因。',
    'draft_duel_phase_timeout_seconds_help': '单个阶段（组队、选将、上阵等）的最长等待秒数，超时自动重试或重新识别。',
    'matchmaking_timeout_seconds_help': '匹配阶段的最长等待秒数，超时后重新发起匹配。',
    # 优化/调度
    'close_game_wait_duration_help': '距离下一个任务超过此分钟数时关闭游戏；设为 0 表示禁用。',
    'close_emulator_wait_duration_help': '距离下一个任务超过此分钟数时关闭模拟器；设为 0 表示禁用。错峰模式下空闲超过 3 分钟也会直接关模拟器，任务前自动拉起。',
    'emulator_startup_lead_time_help': '任务开始前提前多久拉起模拟器，避免开局卡顿。',
    'queue_mode_help': '排队模式：多个实例轮流执行任务，避免同时跑造成压力。',
    'queue_idle_threshold_help': '排队模式下释放执行权的空闲阈值（分钟），仅排队模式生效。',
    'stagger_enable_help': '错峰执行：同组配置任务级互斥，跑完一个立刻交接，避免两个号同时执行。',
    'stagger_group_help': '错峰分组：逗号分隔的配置名，例如 oas1,oas3。',
    'stagger_gap_minutes_help': '错峰最小间隔（分钟），0 表示跑完立刻让给同组另一个实例。',
    # 桌面版
    'desktop_enable_help': '启用桌面版（PC 客户端）支持，不再依赖模拟器。',
    'game_path_help': '游戏客户端安装路径。',
    'launch_instance_help': '要启动的客户端实例序号（多开时用）。',
    'profile_dir_help': '客户端配置目录（存档/设置所在位置）。',
    'window_pos_help': '游戏窗口的屏幕坐标位置。',
    'window_title_help': '用于定位游戏窗口的标题关键字。',
    # 防封
    'antiban_enable_help': '防封总开关：按计划安排活跃与休息时间。',
    'daily_active_limit_help': '每天允许活跃的最长时长。',
    'long_rest_duration_help': '长时间休息的持续时长。',
    'sleep_start_help': '每日休息开始时间。',
    'sleep_end_help': '每日休息结束时间。',
    # 单条
    'check_true_orochi_enable_help': '是否启用真·八岐大蛇（真蛇）副本。',
    'thirteen_switch_help': '是否开启十三层（更高层数）挑战。',
    'min_run_interval_help': '两次运行之间的最小间隔（分钟）。',
}


def main() -> None:
    data = json.loads(I18N.read_text(encoding='utf-8'))
    before = len(data)
    merged = 0
    for table in (TASK_NAMES, HELP):
        for key, value in table.items():
            if key not in data:
                data[key] = value
                merged += 1
    I18N.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8',
    )
    print(f'keys {before} -> {len(data)} (+{merged})')


if __name__ == '__main__':
    main()
