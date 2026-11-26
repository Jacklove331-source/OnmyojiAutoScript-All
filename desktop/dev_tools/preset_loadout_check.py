from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
import importlib.util, json, os, sys
import cv2, numpy as np
ROOT=Path(os.environ.get('OAS_PRESET_LINK_SOURCE',str(Path(__file__).resolve().parents[1])) )
WORK=Path(os.environ.get('OAS_PRESET_LINK_WORK',str(ROOT/'log')))
REPO=Path(os.environ.get('OAS_PRESET_LINK_REPO',str(ROOT)))
os.chdir(ROOT);sys.path.insert(0,str(ROOT))
def staged(name,path):
 spec=importlib.util.spec_from_file_location(name,REPO/path)
 module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
 return module
config=staged('tasks.Component.GeneralBattle.config_general_battle','tasks/Component/GeneralBattle/config_general_battle.py')
M=staged('tasks.Component.preset_team','tasks/Component/preset_team.py')
M.POPUP_CONFIRM.file=str(REPO/'tasks/Component/GeneralBattle/gb/preset_popup_confirm.png')
bw=staged('tasks.Component.GeneralBattle.battle_wait','tasks/Component/GeneralBattle/battle_wait.py')
gb=staged('tasks.Component.GeneralBattle.general_battle','tasks/Component/GeneralBattle/general_battle.py')
ss=staged('tasks.Component.SwitchSoul.switch_soul','tasks/Component/SwitchSoul/switch_soul.py')
from module.exception import ScriptError
rows=[]
def check(name,fn):
 try:fn();rows.append({'case':name,'passed':True})
 except Exception as error:rows.append({'case':name,'passed':False,'error':repr(error)})
def expect_failure(fn):
 try:fn()
 except ScriptError:return
 raise AssertionError('Expected ScriptError')
def image(path):return cv2.cvtColor(cv2.imdecode(np.fromfile(str(path),np.uint8),1),cv2.COLOR_BGR2RGB)
frame=image(REPO/'dev_tools/fixtures/preset_soul_link/popup.png')
def task_for(frame):
 task=object.__new__(gb.GeneralBattle)
 task.interval_timer={};task.device=NS(image=frame.copy())
 task.screenshot=lambda:None
 return task
def real_panel():
 assert M._panel(task_for(frame)).popup
check('actual dungeon shortcut panel is recognized',real_panel)
for index,center_y in enumerate((207,303,399),1):
 def icon(center_y=center_y):
  x,y=M._find_soul_button(frame,M.POPUP_TEAMS,center_y)
  assert 870<=x<=900 and abs(y-(center_y-28))<=8,(x,y,center_y)
 check(f'actual screenshot locates saved-soul icon within selected row {index}',icon)
def empty_row():
 empty=frame.copy();empty[255:350,800:920]=50
 expect_failure(lambda:M._find_soul_button(empty,M.POPUP_TEAMS,303))
check('missing selected-row button never matches another team',empty_row)
def shaded():
 dim=(frame*.4).astype(np.uint8)
 assert M._panel(task_for(dim)) is None
 expect_failure(lambda:M._find_soul_button(dim,M.POPUP_TEAMS,207))
check('dimmed background cannot apply souls or confirm a team',shaded)
check('off-screen row is rejected',lambda:expect_failure(lambda:M._find_soul_button(frame,M.POPUP_TEAMS,800)))
def ambiguous():
 fake=np.full_like(frame,100);template=cv2.resize(M.SwitchSoulAssets.I_SOU_SWITCH_1.image,None,fx=.8,fy=.8);h,w=template.shape[:2]
 fake[170:170+h,810:810+w]=template;fake[170:170+h,875:875+w]=template
 expect_failure(lambda:M._find_soul_button(fake,M.POPUP_TEAMS,207))
check('two equally plausible buttons are rejected',ambiguous)
P=M.Panel(M.BATTLE_GROUPS,M.BATTLE_TEAMS,object())
def selection(fail=False):
 calls=[];state=NS(open=True)
 task=NS(screenshot=lambda:None,I_PRESET_ENSURE=object(),I_PRESENT_LESS_THAN_5=object())
 task.appear=lambda *_:False
 def confirm(*args,**kwargs):calls.append('confirm');state.open=False;return True
 task.appear_then_click=confirm
 class Navigator:
  def __init__(self,*_):pass
  def select(self,index,layout):calls.append((layout.name,index));return layout.click_x,450
 def equip(*args):
  calls.append('souls')
  if fail:raise ScriptError('missing saved souls')
 with patch.object(M,'_panel',lambda _:P if state.open else None),patch.object(M,'IndexedPresetList',Navigator),patch.object(M,'apply_team_souls',equip):
  if fail:expect_failure(lambda:M.select_battle_team(task,True,9,9));assert 'confirm' not in calls
  else:
   M.select_battle_team(task,True,9,9)
   assert calls==[('战斗预设分组',9),('战斗预设队伍',9),'souls','confirm'],calls
