# This Python file uses the following encoding: utf-8
# copy from alas https://github.com/LmeSzinc/AzurLaneAutoScript
from deploy.config import DeployConfig
from deploy.logger import logger
from deploy.utils import *


class GitManager(DeployConfig):
    @cached_property
    def git(self):
        return self.filepath('GitExecutable')

    @staticmethod
    def remove(file):
        try:
            os.remove(file)
            logger.info(f'Removed file: {file}')
        except FileNotFoundError:
            logger.info(f'File not found: {file}')

    def _git_output(self, args: str) -> str:
        """执行 git 命令并返回输出（失败返回空串）。"""
        import subprocess
        try:
            result = subprocess.run(f'"{self.git}" {args}', shell=True, capture_output=True,
                                    text=True, encoding='utf-8', errors='ignore')
            return (result.stdout or '').strip()
        except Exception:  # noqa: BLE001
            return ''

    def _update_guard(self, source: str, branch: str, keep_changes: bool) -> bool:
        """更新前的安全检查：分支不符/本地有未推送提交/有未提交改动时拒绝 reset --hard。

        返回 True 表示可以安全更新。这是 2026-10-26 事故的根因防护：
        更新器执行 `git reset --hard origin/master` 把整合版分支和未提交改动一起冲掉。
        """
        current = self._git_output('rev-parse --abbrev-ref HEAD')
        if current and current != branch:
            logger.error(f'Update guard: 当前分支是 {current}，与目标分支 {branch} 不一致，'
                         f'拒绝自动更新（避免覆盖整合版代码）')
            return False
        local_only = self._git_output(f'rev-list --count {source}/{branch}..HEAD')
        if local_only.isdigit() and int(local_only) > 0:
            logger.error(f'Update guard: 本地有 {local_only} 个未推送提交，拒绝自动更新')
            return False
        # 无论 keep_changes 与否，一律拒绝脏工作区：
        # keep_changes=True 只是 stash→pull→pop，stash 失败时仍会 reset --hard，
        # 未跟踪文件也不在 stash 里，更新后可能丢失。
        dirty = self._git_output('status --porcelain')
        if dirty:
            count = len([line for line in dirty.splitlines() if line.strip()])
            logger.error(f'Update guard: 工作区有 {count} 项未提交改动/未跟踪文件，拒绝自动更新')
            return False
        untracked = self._git_output('ls-files --others --exclude-standard')
        if untracked:
            logger.error('Update guard: 存在未跟踪文件，拒绝自动更新（更新可能覆盖它们）')
            return False
        logger.info('Update guard: 检查通过，继续更新')
        return True

    def git_repository_init(
            self, repo, source='origin', branch='master',
            proxy='', ssl_verify=True, keep_changes=False
    ):
        logger.hr('Git Init', 1)

        # 安全检查：分支不符/有未推送提交/有未提交改动时拒绝自动更新，
        # 避免 2026-10-26 那种 `git reset --hard origin/master` 覆盖整合版的事故重演。
        if not self._update_guard(source, branch, keep_changes):
            logger.warning('Git update skipped by update guard')
            return
        if not self.execute(f'"{self.git}" init', allow_failure=True):
            self.remove('./.git/config')
            self.remove('./.git/index')
            self.remove('./.git/HEAD')
            self.execute(f'"{self.git}" init')

        logger.hr('Set Git Proxy', 1)
        if proxy:
            self.execute(f'"{self.git}" config --local http.proxy {proxy}')
            self.execute(f'"{self.git}" config --local https.proxy {proxy}')
        else:
            self.execute(f'"{self.git}" config --local --unset http.proxy', allow_failure=True)
            self.execute(f'"{self.git}" config --local --unset https.proxy', allow_failure=True)

        if ssl_verify:
            self.execute(f'"{self.git}" config --local http.sslVerify true', allow_failure=True)
        else:
            self.execute(f'"{self.git}" config --local http.sslVerify false', allow_failure=True)

        logger.hr('Set Git Repository', 1)
        if not self.execute(f'"{self.git}" remote set-url {source} {repo}', allow_failure=True):
            self.execute(f'"{self.git}" remote add {source} {repo}')

        logger.hr('Fetch Repository Branch', 1)
        self.execute(f'"{self.git}" fetch {source} {branch}')

        logger.hr('Pull Repository Branch', 1)
        # Remove git lock
        for lock_file in [
            './.git/index.lock',
            './.git/HEAD.lock',
            './.git/refs/heads/master.lock',
        ]:
            if os.path.exists(lock_file):
                logger.info(f'Lock file {lock_file} exists, removing')
                os.remove(lock_file)
        if keep_changes:
            if self.execute(f'"{self.git}" stash', allow_failure=True):
                self.execute(f'"{self.git}" pull --ff-only {source} {branch}')
                if self.execute(f'"{self.git}" stash pop', allow_failure=True):
                    pass
                else:
                    # No local changes to existing files, untracked files not included
                    logger.info('Stash pop failed, there seems to be no local changes, skip instead')
            else:
                logger.info('Stash failed, this may be the first installation, drop changes instead')
                self.execute(f'"{self.git}" reset --hard {source}/{branch}')
                self.execute(f'"{self.git}" pull --ff-only {source} {branch}')
        else:
            self.execute(f'"{self.git}" reset --hard {source}/{branch}')
            self.execute(f'"{self.git}" pull --ff-only {source} {branch}')

        logger.hr('Show Version', 1)
        self.execute(f'"{self.git}" --no-pager log --no-merges -1')

    def git_install(self):
        logger.hr('Update Alas', 0)

        if not self.AutoUpdate:
            logger.info('AutoUpdate is disabled, skip')
            return

        self.git_repository_init(
            repo=self.Repository,
            source='origin',
            branch=self.Branch,
            proxy=self.GitProxy,
            ssl_verify=self.SSLVerify,
            keep_changes=self.KeepLocalChanges,
        )
