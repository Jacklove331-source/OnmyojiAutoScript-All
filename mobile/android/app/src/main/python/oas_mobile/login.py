"""Log in to the game on the Android virtual display."""
from __future__ import annotations

import time

import numpy as np

from module.logger import logger
from oas_mobile import bridge, matcher
from oas_mobile.runtime import checkpoint

INGAME_MARKERS = (
    "悬赏封印", "式神录", "阴阳术", "阴阳寮", "好友列表", "结界突破", "御魂副本",
    "式神图鉴", "收集奖励", "式神绘卷", "属性一览", "集体任务", "契灵之境",
    "觉醒材料", "秘闻副本", "狩猎战", "地域鬼王", "花合战", "神秘商店",
)
# 町中（商店街）页面：上一个任务「退出商店」后游戏会停在这里，而这里既不是庭院也不是标题页。
# 实测（+40 run 8743ac67…，03:21:36 帧）画面上的店铺名 OCR 可靠（寄售屋/秘魂屋/杂货铺/礼包屋…），
# 而 page_main_check_town.png 只有 0.412（阈值 0.75）认不出来 → 登录守卫于是空转到 480s 超时。
TOWN_STREET_MARKERS = (
    "寄售屋", "秘魂屋", "杂货铺", "礼包屋", "皮肤屋", "周边屋", "插画屋", "秘巷屋", "魂玉屋",
)
# 活动/逢魔页面：MetaDemon 跑完（尤其是「活动已结束」）会把客户端留在这一页，同样既不是庭院也不是标题页。
# 实测（+41 run c202de39 / 6f80fcee，帧 frames-MetaDemon/f-118-140434.jpg）：页面写着
# 「祸至无日 / 祸主降临·超鬼王 / 笼中残梦 / 总优胜 / 解厄商店 / 风物志」，而守卫一个都认不出
# → 下一轮任务（KekkaiUtilize）在登录阶段空转到 480s 超时（帧 run 14:07-14:15）。
ACTIVITY_PAGE_MARKERS = (
    "活动已结束", "祸至无日", "祸主降临", "笼中残梦", "解厄商店", "风物志", "总优胜",
)
USER_CHOICE_TEXTS = (
    "选择区域", "请选择区服", "请选择服务器", "请选择角色", "请选择大区",
    "未选择服务器", "个性化内容推荐", "请输入手机号", "请输入验证码", "请输入密码",
)
ENTER_SCALES = (0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)
LOGIN_WIDTH, LOGIN_HEIGHT = 1280, 720
# 冷启动实测：+35 从 app_start 到 login result 用了 214s，+36 那次 300s 用满时客户端才刚到
# 庭院（见 run b40f1c63…，02:07:16 起、02:12:24 超时，超时后 9s 的帧已经是带加成交互框的庭院）。
LOGIN_TIMEOUT = 480.0
CHARACTER_SELECT_LIMIT = 4
REGION_SWITCH_POINT = (745, 526)
# 登录期只点「中性」按钮：取消/关闭/知道了 不会改账号状态，也不会替用户确认任何购买或加成。
MODAL_DISMISS_TEXTS = ("取消", "关闭", "知道了", "返回")
# 「是否打开大人之前被自动关闭的加成？」卷轴框的取消按钮中心（1280x720 坐标，实测 1920x1080 帧换算）。
DIALOG_CANCEL_POINT = (550, 419)

# 登录页「画面不动的静止帧」自救阶梯（+35 实测：整段 5 分钟停在 0.630 的同一屏）。
# 判定只用一条客观信号：把每轮画面缩成 64x36 灰度图后算帧间均差；均差 < STALL_DELTA
# 就算「画面没动」。引导 CG / 加载动画的均差是 45~70，标题页约 5，实测静止帧 0.69，
# 所以 1.5 能把「卡住的静止画面」和「正在播放的动画」分开。
STALL_DELTA = 1.5
STALL_MIN_BRIGHTNESS = 20.0    # 全黑帧（灰度均值 < 20，实测纯黑=1.0）属于正常加载，不算"卡住"
STALL_TAP_SECONDS = 30.0       # 静止 30s → 补点一次「进入游戏/屏幕中心」
STALL_RESTART_SECONDS = 150.0  # 静止 150s → 重启一次客户端
STALL_TAPS = 2
STALL_RESTARTS = 1
UNKNOWN_ESCALATE_SECONDS = 120.0   # 画面一直在动却认不出页面 → 按累计时长升级补点


