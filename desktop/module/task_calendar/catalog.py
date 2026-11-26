from .store import task_key

WEEKLY={'weekly_trifles','weekly_purchase','secret','true_orochi','draft_duel','duel','demon_retreat'}
HIDDEN={'restart','script','global_game','souls_tidy','guild_activity_monitor'}

def reward(currency,kind,detail):return {'currency':currency,'kind':kind,'detail':detail}

def rewards(key,config):
    current=config.get(key,{})
    if key=='weekly_trifles':
        options=current.get('trifles',{});result=[]
        if options.get('share_collect'):result.append(reward('ticket','free','图鉴分享蓝票，依游戏每周领取状态'))
        if options.get('share_area_boss') or options.get('share_secret'):result.append(reward('jade','free','地域鬼王/秘闻分享勾玉，仅已启用的分享项'))
        return result
    if key=='daily_trifles':
        options=current.get('trifles_config',{});result=[]
        if options.get('luck_msg') or options.get('store_sign'):result.append(reward('jade','conditional','吉闻祝福/免费礼包可能获得，依实际奖励'))
        if options.get('store_sign'):result.append(reward('ticket','conditional','免费礼包按签到轮次可能获得蓝票'))
        return result
    if key=='weekly_purchase':
        result=[]
        if current.get('itachi_coin_shop',{}).get('itachi_coin_buy_jade'):result.append(reward('jade','exchange','使用鼬乐币兑换，需已有兑换资源'))
        blue=[name for name,options in current.items() if isinstance(options,dict) and options.get('enable') and options.get('mystery_amulet')]
        if blue:result.append(reward('ticket','exchange','已启用商店兑换蓝票，需功勋/荣誉等资源'))
        return result
    if key=='mystery_shop' and current.get('shop_config',{}).get('mystery_amulet'):
        return [reward('ticket','purchase','神秘商店购买蓝票，会花费勾玉；不是免费领取')]
    jade={
      'area_boss':('free','地域鬼王挑战奖励，数量按解锁次数与实际完成情况'),
      'realm_raid':('free','结界突破阶段奖励，按挑战进度'),
      'wanted_quests':('conditional','勾玉悬赏/协作，依接到的悬赏类型'),
      'find_jade':('conditional','勾玉协作悬赏，依接到的任务'),
      'secret':('free','秘闻/竞速/百战阶段奖励，按完成层数'),
      'duel':('conditional','斗技阶段/周结算奖励，脚本结束不等于周结算已到账'),
      'draft_duel':('conditional','百鬼弈胜场奖励，按实际进度'),
      'kekkai_utilize':('conditional','太鼓结界卡蹭卡收益，依结界卡类型及收取情况'),
      'kekkai_activation':('conditional','太鼓结界卡收益，依挂卡类型及收取情况'),
      'pets':('conditional','宠物协战及礼物可能获得，依实际奖励'),
      'talisman_pass':('conditional','花合战活跃奖励/阶段奖励，依当期可领取内容')}
    result=[reward('jade',*jade[key])] if key in jade else []
    return result

def tasks(config,translations=None):
    translations=translations or {};rows=[]
    for raw,body in config.items():
        if not isinstance(body,dict) or not isinstance(body.get('scheduler'),dict):continue
        key=task_key(raw)
        if key in HIDDEN:continue
        scheduler=body['scheduler'];command=''.join(x[:1].upper()+x[1:] for x in key.split('_'))
        rows.append({'id':key,'command':command,'name':str(translations.get(command) or translations.get(raw) or command),
          'period':'weekly' if key in WEEKLY else 'daily','enabled':scheduler.get('enable') is True,
          'next_run':str(scheduler.get('next_run') or ''),'rewards':rewards(key,config)})
    rows.sort(key=lambda r:(not r['enabled'],not bool(r['rewards']),r['period']=='weekly',r['name']))
    return rows
