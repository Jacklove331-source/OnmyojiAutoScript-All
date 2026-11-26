# This file uses the following encoding: utf-8
"""全局配置：式神池自动扫描（每周一次，可自定时间）。"""
from pydantic import BaseModel, Field

from tasks.Component.config_base import Time


class RosterScan(BaseModel):
    """每周自动扫描式神录，更新 config/roster.json。"""

    enable: bool = Field(default=True, description='roster_scan_enable_help')
    # 1=周一 … 7=周日。这里刻意用 int 而不是 IntEnum：
    # 前端（OASX）只认字符串枚举，整数枚举会让脚本页渲染失败（整页空白）。
    weekday: int = Field(default=7, ge=1, le=7, description='roster_scan_weekday_help')
    time: Time = Field(default=Time(hour=3, minute=0), description='roster_scan_time_help')
    rarities: str = Field(default='SP,SSR,SR', description='roster_scan_rarities_help')
    pages_per_rarity: int = Field(default=4, ge=1, le=20, description='roster_scan_pages_help')
    output_file: str = Field(default='config/roster.json', description='roster_scan_output_help')
    capture_dir: str = Field(default='log/roster_scan', description='roster_scan_capture_help')
    include_level: bool = Field(default=True, description='roster_scan_level_help')
    include_speed: bool = Field(default=False, description='roster_scan_speed_help')
    include_skills: bool = Field(default=True, description='roster_scan_skills_help')
    last_scan: str = Field(default='', description='roster_scan_last_help')