def _capture_login_frame(display: int):
    import cv2

    if bridge.vdstream_running():
        if callable(getattr(bridge, "vdstream_capture", None)):
            bridge.vdstream_capture(85)
        info = bridge.vdstream_info()
        if (info.get("display_id") != display or not info.get("target_on_display")
                or not 0 <= info.get("age_ms", -1) <= 2000):
            return None
    png = bridge.capture_display_png(display)
    if not png:
        return None
    frame = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        return None
    return png, cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)


def _frame_metrics(frame) -> tuple[float, int, int]:
    height, width = frame.shape[:2]
    scale = LOGIN_HEIGHT / float(height)
    scaled_w = int(round(width * scale))
    offset = (scaled_w - LOGIN_WIDTH) // 2 if scaled_w >= LOGIN_WIDTH else -((LOGIN_WIDTH - scaled_w) // 2)
    return scale, offset, scaled_w


def _normalized(frame):
    import cv2

    if frame.shape[:2] == (LOGIN_HEIGHT, LOGIN_WIDTH):
        return frame
    _, offset, scaled_w = _frame_metrics(frame)
    resized = cv2.resize(frame, (scaled_w, LOGIN_HEIGHT), interpolation=cv2.INTER_AREA)
    if scaled_w >= LOGIN_WIDTH:
        return resized[:, offset:offset + LOGIN_WIDTH]
    padded = np.zeros((LOGIN_HEIGHT, LOGIN_WIDTH, 3), dtype=resized.dtype)
    left = -offset
    padded[:, left:left + scaled_w] = resized
    return padded


def _courtyard_visible(frame) -> bool:
    from tasks.GameUi.assets import GameUiAssets

    return (GameUiAssets.I_MAIN_GOTO_EXPLORATION.match(frame)
            and GameUiAssets.I_MAIN_GOTO_TOWN.match(frame))


def _courtyard_entrance_visible(frame) -> bool:
    """庭院探索灯笼能否识别：它被弹窗挡住时町中按钮会先消失。

    实测（`evidence/probe37/now.jpg`，1280x720 + 现有素材直跑 cv2.matchTemplate）：
    `page_main_goto_exploration.png` 命中 0.884（阈值 0.8），
    `page_main_goto_town.png` 只有 0.228（阈值 0.6）——因为「是否打开…加成？」卷轴框正好压在町中按钮上。
    所以只命中探索灯笼 = 人已经在庭院，但有个弹窗挡在中间。
    """
    from tasks.GameUi.assets import GameUiAssets

    return (GameUiAssets.I_MAIN_GOTO_EXPLORATION.match(frame)
            and not GameUiAssets.I_MAIN_GOTO_TOWN.match(frame))


def _in_game_menu_visible(frame) -> bool:
    from tasks.GameUi.assets import GameUiAssets
    from tasks.KekkaiActivation.assets import KekkaiActivationAssets
    from tasks.KekkaiUtilize.assets import KekkaiUtilizeAssets
    from tasks.WantedQuests.assets import WantedQuestsAssets

    return any(rule.match(frame) for rule in (
        GameUiAssets.I_CHECK_DAILY,
        GameUiAssets.I_CHECK_TEAM,
        GameUiAssets.I_CHECK_RECORDS,
        GameUiAssets.I_CHECK_FRIENDS,
        GameUiAssets.I_CHECK_EXPLORATION,
        GameUiAssets.I_CHECK_DEMON_ENCOUNTER_REALWORLD,
        GameUiAssets.I_CHECK_SECRET_ZONES,
        GameUiAssets.I_CHECK_GUILD,
        KekkaiUtilizeAssets.I_REALM_SHIN,
        KekkaiActivationAssets.I_A_CHECK_CARD,
        WantedQuestsAssets.I_WQSE_FIRE,
    ))