check('group and team selection precede saved souls, and souls precede team confirmation',selection)
check('failed soul application prevents team confirmation',lambda:selection(True))
check('disabled preset performs no input or number validation',lambda:M.select_battle_team(object(),False,0,0))
def soul_application(kind='plain'):
 state=NS(overlay=kind in ('confirm','unknown'),clicks=[])
 task=NS(device=NS(image=frame.copy(),click=lambda *a,**kw:state.clicks.append('soul_button')),I_UI_CONFIRM=object(),screenshot=lambda:None)
 def confirm(*a,**kw):state.clicks.append('confirm_souls');state.overlay=False;return True
 task.appear_then_click=confirm
 def text(_):
  if kind=='failure':return '部分式神御魂不完整，无法一键装备'
  if kind=='unknown':return '是否购买道具'
  return '是否一键装备该队伍预设御魂' if state.overlay else '预设御魂装配成功'
 def panel(_):return None if state.overlay and state.clicks else P
 with patch.object(M,'_panel',panel),patch.object(M,'_find_soul_button',return_value=(630,410)),patch.object(M,'_soul_messages',text),patch.object(M,'foreground_rule',lambda *_:state.overlay and bool(state.clicks)),patch.object(M.time,'sleep',lambda *_:None):
  if kind in ('failure','unknown'):
   expect_failure(lambda:M.apply_team_souls(task,P,450));assert state.clicks==['soul_button'],state.clicks
  else:
   M.apply_team_souls(task,P,450)
   assert state.clicks==(['soul_button','confirm_souls'] if kind=='confirm' else ['soul_button']),state.clicks
for kind in ('plain','confirm','failure','unknown'):
 check('soul application handles '+kind,lambda kind=kind:soul_application(kind))
def changed_panel():
 task=NS(screenshot=lambda:None)
 with patch.object(M,'_panel',return_value=None):expect_failure(lambda:M.apply_team_souls(task,P,450))
check('changed panel is rejected before any soul click',changed_panel)
def priority(enabled=True,by_name=False):
 task=object.__new__(ss.SwitchSoul);task.get_task_name=lambda:'RealmRaid'
 task.config=NS(realm_raid=NS(general_battle_config=NS(preset_enable=enabled)))
 calls=[];task.click_preset=lambda:calls.append('open')
 task.switch_souls=lambda arg:calls.append(arg);task.switch_soul_by_name=lambda *args:calls.append(args)
 if by_name:task.run_switch_soul_by_name('group','team')
 else:task.run_switch_soul('1,2')
 assert calls==([] if enabled else ['open',('group','team') if by_name else (1,2)]),calls
for enabled in (True,False):
 for by_name in (False,True):check(f'legacy priority preset={enabled} by_name={by_name}',lambda enabled=enabled,by_name=by_name:priority(enabled,by_name))
def invalid_legacy():
 task=object.__new__(ss.SwitchSoul);task.get_task_name=lambda:'Orochi';task.config=NS(orochi=NS(general_battle_config=NS(preset_enable=True)))
 task.click_preset=lambda:(_ for _ in ()).throw(AssertionError('legacy input'))
 task.run_switch_soul('invalid separate configuration')
check('unneeded independent soul configuration need not be valid',invalid_legacy)
def hunt_branch():
 task=NS(get_task_name=lambda:'Hunt',kirin_day=False,config=NS(hunt=NS(kirin_battle_config=NS(preset_enable=True),netherworld_battle_config=NS(preset_enable=False))))
 assert not M.uses_linked_preset(task)
 task.kirin_day=True;assert M.uses_linked_preset(task)
check('Hunt uses the current kirin or netherworld branch only',hunt_branch)
def account_scope():
 task=NS(get_task_name=lambda:'Tako',config=NS(tako=NS(general_battle=NS(preset_enable=False)),orochi=NS(general_battle_config=NS(preset_enable=True))))
 assert not M.uses_linked_preset(task)
check('another task preset cannot suppress this task legacy souls',account_scope)
def model_rules():
 linked=config.GeneralBattleConfig(preset_enable=True,lock_team_enable=True,preset_group=99999,preset_team=99999)
 assert not linked.lock_team_enable and linked.preset_group==linked.preset_team==99999
 assert config.GeneralBattleConfig(preset_enable=False,lock_team_enable=True).lock_team_enable
check('enabled preset takes priority over lock while disabled behavior is preserved',model_rules)
def delegates():
 task=object.__new__(gb.GeneralBattle)
 with patch.object(M,'select_battle_team') as select:
  task.switch_preset_team(True,4,5);select.assert_called_once_with(task,True,4,5)
