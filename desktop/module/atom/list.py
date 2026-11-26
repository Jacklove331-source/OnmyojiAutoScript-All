# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey

import cv2
import random
import numpy as np

from random import randint

from ppocronnx.predict_system import BoxedResult
from module.atom.ocr import RuleOcr
from module.atom.image import RuleImage
from module.logger import logger


class RuleList:

    def __init__(self, folder: str, direction: str, mode: str, roi_back: tuple, size: tuple, array: list[str]) -> None:
        """
        初始化
        :param folder:
        :param direction:
        :param mode:
        :param roi_back:
        :param size:
        :param array:
        """
        self.folder = folder
        self.is_vertical = direction == "vertical"
        self.is_image = mode == "image"
        self.is_ocr = mode == "ocr"
        self.roi_back: list = list(roi_back)
        self.size: list = list(size)
        # 这个表示一个区域中一次所能显示的最大数量
        self.max_show: int = self.roi_back[3] // self.size[1] if self.is_vertical else self.roi_back[2] // self.size[0]

        self.array: list = array

        # 以下是需要计算的
        self.appear_area: list = None  # 出现的区域
        self.is_bottom = False  # 表示是否已经滑动到底部了
        self._target = None  # 目标
        self.targets = {}  # 目标列表 只是针对image

    @property
    def name(self):
        return f'RuleList[{self.__hash__()}]'

    def __hash__(self):
        return hash((self.folder, self.is_vertical, self.is_image, self.is_ocr, tuple(self.roi_back), tuple(self.size), tuple(self.array)))

    def swipe_pos(self, number: int=2, after: bool=True) -> tuple:
        """
        返回滑动的起始位置和终点位置
        :param number:
        :param after:
        :return:
        """
        center: tuple = (self.roi_back[0] + self.roi_back[2]) // 2, (self.roi_back[1] + self.roi_back[3]) // 2
        distance: int = self.size[1] * number if self.is_vertical else self.size[0] * number
        random_start: int = randint(- self.size[0] // 4, self.size[0] // 4) if self.is_vertical else randint(- self.size[1] // 4, self.size[1] // 4)
        random_end: int = randint(- self.size[0] // 4, self.size[0] // 4) if self.is_vertical else randint(- self.size[1] // 4, self.size[1] // 4)

        x1, y1, x2, y2 = None, None, None, None
        if self.is_vertical:
            if after:
                # 竖直方向向下滑动， 那就是起始点是在下面 终点是在上面
                x1, y1 = center[0]+random_start, center[1] + distance//2
                x2, y2 = center[0]+random_end, center[1] - distance//2
            else:
                # 竖直方向向上滑动， 那就是起始点是在上面 终点是在下面
                x1, y1 = center[0]+random_start, center[1] - distance//2
                x2, y2 = center[0]+random_end, center[1] + distance//2
        else:
            if after:
                # 水平方向向右滑动， 那就是起始点是在右面 终点是在左面
                x1, y1 = center[0] + distance//2, center[1]+random_start
                x2, y2 = center[0] - distance//2, center[1]+random_end
            else:
                # 水平方向向左滑动， 那就是起始点是在左面 终点是在右面
                x1, y1 = center[0] - distance//2, center[1]+random_start
                x2, y2 = center[0] + distance//2, center[1]+random_end

        if x1 is None or y1 is None or x2 is None or y2 is None:
            raise Exception("滑动位置计算错误")
        return int(x1), int(y1), int(x2), int(y2)

    def target_check(self, name: str) -> bool:
        """
        检查输入的名称
        :param name:
        :return:
        """
        # 首先获取构建类的信息
        # roi_front = None
        # for item in self.array:
        #     if item["itemName"] == name:
        #         roi_front = item["roiFront"].split(",")
        #         break
        # if roi_front is None:
        #     logger.error(f'Not found {name} in {self.array}')
        #     return False
        file = self.folder + "/" + name + ".png"

        # 保证匹配的目标 是正确的
        if self.is_image:
            if self._target is None or isinstance(self._target, RuleOcr):
                self._target = RuleImage(roi_front=self.roi_back, roi_back=self.roi_back, method="Template matching", threshold=0.8, file=file)
            # 如果不对的话，就重新构建
            elif self._target.name != name:
                self._target = RuleImage(roi_front=self.roi_back, roi_back=self.roi_back, method="Template matching", threshold=0.8, file=file)
            else:
                pass
        elif self.is_ocr:
            if self._target is None or isinstance(self._target, RuleImage):
                self._target = RuleOcr(roi=self.roi_back, area=(0, 0, 10, 10), mode="Full", method="Default",
                                       keyword=name, name=name)
            elif self._target.name != name:
                self._target.name = name
                self._target.keyword = name
        else:
            logger.error(f'Not found {name} in {self.array}')
            return False

    def targets_check(self, targets: list):
        """
        检查输入的图片名是缓存有
        :param targets:
        :return:
        """
        for item in targets:
            if item in self.targets:
                continue
            # 如果还没有缓存的话就先缓存
            file = self.folder + "/" + item + ".png"
            self.targets[item] = RuleImage(roi_front=self.roi_back, roi_back=self.roi_back,
                                           method="Template matching", threshold=0.8, file=file)

    def image_appear(self, image: np.array, name: str) -> bool | tuple:
        """
        判断是否出现了某个图片
        :param image: 屏幕的截图
        :param name:
        :return: 如果在当前的显示中，返回可以点击的坐标.如果不是出现就是返回False
        """
        if self.is_image and isinstance(name, str):
            self.target_check(name)
            appear = self._target.match(image)
            if appear:
                return self._target.coord()
            else:
                return False
        elif self.is_image and isinstance(name, list):
            self.targets_check(name)
            for item in name:
                appear = self.targets[item].match(image)
                if appear:
                    return self.targets[item].coord()
            return False

        else:
            logger.error(f'Mode is not image')
            return False

    def ocr_appear(self, image: np.ndarray, name: str | list[str]) -> tuple[int, int] | int | None:
        """Return the first matching label, a scroll direction, or None for an unreadable list."""
        names = [name] if isinstance(name, str) else name
        if not self.is_ocr or not names:
            return None
        self.target_check(names[0])
        results = self._target.detect_and_ocr(image)
        visible = {''.join(item.ocr_text.split()): item.box for item in results or ()
                   if item.score > RuleOcr.score and item.ocr_text}
        for label in names:
            if label in visible:
                box = visible[label]
                x = (box[0, 0] + box[1, 0]) / 2 + self.roi_back[0]
                y = (box[0, 1] + box[2, 1]) / 2 + self.roi_back[1]
                return int(x), int(y)

        # List indices are only used to choose a direction when every accepted label
        # is outside the visible range. A missing variant within the range needs no swipe.
        indices = {label: index for index, label in enumerate(self.array)}
        visible_indices = [indices[label] for label in visible if label in indices]
        target_indices = [indices[label] for label in names if label in indices]
        if not visible_indices or not target_indices:
            return None
        if max(target_indices) < min(visible_indices):
            return -1
        if min(target_indices) > max(visible_indices):
            return 1
        return 0


if __name__ == '__main__':
    L_N33AME = RuleList(folder="./tasks/Orochi/res", direction="vertical", mode="ocr", roi_back=(160, 130, 317, 500),
                        size=(301, 86),
                        array=["壹层", "贰层", "叁层", "肆层", "伍层", "陆层", "柒层", "捌层", "玖层", "拾层", "悲鸣", "神罚"])
    image = cv2.imread("D:/watu_list_text204237.png")
    print(L_N33AME.ocr_appear(image, "柒层"))
    print(L_N33AME.ocr_appear(image, "神罚"))
