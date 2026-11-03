"""独立 CPU 向量进程；唯一 Chroma 访问者，标准输入输出只传有界 JSON。"""
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MODEL = ROOT / '.cache/memory/models/bge-small-zh-v1.5'


def verify_model(directory):
    """只核验锁定本地文件；运行期绝不下载或切换模型。"""
    if sys.version_info[:2] != (3, 11):
        raise ValueError('记忆向量进程必须使用 Python 3.11')
    lock = json.loads((ROOT / 'memory.lock.json').read_text(encoding='utf-8'))
    from importlib.metadata import version
    for package, expected in lock['packages'].items():
        if version(package) != expected:
            raise ValueError('记忆运行库版本不符：' + package)
    for name, expected in lock['files'].items():
        path = Path(directory) / name
        if not path.is_file() or path.is_symlink():
            raise ValueError('记忆模型缺失：' + name)
        with path.open('rb') as handle:
            if hashlib.file_digest(handle, 'sha256').hexdigest() != expected:
                raise ValueError('记忆模型哈希不符：' + name)
    return lock


class VectorEngine:
    """Chroma 仅存编号、事实版本和向量；正文只存在 JSON 真源。"""

    def __init__(self, directory, model_dir=DEFAULT_MODEL):
        """离线加载 INT8 BGE，限制 CPU 并禁用遥测和默认 embedding 下载。"""
        os.environ.update(ANONYMIZED_TELEMETRY='False', HF_HUB_OFFLINE='1', TOKENIZERS_PARALLELISM='false')
        verify_model(model_dir)
        import chromadb
        from chromadb.config import Settings
        import onnxruntime as ort
        from tokenizers import Tokenizer
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        self.model = ort.InferenceSession(str(Path(model_dir) / 'onnx/model_int8.onnx'), options, providers=['CPUExecutionProvider'])
        self.tokenizer = Tokenizer.from_file(str(Path(model_dir) / 'tokenizer.json'))
        self.tokenizer.enable_truncation(max_length=512)
        self.tokenizer.enable_padding(pad_id=0, pad_token='[PAD]')
        self.client = chromadb.PersistentClient(path=str(directory), settings=Settings(anonymized_telemetry=False))
        self.collection = self.client.get_or_create_collection('jac-bge-v2-context1', embedding_function=None,
            configuration={'hnsw': {'space': 'cosine', 'num_threads': 1}})

    def embed(self, texts, query=False):
        """官方中文查询指令仅用于 query；CLS 池化后 L2 归一化。"""
        import numpy as np
        if not 1 <= len(texts) <= 5 or any(not isinstance(text, str) or len(text) > 4000 for text in texts):
            raise ValueError('向量批次或文本超限')
        encoded = self.tokenizer.encode_batch([('为这个句子生成表示以用于检索相关文章：' if query else '') + text for text in texts])
        values = dict(input_ids=np.asarray([item.ids for item in encoded], dtype=np.int64),
                      attention_mask=np.asarray([item.attention_mask for item in encoded], dtype=np.int64),
                      token_type_ids=np.asarray([item.type_ids for item in encoded], dtype=np.int64))
        output = self.model.run(None, {item.name: values[item.name] for item in self.model.get_inputs()})[0]
        vectors = output[:, 0] if output.ndim == 3 else output
        return (vectors / np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12)).tolist()

    def execute(self, request):
        """提供限定索引操作；所有召回结果仍须客户端按 JSON 校验。"""
        op = request['op']
        if op == 'inventory':
            result = self.collection.get(include=['metadatas'])
            return dict(zip(result['ids'], [item['revision'] for item in result['metadatas']]))
        if op == 'upsert':
            facts = request['facts']
            if facts:
                self.collection.upsert(ids=[fact['id'] for fact in facts],
                    embeddings=self.embed([fact['attribute'] + '：' + fact['evidence'] for fact in facts]),
                    metadatas=[{'revision': fact['revision']} for fact in facts])
            return True
        if op == 'delete':
            if request['ids']:
                self.collection.delete(ids=request['ids'])
            return True
        if op == 'query':
            count = self.collection.count()
            vector = self.embed([request['query']], query=True)
            hits = []
            if count:
                result = self.collection.query(query_embeddings=vector,
                    n_results=min(8, count), include=['distances', 'metadatas'])
                hits = [dict(id=key, revision=meta['revision'], distance=distance)
                        for key, meta, distance in zip(result['ids'][0], result['metadatas'][0], result['distances'][0])]
            pending = request.get('pending', [])
            if pending:
                import numpy as np
                encoded = self.embed([fact['attribute'] + '：' + fact['evidence'] for fact in pending])
                distances = 1 - np.asarray(encoded) @ np.asarray(vector[0])
                hits.extend(dict(id=fact['id'], revision=fact['revision'], distance=float(distance))
                            for fact, distance in zip(pending, distances))
            return hits
        raise ValueError('不支持的向量请求')


def main():
    """逐行处理受控 IPC；异常不携带用户正文或系统环境。"""
    engine = VectorEngine(sys.argv[1], sys.argv[2])
    print(json.dumps({'ready': True}), flush=True)
    for line in sys.stdin:
        if len(line) > 65536:
            raise ValueError('向量请求超限')
        try:
            result = {'result': engine.execute(json.loads(line))}
        except Exception as error:
            result = {'error': type(error).__name__}
        print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
