class CampaignEnd(Exception):
    pass


class MapDetectionError(Exception):
    pass


class MapWalkError(Exception):
    pass


class MapEnemyMoved(Exception):
    pass


class CampaignNameError(Exception):
    pass


class ScriptError(Exception):
    # This is likely to be a mistake of developers, but sometimes a random issue
    pass


class ScriptEnd(Exception):
    pass


class GameStuckError(Exception):
    pass


class GameBugError(Exception):
    # An error has occurred in Azur Lane game client. Alas is unable to handle.
    # A restart should fix it.
    pass


class GameTooManyClickError(Exception):
    pass


class EmulatorNotRunningError(Exception):
    pass


class GameNotRunningError(Exception):
    pass


class DeviceDisconnectedError(GameNotRunningError):
    """ADB 掉线且自动重连失败。

    继承 GameNotRunningError，保持调度器「任务转 Restart」的既有恢复路径；
    对局/任务层可以用 isinstance 区分「设备掉线」和「游戏真的没跑」。
    """
    pass


class GamePageUnknownError(Exception):
    pass


class RequestHumanTakeover(Exception):
    # Request human takeover
    # Alas is unable to handle such error, probably because of wrong settings.
    pass

class TaskEnd(Exception):
    pass
