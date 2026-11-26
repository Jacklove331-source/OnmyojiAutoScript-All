# This Python file uses the following encoding: utf-8

"""百鬼棋局对手阵容侦察。

战斗阶段屏幕顶部会以卡牌形式列出对手当前上阵的式神，卡面与商店头像
同源（同一张立绘），所以可以用 ``resource/store/portraits`` 做模板匹配，
再把识别到的式神映射回羁绊，喂给「随机应变」教练做抢卡判断。

识别失败（对手还没上阵、界面被特效遮住）时返回空结果，不影响棋局。
"""

from pathlib import Path

import cv2
import numpy as np

from module.logger import logger
from tasks.Chess.strategy.shikigami_catalog import (
    SHIKIGAMI_BY_ROMAJI,
    SHIKIGAMI_ENTRIES,
)

CHESS_DIR = Path(__file__).resolve().parents[1]
STORE_PORTRAIT_DIR = CHESS_DIR / 'resource' / 'store' / 'portraits'


class ChessScoutMixin:
    """识别对手上阵式神与其羁绊。"""

    # 顶部对手阵容条：卡面立绘所在区域。
    ENEMY_LINEUP_ROI = (340, 40, 940, 260)
    ENEMY_LINEUP_THRESHOLD = 0.86
    ENEMY_LINEUP_MAX = 10
    # 两张卡之间的最小水平间距，用于同卡多模板命中的去重。
    ENEMY_LINEUP_MIN_DISTANCE = 60

    def _scout_templates(self) -> list[tuple[str, object]]:
        """加载全部商店头像模板（灰度），首次调用后缓存。"""
        cached = getattr(self, '_chess_scout_templates', None)
        if cached is not None:
            return cached
        templates: list[tuple[str, object]] = []
        for entry in SHIKIGAMI_ENTRIES:
            path = STORE_PORTRAIT_DIR / f'cost{entry.cost}' / f'store_{entry.romaji}.png'
            if not path.exists():
                logger.debug(f'Chess scout: missing portrait {path}')
                continue
            image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
            if image is None:
                continue
            templates.append((entry.romaji, image))
        self._chess_scout_templates = templates
        return templates

    def scout_opponent_lineup(self) -> list[str]:
        """返回对手上阵式神的 romaji 列表（按屏幕从左到右）。"""
        image = getattr(self.device, 'image', None)
        if image is None:
            return []
        x, y, width, height = self.ENEMY_LINEUP_ROI
        region = cv2.cvtColor(image[y:y + height, x:x + width], cv2.COLOR_BGR2GRAY)
        if region.size == 0:
            return []

        hits: list[tuple[float, int, int, str]] = []
        for romaji, template in self._scout_templates():
            if template.shape[0] > region.shape[0] or template.shape[1] > region.shape[1]:
                continue
            result = cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED)
            # 同一张卡可能重复出现（对手两个同名式神），必须取出所有命中点，
            # minMaxLoc 只给一个最高点会漏掉重复卡。
            ys, xs = np.where(result >= self.ENEMY_LINEUP_THRESHOLD)
            if len(xs) == 0:
                continue
            scores = result[ys, xs]
            order = np.argsort(scores)[::-1][:40]
            for index in order:
                hits.append(
                    (float(scores[index]), int(xs[index]), int(ys[index]), romaji)
                )

        hits.sort(key=lambda item: item[0], reverse=True)
        picked: list[tuple[float, int, int, str]] = []
        for score, px, py, romaji in hits:
            if any(
                abs(px - other_x) < self.ENEMY_LINEUP_MIN_DISTANCE
                and abs(py - other_y) < 40
                for _, other_x, other_y, _ in picked
            ):
                continue
            picked.append((score, px, py, romaji))
            if len(picked) >= self.ENEMY_LINEUP_MAX:
                break

        picked.sort(key=lambda item: item[1])
        return [romaji for _, _, _, romaji in picked]

    @staticmethod
    def bonds_from_lineup(names) -> dict[str, int]:
        """把式神列表折算成羁绊计数。"""
        bonds: dict[str, int] = {}
        for name in names:
            entry = SHIKIGAMI_BY_ROMAJI.get(name)
            if entry is None:
                continue
            for bond in entry.bonds:
                bonds[bond] = bonds.get(bond, 0) + 1
        return bonds

    def scout_opponent_bonds(self, names=None) -> dict[str, int]:
        """识别对手阵容并返回羁绊计数。"""
        self.screenshot()
        lineup = list(names) if names is not None else self.scout_opponent_lineup()
        bonds = self.bonds_from_lineup(lineup)
        display = [
            SHIKIGAMI_BY_ROMAJI[name].chinese_name
            for name in lineup
            if name in SHIKIGAMI_BY_ROMAJI
        ]
        if display:
            logger.info(
                f'Chess opponent lineup: {"/".join(display)} | bonds={bonds}'
            )
        else:
            logger.debug('Chess opponent lineup: not detected')
        return bonds
