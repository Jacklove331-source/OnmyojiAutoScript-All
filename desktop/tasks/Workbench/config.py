# This file uses the following encoding: utf-8
"""Workbench 任务的配置（会出现在配置页的 Script 任务列表里）。"""
from pydantic import BaseModel, Field

from tasks.Component.config_base import ConfigBase
from tasks.Component.config_scheduler import Scheduler


class WorkbenchOptions(BaseModel):
    # 数据来源：json=导入档案文件，screen=从设备截图 OCR
    # paz-db=直读平安志库（权威、含固有属性）；json=本地档案；screen=读屏；paz-snapshot=raw 数组
    source: str = Field(default='paz-db', description='workbench_source_help')
    paz_db_path: str = Field(default='', description='workbench_paz_db_help')
    paz_snapshot_file: str = Field(default='config/workbench/paz-snapshot.json',
                                   description='workbench_paz_snapshot_help')
    # 导入/读取用到的文件（相对仓库根目录或绝对路径）
    profile_file: str = Field(default='config/workbench/profile.json',
                              description='workbench_profile_file_help')
    souls_file: str = Field(default='config/workbench/souls.json',
                            description='workbench_souls_file_help')
    owned_file: str = Field(default='config/workbench/owned.json',
                            description='workbench_owned_file_help')
    # 评分标准与阈值
    score_standard: str = Field(default='builtin.score.low', description='workbench_standard_help')
    keep_threshold: int = Field(default=0, ge=0, le=100, description='workbench_keep_help')
    observe_threshold: int = Field(default=0, ge=0, le=100, description='workbench_observe_help')
    # 输出
    export_dir: str = Field(default='log/workbench', description='workbench_export_help')
    # 分析面板时用哪个式神（留空取第一个已拥有）
    panel_shikigami: str = Field(default='', description='workbench_panel_shikigami_help')


class Workbench(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    workbench: WorkbenchOptions = Field(default_factory=WorkbenchOptions)
