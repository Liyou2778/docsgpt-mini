"""Tokenizer shared by BM25 and the hashing vectorizer.

Chinese text has no whitespace word boundaries, so we treat every CJK
character bigram as a token (a well-known cheap segmentation substitute),
add whole domain phrases from a small built-in dictionary, and keep
ASCII/latin words as lower-cased tokens.  The same tokenizer is used at
index time and at query time, which keeps BM25 and the vectors consistent.
"""

import re
from collections import Counter

CJK_RUN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
ASCII_WORD_RE = re.compile(r"[a-z0-9_]+")
SENTENCE_SPLIT_RE = re.compile(r"[^。！？!?；;\n]*[。！？!?；;\n]|[^。！？!?；;\n]+$")

# Small domain dictionary.  Multi-character entries become single tokens in
# addition to their bigrams, which sharpens exact-phrase matching.
DOMAIN_TERMS = (
    "向量检索",
    "余弦相似度",
    "倒数排名融合",
    "混合检索",
    "关键词检索",
    "引用溯源",
    "分块",
    "重叠",
    "入库",
    "状态机",
    "文档解析",
    "倒排索引",
    "词频",
    "逆文档频率",
    "相似度",
    "大语言模型",
    "检索增强生成",
    "提示词",
    "向量化",
    "维度",
    "归一化",
    "哈希",
    "确定性",
    "离线",
    "幻觉",
    "基准测试",
    "评估指标",
    "召回率",
    "延迟",
    "数据库",
    "事务",
    "索引",
    "游标",
    "并发",
    "线程",
    "异步",
    "协程",
    "事件循环",
    "缓存",
    "过期时间",
    "容器",
    "镜像",
    "编排",
    "网关",
    "接口",
    "状态码",
    "分页",
    "幂等",
    "重试",
    "限流",
    "熔断",
    "日志",
    "监控",
    "告警",
    "单元测试",
    "集成测试",
    "版本控制",
    "分支",
    "合并冲突",
    "快照",
    "前端",
    "后端",
    "接口文档",
    "数据清洗",
    "分词",
    "停用词",
    "大写",
    "小写",
    "标点",
    "解析器",
    "调度",
    "优先级",
    "队列",
    "消息",
    "延迟队列",
    "持久化",
    "序列化",
    "编码",
    "解码",
    "压缩",
    "分片",
    "副本",
    "一致性",
    "共识",
    "选主",
    "分区",
    "负载均衡",
    "健康检查",
    "滚动更新",
    "回滚",
    "灰度",
    "蓝绿",
)

# Very small stop-word list; only affects sentence picking for the mock LLM.
STOPWORDS = frozenset(
    """
    的 了 和 是 在 有 与 及 或 而 也 就 都 这 那 你 我 他 她 它 们 一个 一种 如何 怎么 什么
    为什么 哪些 请问 请 吗 呢 吧 啊 呀 多少 是否 可以 需要 应该 以及 通过 对于 关于 因为
    所以 如果 然后 其中 这些 那些 这个 那个 一种 一些 一下 能够 进行 我们 他们 你们 它
    """.split()
)


def normalize_text(text):
    """Light normalisation: unify newlines and full-width alphanumerics.

    Only full-width letters and digits are folded to ASCII so that model names
    and version numbers match between query and index.  Full-width Chinese
    punctuation (，。：；) is intentionally left untouched to keep the original
    text readable in the citation panel.
    """
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    out = []
    for ch in text:
        code = ord(ch)
        if 0xFF10 <= code <= 0xFF19 or 0xFF21 <= code <= 0xFF3A or 0xFF41 <= code <= 0xFF5A:
            out.append(chr(code - 0xFEE0))  # full-width alphanumerics -> ASCII
        elif code == 0x3000:  # ideographic space
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out)


def tokenize(text, use_dictionary=True):
    """Return a list of tokens (bigrams, dictionary phrases, latin words)."""
    if not text:
        return []
    lowered = text.lower()
    tokens = []

    for match in ASCII_WORD_RE.finditer(lowered):
        tokens.append(match.group(0))

    for match in CJK_RUN_RE.finditer(text):
        run = match.group(0)
        if len(run) == 1:
            tokens.append(run)
            continue
        for i in range(len(run) - 1):
            tokens.append(run[i : i + 2])

    if use_dictionary:
        for term in DOMAIN_TERMS:
            count = text.count(term)
            if count:
                tokens.extend([term] * min(count, 8))

    return tokens


def term_frequencies(text):
    return Counter(tokenize(text))


def split_sentences(text):
    """Split into sentences, keeping offsets.  Returns [(start, end), ...]."""
    spans = []
    for match in SENTENCE_SPLIT_RE.finditer(text):
        start, end = match.span()
        if end > start:
            spans.append((start, end))
    return spans


def query_terms(question):
    """Content tokens used for sentence relevance in the mock answerer."""
    tokens = []
    for token in tokenize(question, use_dictionary=False):
        if token in STOPWORDS:
            continue
        if token.strip():
            tokens.append(token)
    return tokens
