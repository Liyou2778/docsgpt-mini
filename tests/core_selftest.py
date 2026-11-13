"""Quick self-check for the dependency-free core modules."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.bm25 import BM25Index
from app.chunking import chunk_text
from app.hybrid import fuse, path_ranks
from app.llm import Answerer
from app.tokenize import split_sentences, tokenize
from app.vector import embed_text

text = (
    "向量检索使用余弦相似度衡量查询与文档的相似程度。BM25 擅长精确关键词匹配。"
    "混合检索把两路分数融合起来。"
) * 30
chunks = chunk_text(text, 200, 0.1)
print("chunks:", len(chunks), "first:", chunks[0]["start"], chunks[0]["end"], len(chunks[0]["text"]))
assert chunks and chunks[0]["text"].strip()
assert all(piece["end"] > piece["start"] for piece in chunks)

print("tokenize:", tokenize("向量检索使用余弦相似度 BM25")[:10])
assert "向量" in tokenize("向量检索使用余弦相似度")
assert split_sentences("第一句。第二句！第三句")
print("sentences:", split_sentences("第一句。第二句！第三句"))

vec_a = embed_text("向量检索 余弦相似度")
vec_b = embed_text("向量检索 余弦相似度")
vec_c = embed_text("完全无关的文本 数据库 事务")
print("vector norm:", float((vec_a**2).sum()) ** 0.5)
print("cos same:", float(vec_a @ vec_b), "cos diff:", float(vec_a @ vec_c))
assert abs(float(vec_a @ vec_b) - 1.0) < 1e-5
assert float(vec_a @ vec_c) < 0.9

index = BM25Index()
items = [
    ("c1", tokenize("向量检索 使用 余弦相似度 计算 相似度")),
    ("c2", tokenize("数据库 事务 与 索引 的 原理")),
    ("c3", tokenize("BM25 是 关键词 检索 算法")),
]
index.build(items)
scores = index.score(tokenize("余弦相似度 怎么 计算"))
print("bm25 scores:", scores.tolist())
assert scores[0] == max(scores)

import numpy as np

vector_scores = np.array([0.3, 0.1, 0.2])
fused = fuse(scores, vector_scores, "weighted", 0.5)
print("fused:", fused.tolist(), "ranks:", path_ranks(fused).tolist())

answerer = Answerer("mock")
out = answerer.generate(
    "如何计算余弦相似度",
    [
        {"index": 1, "title": "v", "text": "余弦相似度通过两个向量的点积除以模长乘积得到。它衡量方向上的接近程度。"},
        {"index": 2, "title": "b", "text": "BM25 基于词频和逆文档频率打分。"},
    ],
)
print("mock answer:", out["text"])
assert "[1]" in out["text"]
print("OK")
