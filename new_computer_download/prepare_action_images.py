#!/usr/bin/env python3
"""通过宿主HTTPS下载官方镜像并逐层校验，避免国内网络下虚拟机无法访问Registry。"""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / '.cache/actions/images'
REGISTRY = 'https://registry-1.docker.io/v2/'
ACCEPT = 'application/vnd.oci.image.index.v1+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.docker.distribution.manifest.v2+json'


def request(url, token=None):
    """仅使用公开仓库匿名拉取令牌，不读取Docker登录凭据。"""
    headers = {'Accept': ACCEPT}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30)


def checked_download(repository, digest, token):
    """有界下载官方blob，缓存仅在SHA256匹配时复用。"""
    if not digest.startswith('sha256:') or len(digest) != 71:
        raise ValueError('镜像摘要无效')
    target = CACHE / digest[7:]
    if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == digest[7:]:
        return target
    temporary = target.with_suffix('.partial')
    with request(REGISTRY+repository+'/blobs/'+digest, token) as response, temporary.open('wb') as stream:
        total, checksum = 0, hashlib.sha256()
        while chunk := response.read(1024*1024):
            total += len(chunk)
            if total > 512*1024*1024:
                raise ValueError('单个镜像层超过512 MiB')
            checksum.update(chunk)
            stream.write(chunk)
    if checksum.hexdigest() != digest[7:]:
        raise ValueError('官方镜像blob摘要不符')
    temporary.replace(target)
    return target


def prepare(name, source, resolve=False):
    """固定arm64清单、校验压缩层和解压层摘要，生成Docker可导入的归档。"""
    repository = source['repository']
    if repository not in {'library/python', 'library/node'}:
        raise ValueError('仅支持登记的官方Python/Node仓库')
    with request('https://auth.docker.io/token?service=registry.docker.io&scope=repository:'+repository+':pull') as response:
        token = json.load(response)['token']
    ref = source['tag'] if resolve else source['manifest']
    with request(REGISTRY+repository+'/manifests/'+ref, token) as response:
        raw = response.read(1024*1024)
    if not resolve and 'sha256:'+hashlib.sha256(raw).hexdigest() != ref:
        raise ValueError('镜像清单摘要不符')
    manifest = json.loads(raw)
    if 'manifests' in manifest:
        target = next(item for item in manifest['manifests'] if item['platform'].get('os') == 'linux' and item['platform'].get('architecture') == 'arm64')
        with request(REGISTRY+repository+'/manifests/'+target['digest'], token) as response:
            raw = response.read(1024*1024)
        if 'sha256:'+hashlib.sha256(raw).hexdigest() != target['digest']:
            raise ValueError('arm64清单摘要不符')
        manifest = json.loads(raw)
    digest = 'sha256:'+hashlib.sha256(raw).hexdigest()
    config_id = manifest['config']['digest']
    config = checked_download(repository, config_id, token)
    info = json.loads(config.read_bytes())
    if info['architecture'] != 'arm64' or info['os'] != 'linux':
        raise ValueError('镜像平台不符')
    layers = []
    for index, item in enumerate(manifest['layers']):
        blob = checked_download(repository, item['digest'], token)
        layer = blob.with_suffix('.tar')
        if not layer.is_file():
            with gzip.open(blob, 'rb') as reader, layer.open('wb') as writer:
                total = 0
                while chunk := reader.read(1024*1024):
                    total += len(chunk)
                    if total > 1024*1024*1024:
                        raise ValueError('镜像解压层超限')
                    writer.write(chunk)
        with layer.open('rb') as stream:
            checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
        if 'sha256:'+checksum != info['rootfs']['diff_ids'][index]:
            raise ValueError('镜像解压层摘要不符')
        layers.append(layer)
    output = CACHE / (name+'-locked.tar')
    with tarfile.open(output, 'w') as archive:
        archive.add(config, arcname=config_id[7:]+'.json', recursive=False)
        names = []
        for layer in layers:
            entry = layer.stem+'/layer.tar'
            archive.add(layer, arcname=entry, recursive=False)
            names.append(entry)
        value = json.dumps([{'Config': config_id[7:]+'.json', 'RepoTags': ['jac-'+name+':locked'], 'Layers': names}]).encode()
        entry = tarfile.TarInfo('manifest.json')
        entry.size = len(value)
        archive.addfile(entry, io.BytesIO(value))
    return output, {**source, 'manifest': digest, 'config': config_id}


def install(resolve=False):
    """导入专用本机daemon并核对实际镜像ID；正常安装只读固定锁文件。"""
    CACHE.mkdir(parents=True, exist_ok=True)
    path = ROOT / 'actions.lock.json'
    lock = json.loads(path.read_text())
    sources = lock.get('image_sources') or {
        'python': {'repository': 'library/python', 'tag': '3.11-slim-bookworm'},
        'node': {'repository': 'library/node', 'tag': '22-bookworm-slim'}}
    socket = json.loads((CACHE.parent / 'runtime.json').read_text())['socket']
    docker = ['docker', '--host', 'unix://'+socket]
    for name, source in sources.items():
        output, exact = prepare(name, source, resolve)
        subprocess.run(docker+['load', '-i', str(output)], check=True, timeout=120)
        inspected = json.loads(subprocess.check_output(docker+['image', 'inspect', 'jac-'+name+':locked']))[0]
        config = json.loads((CACHE / exact['config'][7:]).read_bytes())
        if (inspected['RootFS']['Layers'] != config['rootfs']['diff_ids']
                or inspected['Config'] != config['config'] or inspected['Architecture'] != 'arm64'
                or inspected['Os'] != 'linux'):
            raise ValueError('导入后的实际镜像配置或层摘要不符')
        actual = inspected['Id']
        if not resolve and actual != lock['images'][name]:
            raise ValueError('导入镜像ID与已验收锁文件不一致')
        lock.setdefault('image_sources', {})[name] = exact
        lock['images'][name] = actual
        if resolve:
            path.write_text(json.dumps(lock, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        print(name+'官方镜像已逐层校验并导入', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resolve', action='store_true', help='开发维护时显式更新官方镜像摘要')
    install(parser.parse_args().resolve)
