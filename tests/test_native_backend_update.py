"""锁定后端补丁升级：已知旧版、未知改动拒绝与失败回滚。"""

import hashlib
import subprocess

import pytest

from new_computer_download import update_native_backend as updater


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    """创建仅含一行源码的临时 Git 库，验证真实补丁应用而不访问模型。"""
    repo = tmp_path / 'engine'
    repo.mkdir()
    updater.git(repo, 'init', '--quiet')
    source = repo / 'source.cpp'
    source.write_text('base\n')
    updater.git(repo, 'add', 'source.cpp')
    updater.git(repo, '-c', 'user.name=Test', '-c', 'user.email=test@example.test', 'commit', '--quiet', '-m', 'base')
    revision = updater.git(repo, 'rev-parse', 'HEAD').strip()
    source.write_text('old-approved\n')
    old = updater.git(repo, 'diff', 'HEAD', '--no-ext-diff')
    source.write_text('new-approved\n')
    new = updater.git(repo, 'diff', 'HEAD', '--no-ext-diff')
    patch = tmp_path / 'new.patch'
    patch.write_text(new)
    source.write_text('old-approved\n')
    monkeypatch.setattr(updater, 'ROOT', tmp_path)
    lock = dict(repositories=dict(engine=dict(commit=revision, patch='new.patch',
        previous_patch_sha256=[hashlib.sha256(old.strip().encode()).hexdigest()])))
    return repo, source, lock, patch


def test_known_old_patch_updates_then_is_idempotent(checkout):
    """精确旧版能迁移，第二次升级不重复应用或覆盖其他内容。"""
    repo, source, lock, _ = checkout
    assert updater.update_engine(repo, lock)
    assert source.read_text() == 'new-approved\n'
    assert updater.update_engine(repo, lock) is False


def test_clean_pinned_checkout_can_apply_current_combined_patch(checkout):
    """新机器从固定 commit 安装相同组合补丁。"""
    repo, source, lock, _ = checkout
    source.write_text('base\n')
    assert updater.update_engine(repo, lock)
    assert source.read_text() == 'new-approved\n'


def test_unknown_changes_and_wrong_revision_are_preserved(checkout):
    """用户未登记源码不能被自动覆盖，错误 commit 也保持原内容。"""
    repo, source, lock, _ = checkout
    source.write_text('user change\n')
    with pytest.raises(ValueError, match='未登记'):
        updater.update_engine(repo, lock)
    assert source.read_text() == 'user change\n'
    lock['repositories']['engine']['commit'] = '0' * 40
    with pytest.raises(ValueError, match='commit'):
        updater.update_engine(repo, lock)
    assert source.read_text() == 'user change\n'


def test_failed_new_patch_restores_approved_previous_diff(checkout, monkeypatch):
    """旧补丁反向移除后发生新补丁失败，必须恢复旧源码和原始 diff。"""
    repo, source, lock, patch = checkout
    git = updater.git
    original = git(repo, 'diff', 'HEAD', '--no-ext-diff')
    def fail_new(directory, *arguments):
        """仅在真正应用新版时模拟 I/O 失败，回滚命令保持真实执行。"""
        if arguments == ('apply', str(patch)):
            raise subprocess.CalledProcessError(1, arguments)
        return git(directory, *arguments)
    monkeypatch.setattr(updater, 'git', fail_new)
    with pytest.raises(subprocess.CalledProcessError):
        updater.update_engine(repo, lock)
    assert source.read_text() == 'old-approved\n'
    assert git(repo, 'diff', 'HEAD', '--no-ext-diff') == original
