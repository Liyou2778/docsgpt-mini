"""LLM abstraction layer.

Default provider is ``mock``: a deterministic, offline answerer that selects
the most query-relevant sentence from each of the top passages and appends the
citation marker ``[n]`` right after it.  It never invents a citation number, so
every marker is traceable by construction.

An OpenAI-compatible endpoint can be enabled with LLM_PROVIDER=openai plus
LLM_BASE_URL / LLM_API_KEY / LLM_MODEL.  If the remote call fails the answerer
falls back to the deterministic mock so the app stays usable.
"""

import re

import httpx

from . import config
from .tokenize import query_terms, split_sentences, tokenize

CITATION_RE = re.compile(r"\[(\d{1,3})\]")
SENTENCE_LIMIT = 180


def _clean_sentence(sentence, limit=SENTENCE_LIMIT):
    sentence = " ".join(sentence.split())
    sentence = sentence.strip(" \t\n，,、；;：:")
    if len(sentence) > limit:
        sentence = sentence[:limit].rstrip("，,、；;：: ") + "…"
    return sentence


def _is_heading(sentence):
    """Markdown headings and very short fragments make poor answer sentences."""
    stripped = " ".join(sentence.split())
    if not stripped:
        return True
    if stripped.startswith("#"):
        return True
    if len(stripped) < 8 and not re.search(r"[。！？!?；;]$", stripped):
        return True
    return False


def _sentence_score(sentence, terms):
    tokens = set(tokenize(sentence, use_dictionary=False))
    if not tokens or not terms:
        return 0.0
    overlap = len(terms & tokens)
    # Length normalisation keeps long chunks from dominating.
    return overlap / (len(tokens) ** 0.5)


def pick_sentence(text, terms):
    """Return (sentence, score) for the best matching sentence in ``text``."""
    spans = split_sentences(text) or [(0, len(text))]
    best = None
    best_score = -1.0
    for start, end in spans:
        cleaned = _clean_sentence(text[start:end])
        if not cleaned or _is_heading(cleaned):
            continue
        score = _sentence_score(cleaned, terms)
        if score > best_score:
            best_score = score
            best = cleaned
    if best is None:
        return _clean_sentence(text), 0.0
    return best, max(best_score, 0.0)


class Answerer:
    def __init__(self, provider=None):
        self.provider = (provider or config.LLM_PROVIDER or "mock").lower()

    # ------------------------------------------------------------------ public
    def generate(self, question, contexts):
        """``contexts`` is a list of dicts with 1-based ``index`` and ``text``."""
        if self.provider == "openai":
            try:
                text = self._openai(question, contexts)
                return {
                    "text": text,
                    "provider": "openai",
                    "model": config.LLM_MODEL,
                    "fallback": False,
                    "error": None,
                }
            except Exception as exc:  # pragma: no cover - network dependent
                text = self._mock(question, contexts)
                return {
                    "text": text,
                    "provider": "mock",
                    "model": "mock-deterministic",
                    "fallback": True,
                    "error": "%s: %s" % (type(exc).__name__, exc),
                }
        return {
            "text": self._mock(question, contexts),
            "provider": "mock",
            "model": "mock-deterministic",
            "fallback": False,
            "error": None,
        }

    # -------------------------------------------------------------------- mock
    def _mock(self, question, contexts):
        if not contexts:
            return "知识库中还没有可用的内容，请先上传文档后再提问。"
        terms = set(query_terms(question))
        picked = []
        for context in contexts[:3]:
            sentence, score = pick_sentence(context["text"], terms)
            if sentence:
                picked.append((context["index"], sentence, score))
        if not picked:
            return "抱歉，在已入库的文档中没有找到与问题相关的段落。"
        # Prefer passages that actually share query terms; if none do, still
        # answer from the top-ranked passage.
        relevant = [item for item in picked if item[2] > 0]
        chosen = relevant or picked[:1]
        body = " ".join("%s[%d]" % (sentence, index) for index, sentence, _ in chosen)
        return "根据知识库中检索到的内容：%s" % body

    # ------------------------------------------------------------------ openai
    def _openai(self, question, contexts):
        if not config.LLM_BASE_URL or not config.LLM_MODEL:
            raise RuntimeError("LLM_BASE_URL and LLM_MODEL are required for openai provider")
        blocks = []
        for context in contexts:
            blocks.append("[%d]《%s》\n%s" % (context["index"], context["title"], context["text"]))
        system = (
            "你是一个基于知识库的问答助手。只能使用提供的编号资料回答，"
            "每个结论后必须紧跟引用标记，例如 [1]。不要编造资料中没有的内容。"
            "如果资料不足，请明确说明。请用中文回答。"
        )
        user = "问题：%s\n\n资料：\n%s" % (question, "\n\n".join(blocks))
        headers = {"Content-Type": "application/json"}
        if config.LLM_API_KEY:
            headers["Authorization"] = "Bearer " + config.LLM_API_KEY
        payload = {
            "model": config.LLM_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
        }
        url = config.LLM_BASE_URL.rstrip("/") + "/chat/completions"
        response = httpx.post(url, json=payload, headers=headers, timeout=config.LLM_TIMEOUT_SECONDS)
        response.raise_for_status()
        data = response.json()
        text = data["choices"][0]["message"]["content"].strip()
        if not text:
            raise RuntimeError("empty completion")
        return text


ANSWERER = Answerer()
