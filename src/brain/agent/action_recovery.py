"""按真实副作用类型恢复检查点，外部界面结果不明时禁止自动重放。"""
import json
import subprocess

from .boundary import AgentRefused
from .workspace_tools import parent_fd, read_at, fingerprint


def reconcile(record, store):
    """核对原文件或回收任务容器，返回仍需用户核对的原因。"""
    pending = record.get('pending') or {}
    kind = pending.get('recovery')
    if kind == 'file-transaction' or any(
            item['status'] in {'committing', 'rolling_back'} for item in record.get('edits', {}).values()):
        for path, item in record.get('edits', {}).items():
            if item['status'] not in {'committing', 'rolling_back'}:
                continue
            rollback = item['status'] == 'rolling_back'
            expected = item['after_hash'] if rollback else item['before_hash']
            desired = item['before_hash'] if rollback else item['after_hash']
            try:
                with parent_fd(record, path) as (fd, name):
                    current = fingerprint(read_at(fd, name)[0])
            except (OSError, AgentRefused) as error:
                return '无法核对上次文件事务：' + str(error)
            if current == desired:
                item['status'] = 'rolled_back' if rollback else 'committed'
            elif current == expected:
                item['status'] = 'committed' if rollback else 'staged'
            else:
                return '原文件与事务前后版本均不同，请核对冲突：' + path
        if kind == 'file-transaction':
            record['pending'] = None
        store.save(record)
    if kind == 'isolated-execution' or record.get('sandbox_pending'):
        state = record.get('sandbox_pending')
        if state:
            from .sandbox_tools import docker_command
            docker = docker_command()
            result = subprocess.run(docker + ['inspect', state['container']], capture_output=True, timeout=10)
            if result.returncode == 0:
                data = json.loads(result.stdout)
                if data[0].get('Config', {}).get('Labels', {}).get('local.jac.task') != record['task_id']:
                    raise AgentRefused('容器归属无法核对，未回收')
                subprocess.run(docker + ['rm', '-f', state['container']], check=True, capture_output=True, timeout=10)
            elif b'No such' not in result.stderr:
                return '无法核对上次代码容器是否已回收'
        record.pop('sandbox_pending', None)
        record['pending'] = None
        record.setdefault('notes', []).append('上次隔离执行已清理；输出未经确认时可重新执行无网络临时副本，不重放宿主动作。')
        store.save(record)
        return None
    if not pending or kind in {'read-or-private-draft', 'file-transaction'}:
        return None
    return '存在结果不明的外部界面动作；请先核对实际结果，在补充资料中说明结果并选择“已核对上次操作”'
