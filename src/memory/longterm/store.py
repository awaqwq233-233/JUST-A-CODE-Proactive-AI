"""JSON 事实真源：原子事务、来源版本、删除屏障和有界待处理事件。"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
import uuid

VERSION = 2
MAX_FACTS = 4000
MAX_PENDING = 128
MAX_BYTES = 8 * 1024 * 1024


def default_directory():
    """返回跨启动目录稳定的用户存储位置；自定义目录仍使用固定 v2 子目录。"""
    base = os.environ.get('JAC_MEMORY_DIR')
    if not base:
        base = str(Path(os.environ.get('APPDATA', str(Path.home()))) / 'jac' / 'memory') if os.name == 'nt' else str(Path.home() / '.jac/memory')
    return Path(base).expanduser() / 'memory-v2'


def atomic_json(path, data):
    """写私有 UTF-8 临时文件、同步数据并原子替换，再同步父目录。"""
    encoded = json.dumps(data, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
    if len(encoded) > MAX_BYTES:
        raise ValueError('记忆存储达到容量上限，请整理后继续')
    temporary = path.with_name(path.name + '.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'wb') as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    if os.name != 'nt':
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def tokens(text):
    """用中文相邻双字与英文词进行轻量召回，避免只凭单个常用字命中。"""
    parts = re.findall(r'[\u4e00-\u9fff]+|[a-z0-9_]+', text.lower())
    return {word for part in parts for word in ([part] if not re.fullmatch(r'[\u4e00-\u9fff]+', part) else [part[i:i + 2] for i in range(len(part) - 1)])} - {'我的', '记得', '什么', '之前', '记住', '一下', '我是', '你还', '告诉', '喜欢'}


def slot_key(subject, attribute, scope='长期'):
    """同主体属性范围具有稳定身份，不依赖可变的事实内容。"""
    return hashlib.sha256('\0'.join((subject.strip(), attribute.strip(), scope.strip())).encode()).hexdigest()[:32]


class FactStore:
    """唯一写入者持有 JSON；索引和 Markdown 均无权恢复真源。"""

    def __init__(self, directory=None):
        """检查目录身份、取得进程锁并恢复已提交事务；损坏时拒绝旧备份回退。"""
        self.directory = Path(directory or default_directory()).expanduser().absolute()
        for parent in (self.directory, *self.directory.parents):
            if parent.is_symlink():
                raise ValueError('记忆目录不允许符号链接')
        self.directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.lock = threading.RLock()
        self.failed = False
        self.path = self.directory / 'state.json'
        self.journal = self.directory / 'commit.json'
        self.fd = os.open(self.directory / 'writer.lock', os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        try:
            if os.name == 'nt':
                import msvcrt
                os.write(self.fd, b'0')
                os.lseek(self.fd, 0, 0)
                msvcrt.locking(self.fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if self.journal.exists():
                committed = self._read(self.journal)
                # 完整事务能恢复损坏主文件，但旧残留事务不能覆盖更新的删除屏障。
                try:
                    primary = self._read(self.path)
                except (ValueError, OSError):
                    primary = None
                if primary and primary['sequence'] > committed['sequence']:
                    committed = primary
                atomic_json(self.path, committed)
                self.state = committed
                self.journal.unlink()
            else:
                self.state = self._read(self.path) if self.path.exists() else self._empty()
            if not self.path.exists():
                self._commit(self.state)
            self._migrate()
        except BaseException:
            os.close(self.fd)
            self.fd = None
            raise

    @staticmethod
    def _empty():
        """新存储无预置用户事实；应用身份由系统配置独立提供。"""
        return dict(version=VERSION, sequence=0, revision=0, epoch=0, facts={}, tombstones={}, pending={}, seen=[],
                    settings={'automatic': True}, migrated=False)

    @staticmethod
    def _read(path):
        """严格读取有界 JSON；拒绝符号链接、旧版本和损坏结构。"""
        if path.is_symlink() or path.stat().st_size > MAX_BYTES:
            raise ValueError('记忆文件路径或大小异常')
        data = json.loads(path.read_text(encoding='utf-8'))
        if (not isinstance(data, dict) or data.get('version') != VERSION or not isinstance(data.get('facts'), dict)
                or not isinstance(data.get('tombstones'), dict) or not isinstance(data.get('pending'), dict)
                or any(type(data.get(key)) is not int for key in ('sequence', 'revision', 'epoch'))):
            raise ValueError('记忆真源损坏或版本不兼容，已停止召回；未使用旧索引恢复')
        if len(data['facts']) > MAX_FACTS or len(data['pending']) > MAX_PENDING:
            raise ValueError('记忆真源容量异常')
        for key, fact in data['facts'].items():
            if (not isinstance(fact, dict) or fact.get('id') != key
                    or any(not isinstance(fact.get(name), str) for name in
                           ('subject', 'attribute', 'scope', 'content', 'evidence', 'updated_at', 'source_kind'))
                    or fact.get('status') not in {'active', 'candidate'}
                    or type(fact.get('revision')) is not int or type(fact.get('event_sequence')) is not int
                    or (fact.get('expires_at') is not None and not isinstance(fact['expires_at'], (int, float)))):
                raise ValueError('记忆事实结构损坏，已停止召回')
        if any(type(value) is not int for value in data['tombstones'].values()):
            raise ValueError('记忆删除屏障损坏，已停止召回')
        if (not isinstance(data.get('settings'), dict) or type(data['settings'].get('automatic')) is not bool
                or not isinstance(data.get('seen'), list) or type(data.get('migrated')) is not bool):
            raise ValueError('记忆设置或迁移状态损坏')
        for key, event in data['pending'].items():
            if (not isinstance(event, dict) or event.get('id') != key or not isinstance(event.get('text'), str)
                    or any(type(event.get(name)) is not int for name in ('sequence', 'epoch', 'attempts'))
                    or not isinstance(event.get('created_at'), (int, float))
                    or not isinstance(event.get('context_refs'), list)):
                raise ValueError('记忆待处理事件损坏')
        return data

    def _commit(self, state):
        """先提交恢复记录，再替换真源；失败后禁止继续使用旧内存副本。"""
        state = copy.deepcopy(state)
        state['sequence'] += 1
        if self.failed:
            raise RuntimeError('记忆写入故障，请重启后恢复事务')
        try:
            atomic_json(self.journal, state)
            atomic_json(self.path, state)
            self.state = state
            self.journal.unlink()
        except BaseException:
            self.failed = True
            raise

    def _migrate(self):
        """一次导入同根 v1；保留原来源，推测只作候选，不导入旧向量或种子。"""
        from .extraction import rule_fact
        if self.state['migrated']:
            return
        state = copy.deepcopy(self.state)
        path = self.directory.parent / 'memory.json'
        count = 0
        if path.is_file() and not path.is_symlink():
            if path.stat().st_size > MAX_BYTES:
                raise ValueError('旧记忆超过迁移上限')
            old = json.loads(path.read_text(encoding='utf-8'))
            if str(old.get('version', '')).split('.')[0] != '1':
                raise ValueError('旧记忆格式不兼容，未自动迁移')
            for item in old.get('facts', [])[:MAX_FACTS]:
                if not isinstance(item, dict) or str(item.get('id', '')).startswith('base_'):
                    continue
                content = item.get('content')
                if not isinstance(content, str) or not 1 <= len(content) <= 1000:
                    continue
                expires = None
                if item.get('ttl'):
                    try:
                        expires = datetime.fromisoformat(item['ttl'].replace('Z', '+00:00')).timestamp()
                    except (ValueError, TypeError, AttributeError):
                        continue
                    if expires <= time.time():
                        continue
                normalized = rule_fact(content) if item.get('source') in {'explicit', 'manual'} else []
                attribute = normalized[0]['attribute'] if normalized else '旧记录:' + str(item.get('id'))
                key = slot_key('用户', attribute, '长期')
                state['facts'][key] = dict(id=key, subject='用户', attribute=attribute, scope='长期', content=content,
                    revision=1, source_kind='legacy:' + str(item.get('source')), evidence=content,
                    event_id='migration', event_sequence=0, updated_at=str(item.get('updated_at', '')),
                    expires_at=expires, status='active' if item.get('source') in {'explicit', 'manual'} else 'candidate')
                count += 1
        state['migrated'] = True
        state['migration_count'] = count
        state['revision'] += bool(count)
        self._commit(state)

    def accept(self, text, source):
        """持久接受必要原话，分配严格递增顺序；重复事件及队列满均不重复接受。"""
        if not isinstance(text, str) or not 1 <= len(text) <= 4000:
            raise ValueError('记忆输入为空或超限')
        identity = str(source.get('utterance_id') or uuid.uuid4().hex)
        with self.lock:
            if identity in self.state['seen'] or identity in self.state['pending']:
                return None
            if len(self.state['pending']) >= MAX_PENDING:
                raise ValueError('记忆待处理已满，本句未接受，请稍后重试')
            state = copy.deepcopy(self.state)
            event = dict(id=identity, text=text, kind=source.get('kind', 'text'), sequence=state['sequence'] + 1,
                         epoch=state['epoch'], created_at=time.time(), attempts=0, explicit=bool(source.get('explicit')),
                         context_refs=source.get('context_refs', [])[:8])
            state['pending'][identity] = event
            self._commit(state)
            return copy.deepcopy(event)

    def finish(self, event_id, proposals=()):
        """校验来源片段与当前版本后更新事实；迟到提炼、删除前事件不能复活旧值。"""
        with self.lock:
            state = copy.deepcopy(self.state)
            event = state['pending'].pop(event_id, None)
            if not event:
                return []
            changed = []
            if event['epoch'] == state['epoch']:
                for proposal in proposals:
                    subject, attribute = proposal['subject'].strip(), proposal['attribute'].strip()
                    content, evidence = proposal['content'].strip(), proposal['evidence'].strip()
                    scope = proposal.get('scope', '长期')
                    if not subject or not attribute or not content or not evidence or evidence not in event['text']:
                        raise ValueError('事实缺少可核对的用户原话')
                    if max(map(len, (subject, attribute, scope))) > 80 or len(content) > 600:
                        raise ValueError('事实字段超限')
                    key = proposal.get('target_id') or slot_key(subject, attribute, scope)
                    prior = state['facts'].get(key)
                    if prior is None and len(state['facts']) >= MAX_FACTS:
                        raise ValueError('记忆条数达到容量上限')
                    if proposal.get('target_id') and (not prior or prior['revision'] != proposal.get('expected_revision')):
                        continue
                    if state['tombstones'].get(key, 0) >= event['sequence'] or (prior and prior['event_sequence'] > event['sequence']):
                        continue
                    if prior and prior['content'] == content and prior['status'] == proposal.get('status', 'active'):
                        prior.update(event_sequence=event['sequence'], event_id=event_id)
                        continue
                    state['facts'][key] = dict(id=key, subject=subject, attribute=attribute, scope=scope, content=content,
                        revision=max(prior['revision'] + 1 if prior else 1, state['revision'] + 1), source_kind=event['kind'], evidence=evidence,
                        event_id=event_id, event_sequence=event['sequence'], updated_at=datetime.now(timezone.utc).isoformat(),
                        expires_at=proposal.get('expires_at'), status=proposal.get('status', 'active'))
                    changed.append(key)
            state['seen'] = (state['seen'] + [event_id])[-1024:]
            if changed:
                state['revision'] += 1
            self._commit(state)
            return changed

    def forget(self, identities=None):
        """事务删除并废弃所有在途提炼；保留仅含身份/顺序的防复活标记。"""
        with self.lock:
            state = copy.deepcopy(self.state)
            keys = list(state['facts']) if identities is None else list(identities)
            for key in keys:
                state['facts'].pop(key, None)
                state['tombstones'][key] = state['sequence'] + 1
            state['pending'] = {}
            state['epoch'] += 1
            state['revision'] += 1
            self._commit(state)
            # v1 不再作为恢复源；删除旧副本，避免用户后来误导入删除前数据。
            for name in ('memory.json', 'memory.json.bak'):
                legacy = self.directory.parent / name
                if self.state.get('migration_count') and legacy.is_file() and not legacy.is_symlink():
                    legacy.unlink()
            self.write_view()
            return len(keys)

    def snapshot(self):
        """返回脱离内部锁的当前有效事实，过期与候选永不作为已确认事实。"""
        with self.lock:
            if self.failed:
                raise RuntimeError('记忆写入故障，已停止使用旧事实')
            return copy.deepcopy([fact for fact in self.state['facts'].values() if fact['status'] == 'active'
                                  and (fact.get('expires_at') is None or fact['expires_at'] > time.time())])

    def search(self, query, vector_hits=(), limit=5):
        """混合候选最终回查真源与版本；只输出相关且仍有效的事实。"""
        query_tokens = tokens(query)
        scores = {}
        for hit in vector_hits:
            if hit.get('distance', 2) < .65:
                scores[hit['id']] = (hit['revision'], 1 - hit['distance'])
        ranked = []
        for fact in self.snapshot():
            overlap = query_tokens & tokens(fact['evidence'] + fact['content'] + fact['attribute'])
            lexical = len(overlap) / max(1, min(len(query_tokens), 6))
            if query_tokens & tokens(fact['attribute']):
                lexical = max(lexical, .6)
            vector = scores.get(fact['id'], (0, 0))
            score = max(lexical, vector[1] if vector[0] == fact['revision'] else 0)
            if score >= .3:
                ranked.append((score, fact['event_sequence'], fact))
        return [row[2] for row in sorted(ranked, key=lambda row: row[:2], reverse=True)[:limit]]

    def settings(self, **changes):
        """持久保存自动记忆开关；关闭时清除未处理的自动候选。"""
        with self.lock:
            if changes:
                if set(changes) != {'automatic'} or type(changes['automatic']) is not bool:
                    raise ValueError('记忆设置无效')
                state = copy.deepcopy(self.state)
                state['settings'].update(changes)
                if not changes['automatic']:
                    state['pending'] = {key: event for key, event in state['pending'].items() if event.get('explicit')}
                state['epoch'] += 1
                for event in state['pending'].values():
                    event['epoch'] = state['epoch']
                self._commit(state)
            return dict(self.state['settings'])

    def defer(self, event_id, yielded=False):
        """有限重试失败候选，持久保存次数并让后续事件继续，不永久堵塞队首。"""
        with self.lock:
            state = copy.deepcopy(self.state)
            event = state['pending'].get(event_id)
            if event:
                event['attempts'] += not yielded
                event['retry_at'] = time.time() + min(60, 15 * max(1, event['attempts']))
                event['needs_input'] = event['attempts'] >= 3
                self._commit(state)

    def resolve_pending(self, event_id, retry=False):
        """用户可重试或丢弃指定候选；未确认原话不会永久阻塞回答。"""
        with self.lock:
            if not retry:
                return self.finish(event_id)
            state = copy.deepcopy(self.state)
            event = state['pending'].get(event_id)
            if not event:
                raise ValueError('待整理内容已变化，请刷新')
            event.update(attempts=0, retry_at=0, needs_input=False)
            self._commit(state)
            return True

    def expire(self):
        """到期事实提交版本失效，撤销旧上下文；不执行向量检索或清除无关候选。"""
        with self.lock:
            state = copy.deepcopy(self.state)
            keys = [key for key, fact in state['facts'].items()
                    if fact.get('expires_at') is not None and fact['expires_at'] <= time.time()]
            if not keys:
                return False
            for key in keys:
                state['facts'].pop(key)
                state['tombstones'][key] = state['sequence'] + 1
            state['revision'] += 1
            self._commit(state)
            return True

    def write_view(self):
        """从有效真源重建人类可读视图；该文件从不反向导入或参与索引。"""
        lines = ['# J.A.C. 长期记忆', '', '由 JSON 真源生成；请通过记忆管理界面更正或删除。', '']
        for fact in self.snapshot():
            lines.extend([f"- {fact['content']}", f"  来源：{fact['source_kind']}；时间：{fact['updated_at']}；编号：{fact['id']}"])
        target = self.directory / 'MEMORY.md'
        temporary = self.directory / 'MEMORY.md.tmp'
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write('\n'.join(lines) + '\n')
        os.replace(temporary, target)
        daily = self.directory / 'daily'
        daily.mkdir(mode=0o700, exist_ok=True)
        grouped = {}
        for fact in self.snapshot():
            day = fact['updated_at'][:10]
            if re.fullmatch(r'\d{4}-\d{2}-\d{2}', day):
                grouped.setdefault(day, []).append(fact['content'])
        for path in daily.glob('*.md'):
            if path.stem not in grouped and not path.is_symlink():
                path.unlink()
        for day, entries in grouped.items():
            path = daily / (day + '.md')
            fd = os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                handle.write('# ' + day + ' 有效记忆\n\n' + '\n'.join('- ' + text for text in entries) + '\n')

    def close(self):
        """释放进程写锁；所有已确认变更已同步提交，不依赖退出时补写。"""
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