def _region_visible(frame) -> bool:
    from tasks.Restart.assets import RestartAssets

    return RestartAssets.I_LOGIN_SPECIFIC_SERVE.match(frame)


def _title_visible(frame) -> bool:
    from tasks.Restart.assets import RestartAssets

    return RestartAssets.I_LOGIN_8.match(frame)


def _region_close_point(frame):
    import cv2
    from tasks.Restart.assets import RestartAssets

    close = RestartAssets.I_LOGIN_RED_CLOSE
    template = close.image
    # The region dialog has a close icon at its top right, above the role cards.
    x, y = 1030, 66
    crop = frame[y:181, x:1172]
    height, width = template.shape[:2]
    if height > crop.shape[0] or width > crop.shape[1]:
        return None
    scores = cv2.matchTemplate(crop, template, cv2.TM_CCOEFF_NORMED)
    _, score, _, (dx, dy) = cv2.minMaxLoc(scores)
    if score <= close.threshold:
        return None
    return x + dx + width // 2, y + dy + height // 2


def _box_center(box) -> tuple[int, int]:
    points = np.asarray(box)
    return tuple(int(value) for value in (points.min(axis=0) + points.max(axis=0)) / 2)


def _modal_dismiss_point(results):
    """找登录期挡住画面的中性按钮（取消/关闭/知道了），返回它在 1280x720 下的中心。

    +36 实测（run b40f1c63…，02:12:33 帧）：客户端已经进了庭院，但被
    「是否打开大人之前被自动关闭的加成？」这个卷轴框挡住，`home_explore` 只有 0.65，
    庭院的探索/町中识别全部落空，登录守卫于是空转到超时。登录阶段没有任何需要确认的操作，
    所以只点取消类按钮，绝不点「确认」。
    """
    for item in results:
        text = "".join(item.ocr_text.split())
        if text in MODAL_DISMISS_TEXTS:
            return _box_center(item.box)
    return None


def match_character_card(results, character: str, roi):
    """Return the center and count of complete matches in the role-card list."""
    target = "".join(character.split())
    boxes = [item.box for item in results if target and "".join(item.ocr_text.split()) == target]
    if len(boxes) != 1:
        return None, len(boxes)
    points = np.asarray(boxes[0], dtype=float)
    if points.shape != (4, 2) or not np.isfinite(points).all():
        return None, 1
    left, top = points.min(axis=0)
    right, bottom = points.max(axis=0)
    x, y, width, height = roi
    if not (0 < left < right < width and 0 < top < bottom < height):
        return None, 1
    return (round(x + (left + right) / 2), round(y + (top + bottom) / 2)), 1


def _display_point(point, frame) -> tuple[int, int]:
    x, y = point
    height, width = frame.shape[:2]
    scale, offset, _ = _frame_metrics(frame)
    nx = (float(x) + offset) / scale
    ny = float(y) / scale
    return min(width - 1, max(0, round(nx))), min(height - 1, max(0, round(ny)))


