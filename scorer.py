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
def _require(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        raise RuntimeError(f"{name} が空です（GitHub Secretの名前を確認）")
    return v


def _check(r: requests.Response, name: str) -> None:
    """エラー時にAPIの返した理由をそのまま出す（401=キー不正, 400 credit=残高不足 など）。"""
    if r.status_code >= 300:
        raise RuntimeError(f"{name} API error {r.status_code}: {r.text[:500]}")



def obj_schema(props: dict) -> dict:
    """Structured Outputs（strict）用のオブジェクト型。全項目必須・余計な項目なし。"""
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def arr(items: dict) -> dict:
    return {"type": "array", "items": items}


STR, INT, BOOL = {"type": "string"}, {"type": "integer"}, {"type": "boolean"}


def unwrap(obj, key: str):
    """{"key": [...]} の形でも [...] の形でも中身の配列を返す（Anthropic は形式を保証しないため両方受ける）。"""
    return obj.get(key, []) if isinstance(obj, dict) else obj


def call_llm(prompt: str, cfg: dict, max_tokens: int = 4000, schema: dict | None = None,
             schema_name: str = "result") -> str:
    """schema を渡すと、OpenAI では Structured Outputs（strict）で形式どおりのJSONを保証させる。"""
    provider = cfg.get("provider", "anthropic")
    if provider == "anthropic":
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": _require("ANTHROPIC_API_KEY"),
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
        key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not key:
            raise RuntimeError("OPENAI_API_KEY が空です（GitHub Secretの名前を確認）")
        body = {
            "model": cfg["openai_model"],
            # gpt-5系は推論トークンもこの上限に含まれるため多めに取る
            "max_completion_tokens": max(max_tokens, 16000),
            "messages": [{"role": "user", "content": prompt}],
        }
        if cfg["openai_model"].startswith(("gpt-5", "o")):
            body["reasoning_effort"] = cfg.get("openai_reasoning_effort", "low")
        if schema:
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": schema_name, "strict": True, "schema": schema}}
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json=body,
            timeout=300,
        )
        _check(r, "OpenAI")
        choice = r.json()["choices"][0]
        msg = choice["message"]
        if msg.get("refusal"):
            raise RuntimeError(f"OpenAI が応答を拒否しました: {msg['refusal'][:200]}")
        if choice.get("finish_reason") == "length":
            raise RuntimeError("OpenAI の出力が上限で途中で切れました（max_completion_tokens を増やす）")
        return msg.get("content") or ""
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
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        if closer == "}":
            raise
        # 配列の一部が崩れていても、読める要素だけ使う（120件中1件の崩れで全体を落とさない）
        items = []
        for m in re.finditer(r"\{[^{}]*\}", text[start:]):
            try:
                items.append(json.loads(m.group(0)))
            except json.JSONDecodeError:
                continue
        if not items:
            raise
        print(f"[llm] JSONの一部が崩れていたため、読める {len(items)} 件だけ使う")
        return items


# ---------- 1段目: 一括採点 ----------
SCORE_PROMPT = """あなたはブログ編集長です。以下のジャンルのブログで記事にすべき話題を選んでいます。

# ジャンル
{genre}

# 採点基準（各0〜10の整数）
- relevance: ジャンルとの関連度。無関係なら0〜2。
- usecase: プログラミングをしない人が、仕事（動画・資料・SNS・事務など）でどう使えるかを具体的に想像しやすいか。
  特定のツールや機能の話でない（業界ニュース・論評・まとめ）なら低く。
- novelty: 日本語の情報がまだ少ないか。海外で出たばかりのツールや、日本ではまだ紹介されていない新機能なら高く。
  日本のメディアですでに大きく報じられている話題や、日本語の記事が元の候補なら中くらい（5前後）。
- monetizable: 記事内で紹介できる有料ツール・サービス・商品（アフィリエイト案件になりそうなもの）があるか。
  無料OSSや、紹介報酬制度がなさそうな製品だけなら低く。
- topic: 何の話題か（製品名・機能名レベルの短いラベル。例「Claude Code Mods」）。同じ話題の候補には同じラベルを付ける。

# 点の付け方
甘く付けないこと。候補全体を見比べて、本当に記事にしたいものだけに8以上を付ける（8以上は全体の1割程度まで）。

# 候補
{items}

# 出力
全候補について、次の形式のJSONだけを出力してください。説明文は不要です。
{{"scores": [{{"id": 0, "relevance": 0, "usecase": 0, "novelty": 0, "monetizable": 0, "topic": ""}}, ...]}}
"""

SCORE_SCHEMA = obj_schema({"scores": arr(obj_schema(
    {"id": INT, "relevance": INT, "usecase": INT, "novelty": INT, "monetizable": INT, "topic": STR}))})

CRITERIA = ("relevance", "usecase", "novelty", "monetizable")  # AIが付ける点（buzz はコード側で計算）


def _score(v) -> int:
    """0〜10の整数にする（"8" や 8.0 や "8点" も受け付け、読めなければ0）。"""
    m = re.search(r"\d+(?:\.\d+)?", str(v if v is not None else ""))
    return max(0, min(10, round(float(m.group(0))))) if m else 0


