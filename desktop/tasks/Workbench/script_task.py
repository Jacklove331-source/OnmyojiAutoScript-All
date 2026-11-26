# This file uses the following encoding: utf-8
"""Workbench 任务入口：本地档案 → 分析 → 报表。

不点游戏、不读内存；需要设备数据时走 ingest 的截图/OCR 通道（source=screen）。
"""
import json
import os
import time

from module.exception import TaskEnd
from module.logger import logger
from tasks.base_task import BaseTask
from tasks.Workbench import features as F
from tasks.Workbench.engine import Catalog
from tasks.Workbench.store import DEFAULT_DIR, Store


class ScriptTask(BaseTask):

    def run(self):
        opt = self.config.workbench.workbench
        logger.hr('Workbench', level=1)

        root = Store()
        store = root.for_profile()
        catalog = Catalog()
        inventory = store.inventory()
        souls, owned = inventory["souls"], inventory["owned"]
        src = getattr(opt, "source", "json")
        if src == "json":
            souls, owned = self._load_json(opt, inventory)
        elif src == "screen":
            souls = self._souls_from_screen(opt) or souls
        elif src == "paz-db":
            from tasks.Workbench.ingest import paz_db

            db_path = getattr(opt, "paz_db_path", "") or None
            matches = paz_db.profiles(db_path)["profiles"]
            pid = store.profile_id if any(p["id"] == store.profile_id for p in matches) else None
            data = paz_db.load(db_path, pid, include_extras=True)
            if data["ok"]:
                souls, owned = data["souls"], data["owned"]
                store = root.save_archive(data["profile"], souls, owned, "paz-db",
                                          extras=data.get("extras"), activate=False)
                logger.info("工作台：从平安志库读入御魂 %d / 式神 %d"
                            % (len(souls), len(owned)))
            else:
                logger.warning("工作台：%s，使用当前档案的本地数据" % data["reason"])
        elif src == "paz-snapshot":
            snapshot_souls, snapshot_owned = self._load_paz_snapshot(opt)
            if snapshot_souls:
                souls, owned = snapshot_souls, snapshot_owned

        if not souls:
            logger.warning('工作台：没有御魂数据，先导入档案或跑一次 source=screen 的读取')
            self._schedule_next()
            raise TaskEnd('Workbench empty')

        report = F.soul_report(souls, standard_id=opt.score_standard or None, catalog=catalog)
        assets = F.asset_summary(souls)
        panel = None
        target = opt.panel_shikigami
        if not target and owned:
            target = owned[0].get("id") or owned[0].get("name")
        if target:
            row = catalog.panel_of(str(target))
            if row:
                panel = F.panel_calc(row)
        head_tail = F.head_tail(souls)
        roster = F.roster(owned, catalog)
        overview = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "standard": report["standard"],
            "soul_count": report["count"],
            "by_decision": report["by_decision"],
            "action_batch": report["action_batch"],
            "assets": assets,
            "panel": panel,
            "head_tail": {k: {"candidates": v["candidates"], "top": v["top"]}
                          for k, v in head_tail.items()},
            "roster": {k: v for k, v in roster.items() if k != "rows"},
        }

        out_dir = opt.export_dir or 'log/workbench'
        os.makedirs(out_dir, exist_ok=True)
        out = os.path.join(out_dir, 'report-%s.json' % time.strftime('%Y%m%d-%H%M%S'))
        with open(out, 'w', encoding='utf-8') as fh:
            json.dump(overview, fh, ensure_ascii=False, indent=1)
        logger.info('工作台：御魂 %d 条，决策 %s，报表 %s'
                    % (report["count"], report["by_decision"], out))

        from tasks.Workbench.guild_monitor import collect_metrics
        collect_metrics(store, report, note=out)
        from tasks.Workbench import monitor as M
        M.sample(store, souls, owned, report=report, note=out)
        self._schedule_next()
        raise TaskEnd('Workbench')

    # ---------------- 内部 ----------------
    def _load_paz_snapshot(self, opt):
        """读平安志 mumu-snapshot 的 result.json（由外部注入器产出）。"""
        from tasks.Workbench.ingest import paz_snapshot as P
        path = getattr(opt, "paz_snapshot_file", "") or "config/workbench/paz-snapshot.json"
        if not os.path.isfile(path):
            logger.warning('工作台：找不到平安志快照 %s' % path)
            return [], []
        try:
            data = P.load_file(path)
        except Exception as exc:
            logger.warning('工作台：快照解析失败 %s' % exc)
            return [], []
        v = P.validate(data["souls"])
        logger.info('工作台：快照 角色=%s 御魂=%d 式神=%d 槽位校验违规=%d'
                    % (data["player"].get("name"), len(data["souls"]),
                       len(data["owned"]), v["violations"]))
        return data["souls"], [{"id": s["id"], "star": s.get("star"),
                                "level": s.get("level"), "awakened": s.get("awakened")}
                               for s in data["owned"]]

    def _souls_from_screen(self, opt):
        """实机通道：抓帧 → ppocr → 御魂对象。失败返回空列表并记日志。"""
        try:
            from tasks.Workbench.ingest import screen as S
        except Exception as exc:
            logger.warning('工作台：实机通道不可用 %s' % exc)
            return []
        serial = ''
        try:
            serials = self.config.script.device.serial
            serial = serials[0] if isinstance(serials, (list, tuple)) and serials else str(serials)
        except Exception:
            pass
        if not serial:
            logger.warning('工作台：未配置设备 serial，无法走实机通道')
            return []
        out_dir = opt.export_dir or 'log/workbench'
        os.makedirs(out_dir, exist_ok=True)
        frame = os.path.join(out_dir, 'screen-%s.png' % time.strftime('%Y%m%d-%H%M%S'))
        res = S.snapshot_from_screen(serial, frame,
                                     dump_json=os.path.join(out_dir, 'screen-snapshot.json'))
        if not res.get('ok'):
            logger.warning('工作台：实机抓帧/OCR 失败 %s' % res)
            return []
        logger.info('工作台：实机 OCR 行 %d，还原御魂 %d 条（未解析 %d 行）'
                    % (res['rows'], len(res['souls']), len(res['unparsed'])))
        if res['souls']:
            store_souls = res['souls']
            return store_souls
        return []

    def _load_json(self, opt, inventory):
        from tasks.Workbench.ingest import load_archive

        profile_path = getattr(opt, "profile_file", "")
        if profile_path and os.path.abspath(profile_path) != os.path.abspath(os.path.join(DEFAULT_DIR, "profile.json")):
            try:
                data = load_archive(profile_path)
                return data["souls"], data["owned"]
            except (ValueError, OSError, TypeError, KeyError, AttributeError) as exc:
                logger.warning('工作台：档案读取失败 %s' % exc)
        values = []
        for field in ("souls", "owned"):
            path = getattr(opt, field + "_file", "")
            rows = inventory[field]
            if path and os.path.abspath(path) != os.path.abspath(os.path.join(DEFAULT_DIR, field + ".json")):
                try:
                    with open(path, encoding="utf-8-sig") as fh:
                        data = json.load(fh)
                    if isinstance(data, list):
                        rows = data
                except (ValueError, OSError) as exc:
                    logger.warning('工作台：%s 读取失败 %s' % (path, exc))
            values.append(rows)
        return values

    def _schedule_next(self):
        try:
            self.config.task_delay(task='Workbench', success=True)
        except Exception as exc:  # 调度器接口缺失时不阻塞任务主体
            logger.warning('工作台：设置下次运行失败 %s' % exc)
