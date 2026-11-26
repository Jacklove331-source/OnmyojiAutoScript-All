# This file uses the following encoding: utf-8
"""全局配置：阴阳师工作台（平安志功能移植模块）。

只做本地数据与计算：导入/导出档案、御魂评分、面板计算、速度预览、头尾脚排名。
字段全部扁平，符合 OAS 配置页生成器的约束。
"""
from pydantic import BaseModel, Field


class Workbench(BaseModel):
    """阴阳师工作台：本地档案与数值分析。"""

    enable: bool = Field(default=True, description='workbench_enable_help')
    # 档案文件（相对仓库根目录或绝对路径）
    profile_file: str = Field(default='config/workbench/profile.json',
                              description='workbench_profile_file_help')
    souls_file: str = Field(default='config/workbench/souls.json',
                            description='workbench_souls_file_help')
    # 评分标准包 id，留空用包内第一套
    source_kind: str = Field(default='paz-db', description='workbench_source_help')
    score_standard: str = Field(default='builtin.score.low',
                                description='workbench_standard_help')
    # 练度档：auto 按御魂数量自动判定，其余强制
    maturity_mode: str = Field(default='auto', description='workbench_maturity_help')
    # 决策阈值覆盖（0 表示用规则自带的 keepScore / observeScore）
    keep_threshold: int = Field(default=0, ge=0, le=100, description='workbench_keep_help')
    observe_threshold: int = Field(default=0, ge=0, le=100, description='workbench_observe_help')
    # 输出
    export_dir: str = Field(default='log/workbench', description='workbench_export_help')
    # 是否在导入时顺便抓一份游戏内截图作为证据
    capture_evidence: bool = Field(default=False, description='workbench_capture_help')
    last_run: str = Field(default='', description='workbench_last_run_help')