def score_all(items: list[Item], genre: str, weights: dict, llm_cfg: dict) -> list[dict]:
    lines = []
    for i, it in enumerate(items):
        s = f"[{i}] ({it.source}) {it.title}"
        if it.summary:
            s += f" — {it.summary[:120]}"
        lines.append(s)
    prompt = SCORE_PROMPT.format(genre=genre.strip(), items="\n".join(lines))
    scores: dict[int, dict] = {}
    last_error = None
    for attempt in range(2):  # 半分以上読めなかったら1回だけやり直す
        try:
            raw = call_llm(prompt, llm_cfg, 4000, SCORE_SCHEMA, "scores")
            got = {int(d["id"]): d for d in unwrap(extract_json(raw), "scores")
                   if isinstance(d, dict) and str(d.get("id", "")).isdigit()}
        except (ValueError, RuntimeError) as e:  # JSONの崩れ・出力の途中切れ
            print(f"[score] 採点結果を読めなかった（{attempt + 1}回目）: {e}")
            last_error = e
            got = {}
        if len(got) > len(scores):
            scores = got
        if len(scores) >= len(items) / 2:
            break
    if not scores:
        raise RuntimeError(f"AIの採点結果を2回とも読み取れませんでした: {last_error}")

    results = []
    for i, it in enumerate(items):
        s = scores.get(i, {})
        pts = {k: _score(s.get(k)) for k in CRITERIA}
        buzz = float(it.extra.get("buzz", 5))
        total = round((sum(pts[k] * weights.get(k, 0) for k in CRITERIA) + buzz * weights.get("buzz", 0)) * 10)
        results.append({"item": it, **pts, "buzz": buzz, "topic": str(s.get("topic", "")).strip(), "total": total})
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
- usecase: 日本の読者（非エンジニア）なら、仕事でどう使えそうか（具体的な場面を1〜2文）
- angle: 記事タイトル案（読者が得られることが分かるもの、32文字程度。「試してみた」「使ってみた」は使わない）
- products: 記事内で紹介できそうな有料ツール・サービス名（なければ空配列）

# 出力
次の形式のJSONだけを出力してください。
{{"items": [{{"id": 0, "summary": "", "why": "", "usecase": "", "angle": "", "products": []}}, ...]}}
"""

DETAIL_SCHEMA = obj_schema({"items": arr(obj_schema(
    {"id": INT, "summary": STR, "why": STR, "usecase": STR, "angle": STR, "products": arr(STR)}))})


def add_details(top: list[dict], genre: str, llm_cfg: dict) -> list[dict]:
    lines = [f"[{i}] ({r['item'].source}) {r['item'].title}\nURL: {r['item'].url}\n概要: {r['item'].summary}"
             for i, r in enumerate(top)]
    raw = call_llm(DETAIL_PROMPT.format(genre=genre.strip(), items="\n\n".join(lines)), llm_cfg,
                   4000, DETAIL_SCHEMA, "details")
    details = {int(d["id"]): d for d in unwrap(extract_json(raw), "items") if isinstance(d, dict) and "id" in d}
    for i, r in enumerate(top):
        r["detail"] = details.get(i, {})
    return top


def topic_key(topic: str) -> str:
    return re.sub(r"[\s\W_]+", "", (topic or "").lower())


def same_topic(key: str, seen: set[str]) -> bool:
    """同じ話題か。ラベルの表記ゆれ（「Claude Duet」と「Anthropic Claude Duet」など）は、片方がもう片方を含めば同じとみなす。"""
    if not key:
        return False
    return any(key == s or (min(len(key), len(s)) >= 8 and (key in s or s in key)) for s in seen)


def pick_diverse(scored: list[dict], n: int, min_relevance: int, recent_topics: set[str] | None = None) -> list[dict]:
    """同じ話題（topic）は1件だけにして上位n件を選ぶ。recent_topics（最近通知した話題）も除く。"""
    picked, seen = [], set()
    recent = recent_topics or set()
    for r in scored:
        if r["relevance"] < min_relevance:
            continue
        key = topic_key(r.get("topic", ""))
        if same_topic(key, seen) or same_topic(key, recent):
            continue
        picked.append(r)
        if key:
            seen.add(key)
        if len(picked) >= n:
            break
    return picked


# ---------- セール検知 ----------
SALE_PROMPT = """あなたはブログ編集者です。以下の読者向けブログで紹介する価値のある
「セール・キャンペーン情報」だけを選んでください。

# 読者とブログ
{genre}

# 対象にするセール
{focus}

# 候補
{items}

# 出力
対象になるものだけを、価値が高い順に最大{n}件、次の形式のJSONで出力してください。該当なしなら {{"items": []}} 。
- what: 何がどうお得か（例「Adobe CC 年間プラン 最大40%OFF」）。候補の文面にない割引率や価格は書かない。
- period: 期間（文面に書かれている場合のみ。不明なら空文字）
- why: この読者にとっての意味（1文）
{{"items": [{{"id": 0, "what": "", "period": "", "why": ""}}]}}
"""

SALE_SCHEMA = obj_schema({"items": arr(obj_schema({"id": INT, "what": STR, "period": STR, "why": STR}))})


def keyword_hit(it: Item, keywords: list[str]) -> bool:
    text = f"{it.title} {it.summary}".lower()
    return any(k.lower() in text for k in keywords)


def judge_sales(items: list[Item], genre: str, focus: str, n: int, llm_cfg: dict) -> list[dict]:
    if not items:
        return []
    lines = [f"[{i}] ({it.source}) {it.title} — {it.summary[:150]}" for i, it in enumerate(items)]
    raw = call_llm(SALE_PROMPT.format(genre=genre.strip(), focus=focus.strip(),
                                      items="\n".join(lines), n=n), llm_cfg, 4000, SALE_SCHEMA, "sales")
    out = []
    for d in unwrap(extract_json(raw), "items"):
        try:
            i = int(d["id"])
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= i < len(items):
            out.append({"item": items[i], "what": d.get("what", ""),
                        "period": d.get("period", ""), "why": d.get("why", "")})
    return out[:n]