def _character_point(frame, character: str, timeout: float):
    from tasks.Restart.assets import RestartAssets

    x, y, width, height = RestartAssets.O_LOGIN_SPECIFIC_SERVE.roi
    left, top = _display_point((x, y), frame)
    right, bottom = _display_point((x + width, y + height), frame)
    deadline = time.monotonic() + timeout
    results = _ocr_with_timeout(frame[top:bottom, left:right], timeout=timeout)
    point, matches = match_character_card(results, character, (left, top, right - left, bottom - top))
    if (matches > 1 or len(character) != 2 or character[-1] != "\u4e36"
            or not character[0].isascii() or not character[0].isalpha()):
        return point, matches

    from oas_mobile.role_text import compact_dot_candidates

    points = [point] if point is not None else []
    candidates = list(compact_dot_candidates(frame))
    leading = character[0]
    # The foreground geometry has already established the full trailing
    # Han stroke. OCR supplies the exact leading letter for that same name.
    equivalent_readings = (character, leading, leading + "\u3001")
    tolerance = 0.015 * frame.shape[1]
    for candidate, variants in candidates:
        for crop in variants:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None, len(points)
            readings = _ocr_with_timeout(crop, timeout=remaining)
            if len(readings) == 1 and "".join(readings[0].ocr_text.split()) in equivalent_readings:
                break
        else:
            continue
        if not any(abs(x - candidate[0]) <= tolerance and abs(y - candidate[1]) <= tolerance
                   for x, y in points):
            points.append(candidate)
    logger.info(f"[mobile] compact character verification found {len(candidates)} visual candidates, {len(points)} matches")
    return (points[0] if len(points) == 1 else None), len(points)


def _server_label_selected(text: str) -> bool:
    name = "".join(text.split()).replace("切換", "切换").split("切换", 1)[0]
    if any(prompt in name for prompt in ("选择", "服务器", "大区", "正在连接", "登录")):
        return False
    return sum(character.isalnum() for character in name) >= 2


def _saved_server_visible(results, frame, fallback_timeout: float) -> bool:
    # The current server name is on the title page, on the left of the switch button.
    left, top, right, bottom = 455, 494, 710, 552
    labels = []
    for item in results:
        x, y = _box_center(item.box)
        if left <= x <= right and top <= y <= bottom:
            labels.append((x, item.ocr_text))
    if _server_label_selected("".join(label for _, label in sorted(labels))):
        return True
    if fallback_timeout <= 0:
        return False
    cropped = _ocr_with_timeout(frame[top:bottom, left:right], timeout=fallback_timeout)
    labels = sorted((_box_center(item.box)[0], item.ocr_text) for item in cropped)
    return _server_label_selected("".join(label for _, label in labels))


def _entry_point(png, frame, results):
    result = matcher.match("login_enter_game.png", png, scales=ENTER_SCALES)
    height, width = frame.shape[:2]
    if result.get("ok") and result.get("loc") and result.get("score", 0) >= 0.70:
        cx, cy = matcher.tap_center(result)
        if 0.40 <= cx / width <= 0.60 and 0.77 <= cy / height <= 0.87:
            return cx, cy
    for item in results:
        cleaned = "".join(item.ocr_text.split())
        if cleaned == "进入游戏" or "进入游" in cleaned:
            x, y = _box_center(item.box)
            if 0.40 <= x / LOGIN_WIDTH <= 0.60 and 0.74 <= y / LOGIN_HEIGHT <= 0.87:
                return _display_point((x, y), frame)
    return None


_OCR_SYSTEM = None


def _ocr_system():
    """PaddleOCR 单例：构造一次就缓存，避免每轮重复初始化。"""
    global _OCR_SYSTEM
    if _OCR_SYSTEM is None:
        from ppocronnx.predict_system import TextSystem

        _OCR_SYSTEM = TextSystem()
    return _OCR_SYSTEM


def _ocr_with_timeout(frame, timeout: float = 20.0):
    """OCR 放子线程跑，超时就放弃本轮，避免整轮被拖死。"""
    import threading

    box = {}

    def _work():
        try:
            box["results"] = _ocr_system().detect_and_ocr(frame)
        except Exception as error:  # noqa: BLE001
            box["error"] = error

    worker = threading.Thread(target=_work, daemon=True)
    started = time.perf_counter()
    worker.start()
    deadline = time.monotonic() + timeout
    while worker.is_alive() and time.monotonic() < deadline:
        checkpoint()
        worker.join(min(.1, max(0, deadline - time.monotonic())))
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    if worker.is_alive():
        logger.warning(f"[mobile] ocr timeout after {elapsed_ms:.0f}ms, skip this round")
        return []
    if "error" in box:
        logger.warning(f"[mobile] ocr failed: {box['error']!r}")
        return []
    if elapsed_ms > 3000:
        logger.warning(f"[mobile] ocr slow: {elapsed_ms:.0f}ms")
    return box.get("results") or []


