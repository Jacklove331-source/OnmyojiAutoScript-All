"""Run the legacy scheduled Moans task using the maintained Orochi route."""
from tasks.Orochi.config import Layer
from tasks.Orochi.script_task import ScriptTask as OrochiTask


class ScriptTask(OrochiTask):
    schedule_task_name = 'OrochiMoans'

    @property
    def _battle_layer(self):
        return Layer.ELEVEN