check('legacy battle entry delegates to shared linked selector',delegates)
def modern(fail=False):
 task=object.__new__(bw.BattleWait);state=bw.PerTaskPreset()
 pri=NS(options=bw.OptionPresetDefault(preset_enable=True,preset_group=2,preset_team=9),per_task=state)
 with patch.object(M,'select_battle_team',side_effect=ScriptError('soul failure') if fail else None) as select,patch.object(bw.runtime,'hook_enabled_update',return_value=bw.HookSignal.CONTINUE):
  if fail:expect_failure(lambda:task._bw_preset_default(NS(),pri));assert not state.done
  else:
   task._bw_preset_default(NS(),pri);assert state.done
   task._bw_preset_default(NS(),pri);select.assert_called_once_with(task,True,2,9)
check('new battle entry applies linked selection once per task',modern)
check('new battle entry never marks failed souls as complete',lambda:modern(True))
def premature_ready():
 task=object.__new__(bw.BattleWait);task.is_in_real_battle=lambda *_:False
 task.appear_then_click=lambda *_args,**_kw:(_ for _ in ()).throw(AssertionError('ready was pressed'))
 pri=NS(options=bw.OptionPrepareDefault(),per_battle=bw.PerBattlePrepare())
 pri.per_battle.timer=NS(reached=lambda:True)
 pub=NS(options={'preset':bw.OptionPresetDefault(preset_enable=True)})
 with patch.dict(bw.runtime.pri_ctx,{},clear=True):expect_failure(lambda:task._bw_prepare_default(pub,pri))
check('new battle timeout cannot click ready before loadout completes',premature_ready)
def language():
 for name in ('assets/i18n/zh-CN.json','module/config/i18n/zh-CN.json'):
  text=json.loads((REPO/name).read_text(encoding='utf-8'));assert '一键预设御魂' in text['preset_enable_help']
check('both Chinese UI dictionaries explain the unified behavior',language)

def gate_before_navigation():
    task=object.__new__(ss.SwitchSoul);task.get_task_name=lambda:'EvoZone'
    task.config=NS(evo_zone=NS(general_battle_config=NS(preset_enable=True)))
    assert not task.should_switch_soul(True) and not task.should_switch_soul(False)
    task.config.evo_zone.general_battle_config.preset_enable=False
    assert task.should_switch_soul(True) and not task.should_switch_soul(False)
check('task entry skips the independent-soul page itself when team preset is enabled',gate_before_navigation)
def dokan_priority():
    task=NS(get_task_name=lambda:'Dokan',config=NS(dokan=NS(dokan_config=NS(switch_preset_enable=True))))
    assert M.uses_linked_preset(task)
check('Dokan dynamic attack presets also override independent soul settings',dokan_priority)
def incomplete_team():
    task=NS(screenshot=lambda:None,I_PRESENT_LESS_THAN_5=object(),I_PRESET_ENSURE=object(),appear=lambda *_:True)
    class Navigator:
        def __init__(self,*_):pass
        def select(self,*_):return 425,450
    with patch.object(M,'_panel',return_value=P),patch.object(M,'IndexedPresetList',Navigator),patch.object(M,'apply_team_souls') as equip:
        expect_failure(lambda:M.select_battle_team(task,True,1,1));equip.assert_not_called()
check('an incomplete team is rejected before any soul equipment or team confirmation',incomplete_team)
def entry_semantics():
    import ast
    class Restore(ast.NodeTransformer):
        def visit_Call(self,node):
            node=self.generic_visit(node)
            if isinstance(node.func,ast.Attribute) and node.func.attr=='should_switch_soul' and isinstance(node.func.value,ast.Name) and node.func.value.id=='self':
                return node.args[0]
            return node
    changed=0
    for original in (ROOT/'tasks').glob('*/script_task.py'):
        target=REPO/original.relative_to(ROOT)
        if not target.exists():continue
        before=ast.dump(ast.parse(original.read_text(encoding='utf-8-sig')),include_attributes=False)
        after=ast.dump(Restore().visit(ast.parse(target.read_text(encoding='utf-8-sig'))),include_attributes=False)
        assert before==after,str(original)
        changed+=1
    target=REPO/'tasks/Exploration/base.py'
    if target.exists():
        original=ROOT/'tasks/Exploration/base.py'
        assert ast.dump(ast.parse(original.read_text(encoding='utf-8-sig')))==ast.dump(Restore().visit(ast.parse(target.read_text(encoding='utf-8-sig'))))
        changed+=1
    assert changed>=29,changed
# Source-entry AST compatibility is checked separately against the saved baseline.

report={'passed':sum(row['passed'] for row in rows),'failed':sum(not row['passed'] for row in rows),'cases':rows}
(WORK/'preset-soul-link-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))
sys.exit(bool(report['failed']))