def _restart_client(display: int) -> int:
    """强制重启一个卡住的游戏客户端，并把它重新放回 `display`。

    实测场景（+35，run 7c548da5…）：游戏进程还活着、帧也还在出，但画面停在同一张
    静态场景上 5 分钟，登录守卫没有任何分支能命中。手工 `am force-stop` + 冷启动
    能让游戏重新走完引导 CG 到「进入游戏」页，所以这里把同一动作做成一次性自救。
    """
    package = bridge.target_package()
    if not package:
        return -1
    try:
        from oas_mobile.shizuku_exec import exec_shell

        exec_shell(f"am force-stop {package}")
    except Exception as error:  # noqa: BLE001
        logger.warning(f"[mobile] client restart skipped: {error!r}")
        return -1
    time.sleep(3.0)
    restored = bridge.display_ensure_app(package)
    if restored < 0:
        logger.warning("[mobile] client restart could not put the game back on a display")
        return -1
    logger.info(f"[mobile] client restarted on display {restored} (was {display})")
    time.sleep(6.0)
    return restored


def ensure_logged_in(display: int, timeout: float = LOGIN_TIMEOUT, *, character: str = "") -> bool:
    """Enter using the saved server, or select the configured character on Android."""
    import cv2

    deadline = time.monotonic() + min(max(float(timeout), 1.0), LOGIN_TIMEOUT)
    character = "".join(character.split())
    character_selected = not character
    enter_taps = region_closes = round_index = 0
    region_opens = role_checks = 0
    previous_gray = None
    static_since = time.monotonic()
    last_wake_at = time.monotonic()
    stall_taps = client_restarts = stall_logs = modal_cancels = 0
    capture_failures = capture_rebuilds = 0
    login_started_at = time.monotonic()
    while time.monotonic() < deadline:
        checkpoint()
        try:
            capture = _capture_login_frame(display)
        except Exception as error:  # noqa: BLE001
            capture = None
            if capture_failures == 0:
                logger.warning(f"[mobile] login capture raised: {error!r}")
        if capture is None:
            # 虚拟屏/直播流失效时这里会一直拿不到帧：必须计数、尝试重建、并尽快失败，
            # 否则会空转到 deadline，把"采集不可用"误报成"登录未确认"（实测 481s）。
            capture_failures += 1
            if capture_failures == 1 or capture_failures % 10 == 0:
                logger.warning(
                    f"[mobile] login capture unavailable ({capture_failures}x), display={display}")
            if capture_failures % 5 == 0:
                # 直播流与本地 display 号不一致时，_capture_login_frame 会稳定返回 None；
                # 先对齐到直播流当前屏号，再谈重建。
                try:
                    stream_display = bridge.vdstream_display()
                except Exception:  # noqa: BLE001
                    stream_display = -1
                if stream_display >= 0 and stream_display != display:
                    logger.info(f"[mobile] login display resync {display} -> {stream_display}")
                    display = stream_display
            if capture_failures % 5 == 0 and capture_rebuilds < 3:
                capture_rebuilds += 1
                try:
                    rebuilt = bridge.display_ensure_app(bridge.target_package())
                except Exception as error:  # noqa: BLE001
                    rebuilt = -1
                    logger.warning(f"[mobile] login display rebuild raised: {error!r}")
                if rebuilt >= 0:
                    display = rebuilt
                    logger.info(f"[mobile] login display rebuilt: {rebuilt}")
            if capture_failures >= 60:
                raise RuntimeError(
                    "capture unavailable: virtual display failed after "
                    f"{capture_failures} attempts (display={display})")
            time.sleep(1.0)
            continue
        capture_failures = 0
        png, frame = capture
        view = _normalized(frame)
        gray = cv2.cvtColor(cv2.resize(view, (64, 36), interpolation=cv2.INTER_AREA),
                            cv2.COLOR_RGB2GRAY).astype(np.int16)
        motion = 255.0 if previous_gray is None else float(np.abs(gray - previous_gray).mean())
        previous_gray = gray
        if motion >= STALL_DELTA or float(gray.mean()) < STALL_MIN_BRIGHTNESS:
            # 画面在动，或者只是正常的黑屏加载 —— 都不算"卡住的静止帧"。
            static_since = time.monotonic()
        if _courtyard_visible(view):
            if not character_selected:
                logger.warning("[mobile] reached courtyard before confirming the configured character")
                return False
            logger.info("[mobile] courtyard confirmed by exploration and town entrances")
            return True

        if _courtyard_entrance_visible(view) and modal_cancels < 3:
            # 已经在庭院，但有个弹窗压在町中按钮上（实测就是「是否打开…加成？」卷轴框）：
            # 直接点卷轴框的取消位关掉它，不点确认、不改账号状态。
            modal_cancels += 1
            checkpoint()
            x, y = _display_point(DIALOG_CANCEL_POINT, frame)
            logger.info(f"[mobile] courtyard reached behind a modal; tap cancel at {x},{y}"
                        f" ({modal_cancels}/3)")
            bridge.display_tap(display, x, y)
            time.sleep(2.0)
            continue

        if _region_visible(view):
            if character:
                if role_checks >= CHARACTER_SELECT_LIMIT:
                    logger.warning("[mobile] configured character selection did not complete")
                    return False
                role_checks += 1
                if not character_selected:
                    point, matches = _character_point(
                        frame, character, timeout=min(12.0, deadline - time.monotonic()))
                    if point is None:
                        logger.info(f"[mobile] configured character needs one complete match, found {matches}")
                    elif time.monotonic() < deadline:
                        fresh = _capture_login_frame(display)
                        if (fresh is not None and fresh[1].shape == frame.shape
                                and _region_visible(_normalized(fresh[1]))):
                            checkpoint()
                            if bridge.display_tap(display, *point):
                                character_selected = True
                                role_checks = 0
                                round_index = 0
                                logger.info("[mobile] selected the uniquely matched configured character")
                time.sleep(2.0)
                continue
            close = _region_close_point(view)
            if close is None or region_closes >= 3:
                logger.warning("[mobile] region selection requires the user")
                return False
            checkpoint()
            x, y = _display_point(close, frame)
            logger.info(f"[mobile] close region dialog at {x},{y}")
            bridge.display_tap(display, x, y)
            region_closes += 1
            time.sleep(2.0)
            continue

        title = _title_visible(view)
        results = []
        # 画面已经静止（认不出来 / 被弹框挡住）时每轮都 OCR：否则要等满 3 轮才看一次，
        # 弹框会白白吃掉几十秒登录预算。
        stalled = time.monotonic() - static_since >= STALL_TAP_SECONDS
        if title or stalled or round_index % 3 == 0:
            results = _ocr_with_timeout(view, timeout=min(20.0, max(0.0, deadline - time.monotonic())))
        round_index += 1
        if time.monotonic() >= deadline:
            break
        texts = "".join("".join(item.ocr_text.split()) for item in results)
        if any(prompt in texts for prompt in USER_CHOICE_TEXTS):
            logger.warning("[mobile] login requires a user choice or account authentication")
            return False

        has_enter_text = "进入游戏" in texts or "进入游" in texts
        if has_enter_text:
            # 标题页的「进入游戏」是花体大字（实测偶发被 OCR 读成「进入游线」），模板（I_LOGIN_8）
            # 也会随新版本开屏底图漂移；只要 OCR 认出「进入游*」且存档区服名已核验，
            # 即使模板没命中也能正常推进：
            # - 若配置了指定角色且尚未选角（not character_selected）：先点「切换」打开选角器；
            # - 若已确认角色（character_selected）：直接点「进入游戏」文本框中心。
            verified = _saved_server_visible(
                results, view, min(3.0, max(0.0, deadline - time.monotonic())))
            if not verified:
                logger.info("[mobile] 进入游戏 visible but the saved server is not verified yet")
            elif not character_selected:
                if region_opens >= 3:
                    logger.warning("[mobile] could not open the configured character picker")
                    return False
                if time.monotonic() >= deadline:
                    break
                fresh = _capture_login_frame(display)
                if fresh is not None and fresh[1].shape == frame.shape:
                    latest = _normalized(fresh[1])
                    if not _region_visible(latest) and not _courtyard_visible(latest):
                        checkpoint()
                        bridge.display_tap(display, *_display_point(REGION_SWITCH_POINT, fresh[1]))
                        region_opens += 1
                        logger.info("[mobile] open the configured character picker (OCR title)")
                        time.sleep(2.0)
                        continue
            elif enter_taps >= 6:
                logger.warning("[mobile] enter-game attempt limit reached")
                return False
            else:
                tapped = False
                for item in results:
                    cleaned = "".join(item.ocr_text.split())
                    if "进入游戏" in cleaned or "进入游" in cleaned:
                        checkpoint()
                        x, y = _display_point(_box_center(item.box), frame)
                        enter_taps += 1
                        logger.info(f"[mobile] tap 进入游戏 by OCR at {x},{y} ({enter_taps})")
                        bridge.display_tap(display, x, y)
                        time.sleep(6.0)
                        tapped = True
                        break
                if tapped:
                    continue

        if title:
            if _saved_server_visible(results, view, min(3.0, deadline - time.monotonic())):
                if not character_selected and region_opens >= 3:
                    logger.warning("[mobile] could not open the configured character picker")
                    return False
                if enter_taps >= 6:
                    logger.warning("[mobile] enter-game attempt limit reached")
                    return False
                # OCR can finish after the screen has changed. Only tap a still-visible title.
                if time.monotonic() >= deadline:
                    break
                fresh = _capture_login_frame(display)
                if fresh is not None and fresh[1].shape == frame.shape:
                    latest = _normalized(fresh[1])
                    if _title_visible(latest) and not _region_visible(latest):
                        target = _entry_point(fresh[0], fresh[1], results)
                        if target is not None and time.monotonic() < deadline:
                            checkpoint()
                            if not character_selected:
                                # The verified title's switch button is just right of the saved server.
                                bridge.display_tap(display, *_display_point(REGION_SWITCH_POINT, fresh[1]))
                                region_opens += 1
                                logger.info("[mobile] open the configured character picker")
                                time.sleep(2.0)
                                continue
                            logger.info(f"[mobile] tap saved-server enter-game at {target[0]},{target[1]}")
                            bridge.display_tap(display, *target)
                            enter_taps += 1
                            time.sleep(6.0)
                            continue
        else:
            if _in_game_menu_visible(view):
                logger.info("[mobile] authenticated game menu confirmed")
                return character_selected
            if ("探索" in texts and "町中" in texts) or any(marker in texts for marker in INGAME_MARKERS):
                logger.info("[mobile] in-game detected via text")
                return character_selected
            street = [name for name in TOWN_STREET_MARKERS if name in texts]
            if len(street) >= 2:
                # 町中商店街：上一个任务退出商店后常停在这里，属于已登录状态，直接放行。
                logger.info(f"[mobile] in-game detected on town street via text {street[:3]}")
                return character_selected
            activity = [name for name in ACTIVITY_PAGE_MARKERS if name in texts]
            if len(activity) >= 2:
                # 逢魔/活动页：MetaDemon 收尾后会留在这里；同样算已登录，交给任务的导航去回庭院。
                logger.info(f"[mobile] in-game detected on activity page via text {activity[:3]}")
                return character_selected
            home = matcher.match("home_explore.png", png)
            if home.get("ok") and home.get("score", 0) >= 0.80:
                logger.info(f"[mobile] home detected (score={home['score']})")
                return character_selected
            dismiss = _modal_dismiss_point(results)
            if dismiss is not None:
                # 认出了挡画面的卷轴/公告框：点它的取消类按钮，别点确认。
                checkpoint()
                x, y = _display_point(dismiss, frame)
                logger.info(f"[mobile] dismiss login-time dialog at {x},{y}")
                bridge.display_tap(display, x, y)
                time.sleep(2.0)
                continue
            for item in results:
                # 「点击屏幕继续」是每日一签/活动开屏的通用续播提示（实测 2026-11-22 11:30：
                # 每日一签抽签结果页卡住了 Restart 的庭院事务 10 分钟）。这里给宽松匹配：
                # 只要文本里含「点击屏幕」就点它中心，别死在弹窗上。
                if "点击屏幕" in "".join(item.ocr_text.split()):
                    checkpoint()
                    bridge.display_tap(display, *_display_point(_box_center(item.box), frame))
                    static_since = time.monotonic()
                    break
            else:
                frozen = time.monotonic() - static_since
                elapsed = time.monotonic() - login_started_at
                if (frozen >= STALL_TAP_SECONDS and stall_taps < STALL_TAPS
                        and time.monotonic() - last_wake_at >= STALL_TAP_SECONDS):
                    # 静止画面且所有分支都不成立：按「补点阶梯」依次试还原加成框的取消位、
                    # 「进入游戏」按钮、屏幕中心。原样把这一屏的 OCR 文本打进日志，
                    # 这样下一次卡住时不用再靠猜。
                    stall_taps += 1
                    if stall_logs < 3:
                        stall_logs += 1
                        logger.info(f"[mobile] stalled screen texts: {texts[:160]!r}")
                    if stall_taps == 1:
                        point = _display_point(DIALOG_CANCEL_POINT, frame)
                    else:
                        point = _entry_point(png, frame, results) or _display_point(
                            (LOGIN_WIDTH // 2, LOGIN_HEIGHT // 2), frame)
                    checkpoint()
                    logger.warning(
                        f"[mobile] login screen frozen for {frozen:.0f}s "
                        f"(motion={motion:.2f}, home score {home.get('score')}),"
                        f" tap {point} to wake it up")
                    bridge.display_tap(display, *point)
                    last_wake_at = time.monotonic()
                elif (elapsed >= UNKNOWN_ESCALATE_SECONDS and stall_taps < STALL_TAPS
                        and float(gray.mean()) >= STALL_MIN_BRIGHTNESS):
                    # 画面一直在动（加载 CG / 转场动画）却一直认不出任何页面：
                    # 原逻辑只等"静止"，这种情况会一路空转到 deadline（实测 frozen=0~2s 时超时）。
                    # 但**全黑加载帧不能升级补点**：冷启动那 1–2 分钟就是黑屏，点它没有意义，
                    # 也会破坏「黑屏不算卡住」的契约（test_black_loading_screen_is_not_treated_as_frozen）。
                    stall_taps += 1
                    point = _entry_point(png, frame, results) or _display_point(
                        (LOGIN_WIDTH // 2, LOGIN_HEIGHT // 2), frame)
                    checkpoint()
                    logger.warning(
                        f"[mobile] login unrecognized for {elapsed:.0f}s "
                        f"(motion={motion:.2f}, home score {home.get('score')}),"
                        f" tap {point} to escalate")
                    bridge.display_tap(display, *point)
                    last_wake_at = time.monotonic()
                elif frozen >= STALL_RESTART_SECONDS and client_restarts < STALL_RESTARTS:
                    # 补点也无效说明客户端自己卡住了（+35 实测场景）：重启一次再等它重新上屏。
                    client_restarts += 1
                    logger.warning(
                        f"[mobile] login screen still frozen after {frozen:.0f}s "
                        f"(motion={motion:.2f}, home score {home.get('score')}),"
                        f" restarting the game client")
                    restored = _restart_client(display)
                    if restored >= 0:
                        display = restored
                    previous_gray = None
                    static_since = time.monotonic()

        time.sleep(2.0)

    logger.warning(
        "[mobile] login timeout; courtyard or saved-server entry could not be confirmed"
        f" (screen frozen for {time.monotonic() - static_since:.0f}s,"
        f" wake taps={stall_taps}, client restarts={client_restarts})")
    return False
