"""AIによる採点（2段階）。
  1段目: 全件を数値だけで一括採点（安く速く）
  2段目: 上位N件だけ、要約・選定理由・試し方を生成
"""
from __future__ import annotations

import json
import os
import re

import requests

from sources import Item


# ---------- LLM 呼び出し ----------
def _check(r: requests.Response, name: str) -> None:
    """エラー時にAPIの返した理由をそのまま出す（401=キー不正, 400 credit=残高不足 など）。"""
    if r.status_code >= 300:
        raise RuntimeError(f"{name} API error {r.status_code}: {r.text[:500]}")



def call_llm(prompt: str, cfg: dict, max_tokens: int = 4000) -> str:
    provider = cfg.get("provider", "anthropic")
    if provider == "anthropic":
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": os.environ.get("ANTHROPIC_API_KEY", "").strip(),
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": cfg["anthropic_model"],
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=120,
        )
        _check(r, "Anthropic")
        return "".join(b.get("text", "") for b in r.json()["content"])
    if provider == "openai":
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {os.environ.get('OPENAI_API_KEY', '').strip()}"},
            json={
                "model": cfg["openai_model"],
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=120,
        )
        _check(r, "OpenAI")
        return r.json()["choices"][0]["message"]["content"]
    raise ValueError(f"unknown provider: {provider}")


def extract_json(text: str):
    """LLMの返答から最初のJSON配列/オブジェクトを取り出す。"""
    text = re.sub(r"```(?:json)?", "", text)
    starts = [i for i in (text.find("["), text.find("{")) if i != -1]
    if not starts:
        raise ValueError("JSON not found in LLM output")
    start = min(starts)
    closer = "]" if text[start] == "[" else "}"
    end = text.rfind(closer)
    return json.loads(text[start:end + 1])


# ---------- 1段目: 一括採点 ----------
SCORE_PROMPT = """あなたはブログ編集長です。以下のジャンルのブログで記事にすべき話題を選んでいます。

# ジャンル
{genre}

# 採点基準（各0〜10の整数）
- relevance: ジャンルとの関連度。無関係なら0〜2。
- testable: 運営者が実際に触って検証・体験談を書けるか。ニュースや論評だけで手を動かせないなら低く。
- monetizable: 記事内で紹介できる有料ツール・サービス・商品（アフィリエイト案件になりそうなもの）があるか。

# 候補
{items}

# 出力
全候補について、次の形式のJSON配列だけを出力してください。説明文は不要です。
[{{"id": 0, "relevance": 0, "testable": 0, "monetizable": 0}}, ...]
"""


def score_all(items: list[Item], genre: str, weights: dict, llm_cfg: dict) -> list[dict]:
    lines = []
    for i, it in enumerate(items):
        s = f"[{i}] ({it.source}) {it.title}"
        if it.summary:
            s += f" — {it.summary[:120]}"
        lines.append(s)
    raw = call_llm(SCORE_PROMPT.format(genre=genre.strip(), items="\n".join(lines)), llm_cfg)
    scores = {int(d["id"]): d for d in extract_json(raw) if "id" in d}

    results = []
    for i, it in enumerate(items):
        s = scores.get(i, {})
        rel, tst, mon = (int(s.get(k, 0)) for k in ("relevance", "testable", "monetizable"))
        total = round((rel * weights["relevance"] + tst * weights["testable"]
                       + mon * weights["monetizable"]) * 10)
        results.append({"item": it, "relevance": rel, "testable": tst,
                        "monetizable": mon, "total": total})
    # 同点なら、はてブ数などの反応が大きい方を優先
    results.sort(key=lambda r: (r["total"], r["item"].signal or 0), reverse=True)
    return results


# ---------- 2段目: 上位の詳細 ----------
DETAIL_PROMPT = """あなたはブログ編集長です。以下のジャンルのブログ運営者に、今日の記事候補を提案します。

# ジャンル
{genre}

# 候補
{items}

# 各候補について作るもの
- summary: 何の話題か（日本語1〜2文。英語の話題も日本語で）
- why: このブログで記事にする価値（1文）
- try: 運営者が今日〜明日で実際に試すなら何をすればいいか（具体的な手順1〜2文）
- angle: 記事タイトル案（「試してみた」系、32文字程度）
- products: 記事内で紹介できそうな有料ツール・サービス名（なければ空配列）

# 出力
JSON配列だけを出力してください。
[{{"id": 0, "summary": "", "why": "", "try": "", "angle": "", "products": []}}, ...]
"""


def add_details(top: list[dict], genre: str, llm_cfg: dict) -> list[dict]:
    lines = [f"[{i}] ({r['item'].source}) {r['item'].title}\nURL: {r['item'].url}\n概要: {r['item'].summary}"
             for i, r in enumerate(top)]
    raw = call_llm(DETAIL_PROMPT.format(genre=genre.strip(), items="\n\n".join(lines)), llm_cfg)
    details = {int(d["id"]): d for d in extract_json(raw) if "id" in d}
    for i, r in enumerate(top):
        r["detail"] = details.get(i, {})
    return top
