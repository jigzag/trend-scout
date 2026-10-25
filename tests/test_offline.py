"""ネットに出ずに全工程を通すテスト（各ソースの実フォーマットを模したサンプル + LLM/LINEモック）。
実行: python -m pytest tests -q   または  python tests/test_offline.py
"""
import json, sys, os, tempfile
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sources, scorer, notifier, main, history  # noqa: E402

HATENA = """<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns="http://purl.org/rss/1.0/" xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
 xmlns:hatena="http://www.hatena.ne.jp/info/xmlns#">
<channel rdf:about="https://b.hatena.ne.jp/hotentry/it"><title>はてブ</title></channel>
<item rdf:about="https://example.com/minimax-video?utm_source=hatena">
 <title>MiniMaxの新しい動画モデルで研修動画を作ってみた</title>
 <link>https://example.com/minimax-video?utm_source=hatena</link>
 <description>&lt;p&gt;Hailuo の新モデルを試した&lt;/p&gt;</description>
 <hatena:bookmarkcount>412</hatena:bookmarkcount>
</item>
<item rdf:about="https://example.com/k8s">
 <title>Kubernetes 1.40 リリースノート</title><link>https://example.com/k8s</link>
 <hatena:bookmarkcount>120</hatena:bookmarkcount>
</item>
</rdf:RDF>"""
QIITA = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Qiita</title>
<entry><title>n8nとClaudeで日報を自動化</title>
<link rel="alternate" type="text/html" href="https://qiita.com/u/items/abc"/>
<content type="html">&lt;p&gt;n8n で Claude API を叩く&lt;/p&gt;</content></entry>
<entry><title>MiniMaxの新しい動画モデルで研修動画を作ってみた</title>
<link rel="alternate" href="https://qiita.com/u/items/dup"/></entry>
</feed>"""
PH = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>VoiceForge - AI voice cloning API</title>
<link rel="alternate" type="text/html" href="https://www.producthunt.com/products/voiceforge"/>
<content type="html">Clone any voice in 10 seconds</content></entry></feed>"""
ZENN = {"articles": [{"title": "Remotionで字幕付き動画を量産する", "path": "/u/articles/remo", "liked_count": 88}]}
HN = {"hits": [{"title": "Show HN: Open-source TTS beats ElevenLabs", "url": "https://github.com/x/tts", "points": 512, "objectID": "1"},
               {"title": "Ask HN: Who is hiring?", "url": None, "points": 300, "objectID": "2"}]}

GORI = """<?xml version="1.0"?><rss version="2.0"><channel><title>gori.me</title>
<item><title>Amazonプライム感謝祭、10月18日から開催決定</title><link>https://gori.me/amazon/1</link>
<description>先行セールは16日から</description></item>
<item><title>Amazonで食品が半額セール</title><link>https://gori.me/amazon/2</link><description>お米が特価</description></item>
<item><title>新型iPhoneレビュー</title><link>https://gori.me/iphone/3</link><description>カメラが良い</description></item>
</channel></rss>"""
PRT = """<?xml version="1.0"?><rdf:RDF xmlns="http://purl.org/rss/1.0/" xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
<item rdf:about="https://prtimes.jp/a"><title>動画生成AI「Foo」年額プラン30%OFFキャンペーン開始</title><link>https://prtimes.jp/a</link>
<description>10月末まで</description></item></rdf:RDF>"""

class Resp:
    def __init__(self, text=None, js=None, status=200):
        self.text = text if text is not None else json.dumps(js)
        self._js = js; self.status_code = status
    def json(self): return self._js
    def raise_for_status(self):
        if self.status_code >= 400: raise RuntimeError(self.status_code)

def fake_get(url, **kw):
    if "hatena" in url: return Resp(HATENA)
    if "qiita" in url: return Resp(QIITA)
    if "producthunt" in url: return Resp(PH)
    if "zenn" in url: return Resp(js=ZENN)
    if "algolia" in url: return Resp(js=HN)
    if "gori.me" in url: return Resp(GORI)
    if "prtimes" in url: return Resp(PRT)
    if "itmedia" in url: return Resp(PH.replace("VoiceForge - AI voice cloning API", "ChatGPTに新機能").replace("producthunt.com/products/voiceforge", "itmedia.co.jp/aiplus/x.html"))
    raise AssertionError(url)

def fake_llm(prompt, cfg, max_tokens=4000, *a, **kw):
    if "セール・キャンペーン情報" in prompt:
        cands = prompt.split("# 候補")[1].split("# 出力")[0].strip().splitlines()
        assert not any("iPhoneレビュー" in c for c in cands)  # キーワードで一次ふるい済み
        out = [{"id": int(c[1:c.index("]")]), "what": c.split(") ", 1)[1][:30], "period": "", "why": "制作に関係"}
               for c in cands if "食品" not in c]
        return json.dumps(out, ensure_ascii=False)
    if "relevance" in prompt and "[0]" in prompt and "summary" not in prompt.split("# 出力")[1]:
        # 1段目: タイトルにキーワードがあれば高得点
        out = []
        for line in prompt.split("# 候補")[1].split("# 出力")[0].strip().splitlines():
            i = int(line[1:line.index("]")])
            hit = any(k in line for k in ("MiniMax", "TTS", "voice", "n8n", "Remotion"))
            topic = "音声合成" if ("TTS" in line or "voice" in line) else line[:20]
            out.append({"id": i, "relevance": 9 if hit else 1, "testable": 8 if hit else 2,
                        "monetizable": 6 if hit else 0, "topic": topic})
        return "```json\n" + json.dumps(out) + "\n```"
    n = prompt.count("\nURL: ")
    return json.dumps([{"id": i, "summary": f"要約{i}", "why": f"理由{i}", "try": f"手順{i}",
                        "angle": f"タイトル案{i}", "products": ["ツールA"]} for i in range(n)], ensure_ascii=False)

def test_parsers():
    with mock.patch.object(sources.requests, "get", fake_get):
        h = sources.fetch_hatena(10)
        assert h[0].signal == 412 and h[0].signal_label == "412users"
        assert h[0].summary == "Hailuo の新モデルを試した"
        q = sources.fetch_qiita(10); assert q[0].url == "https://qiita.com/u/items/abc"
        assert sources.fetch_zenn(10)[0].url == "https://zenn.dev/u/articles/remo"
        hn = sources.fetch_hackernews(10); assert hn[1].url.endswith("id=2")
        assert sources.fetch_producthunt(10)[0].title.startswith("VoiceForge")

def test_dedupe_and_history(tmp_path=Path(tempfile.gettempdir()) / "ts_test"):
    tmp_path.mkdir(exist_ok=True)
    a = sources.Item("A", "Same Title!", "https://www.x.com/p/?utm_source=a")
    b = sources.Item("B", "same title", "https://other.com/q")
    c = sources.Item("C", "Other", "https://x.com/p")
    d = history.dedupe([a, b, c])
    assert len(d) == 1 and set(d[0].extra["also_on"]) == {"B", "C"}
    hpath = tmp_path / "seen.json"; hpath.unlink(missing_ok=True)
    hs = history.History(hpath, 14)
    hs.mark([a], date(2026, 10, 1)); hs.save()
    hs2 = history.History(hpath, 14)
    assert hs2.filter_new([c], date(2026, 10, 10)) == []
    assert len(hs2.filter_new([c], date(2026, 10, 20))) == 1

def test_full_run(tmp_path=Path(tempfile.gettempdir()) / "ts_run"):
    import shutil
    shutil.rmtree(tmp_path, ignore_errors=True); tmp_path.mkdir()
    sent = {}
    def fake_send(text): sent["text"] = text
    with mock.patch.object(sources.requests, "get", fake_get), \
         mock.patch.object(scorer, "call_llm", fake_llm), \
         mock.patch.object(main, "send_line", fake_send), \
         mock.patch.object(main, "BASE", tmp_path):
        text = main.run(config_path=Path(main.__file__).parent / "config.yaml")
    assert sent["text"] == text
    assert "Kubernetes" not in text and "hiring" not in text
    assert text.count("■") == 3
    assert "Qiita" in text  # 重複したMiniMax記事に also_on が付く
    assert not ("TTS beats" in text and "VoiceForge" in text)  # 同じ話題は1件だけ
    assert "utm_source" not in text
    assert "【セール・キャンペーン】" in text and "プライム感謝祭" in text and "30%OFF" in text
    assert "食品" not in text
    assert len(text) < 5000
    files = list((tmp_path / "data" / "candidates").glob("*.json"))
    assert len(files) == 1 and len(json.loads(files[0].read_text(encoding="utf-8"))) == 3
    seen = json.loads((tmp_path / "data" / "seen.json").read_text(encoding="utf-8"))
    assert len(seen) == 5  # 候補3 + セール2
    print(text)

def test_source_failure():
    def bad_get(url, **kw):
        if "qiita" in url: raise ConnectionError("down")
        return fake_get(url, **kw)
    with mock.patch.object(sources.requests, "get", bad_get), \
         mock.patch.object(scorer, "call_llm", fake_llm):
        text = main.run(dry_run=True)
    assert "取得失敗: qiita" in text

def test_broken_json():
    """10/25 の本番エラー: 120件の配列の途中で ':' が抜けていた → 読める要素だけ使う。"""
    raw = '```json\n[{"id": 0, "relevance": 8, "testable": 7, "monetizable": "5点", "topic": "A"},\n' \
          ' {"id": 1, "relevance" 9, "testable": 1},\n {"id": 2, "relevance": 3, "testable": 2, "monetizable": 1, "topic": "B"}]\n```'
    got = scorer.extract_json(raw)
    assert [d["id"] for d in got] == [0, 2]
    items = [sources.Item("X", f"t{i}", f"https://e.com/{i}") for i in range(3)]
    calls = []
    def llm(prompt, cfg, max_tokens=4000, *a, **kw):
        calls.append(1); return raw
    with mock.patch.object(scorer, "call_llm", llm):
        res = scorer.score_all(items, "g", {"relevance": .4, "testable": .3, "monetizable": .15, "buzz": .15}, {})
    assert len(calls) == 1                       # 3件中2件読めたので、やり直さない
    top = res[0]
    assert top["item"].title == "t0" and top["monetizable"] == 5
    def bad(prompt, cfg, max_tokens=4000, *a, **kw):
        calls.append(1); return "すみません、出力できません"
    calls.clear()
    with mock.patch.object(scorer, "call_llm", bad):
        try:
            scorer.score_all(items, "g", {"relevance": .4, "testable": .3, "monetizable": .15}, {})
            assert False
        except RuntimeError as e:
            assert "2回とも" in str(e) and len(calls) == 2

def _check_strict(schema, path="root"):
    """OpenAI Structured Outputs（strict）の条件: object は全項目 required・additionalProperties false。"""
    if schema.get("type") == "object":
        assert schema.get("additionalProperties") is False, path
        assert sorted(schema["required"]) == sorted(schema["properties"]), path
        for k, v in schema["properties"].items():
            _check_strict(v, f"{path}.{k}")
    elif schema.get("type") == "array":
        _check_strict(schema["items"], path + "[]")

def test_structured_outputs():
    import writer
    for s in (scorer.SCORE_SCHEMA, scorer.DETAIL_SCHEMA, scorer.SALE_SCHEMA,
              writer.FACTS_SCHEMA, writer.OUTLINE_SCHEMA, writer.REWRITE_SCHEMA):
        assert s["type"] == "object"  # strict はルートがオブジェクト
        _check_strict(s)
    os.environ["OPENAI_API_KEY"] = "sk-test"
    sent = {}
    def post(url, **kw):
        sent.update(kw["json"])
        return Resp(js={"choices": [{"finish_reason": "stop", "message": {"content":
                    '{"scores": [{"id": 0, "relevance": 9, "testable": 8, "monetizable": 7, "topic": "A"}]}'}}]})
    items = [sources.Item("X", "t0", "https://e.com/0")]
    with mock.patch.object(scorer.requests, "post", post):
        res = scorer.score_all(items, "g", {"relevance": .4, "testable": .3, "monetizable": .15},
                               {"provider": "openai", "openai_model": "gpt-5-mini"})
    rf = sent["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["schema"] == scorer.SCORE_SCHEMA
    assert res[0]["relevance"] == 9 and res[0]["topic"] == "A"
    def cut(url, **kw):
        return Resp(js={"choices": [{"finish_reason": "length", "message": {"content": '{"scores": [{"id"'}}]})
    with mock.patch.object(scorer.requests, "post", cut):
        try:
            scorer.call_llm("x", {"provider": "openai", "openai_model": "gpt-5-mini"}, schema=scorer.SCORE_SCHEMA)
            assert False
        except RuntimeError as e:
            assert "途中で切れ" in str(e)

def test_topic_dedupe(tmp_path=Path(tempfile.gettempdir()) / "ts_topics"):
    """10/22〜23 に Claude Duet が3回出た件: 表記ゆれは同じ話題、過去14日に出した話題は除く。"""
    def r(topic, total, rel=9):
        return {"item": sources.Item("X", topic, f"https://e.com/{topic}"), "topic": topic, "relevance": rel, "total": total}
    scored = [r("Claude Duet", 90), r("Anthropic Claude Duet", 85), r("ChatGPT", 84), r("ChatGPT音声文字起こし", 83),
              r("Kling 4.0", 80), r("Gemini 4", 79)]
    got = [x["topic"] for x in scorer.pick_diverse(scored, 3, 5)]
    assert got == ["Claude Duet", "ChatGPT", "ChatGPT音声文字起こし"]  # 短い「chatgpt」(7字)は含んでいても別扱い
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "seen_topics.json").unlink(missing_ok=True)
    hs = history.History(tmp_path / "seen.json", 14)
    hs.mark_topics([scorer.topic_key("Claude Duet"), scorer.topic_key("Kling 4.0")], date(2026, 10, 22)); hs.save()
    hs2 = history.History(tmp_path / "seen.json", 14)
    recent = hs2.recent_topics(date(2026, 10, 23))
    got = [x["topic"] for x in scorer.pick_diverse(scored, 3, 5, recent)]
    assert "Claude Duet" not in got and "Anthropic Claude Duet" not in got and "Kling 4.0" not in got
    assert hs2.recent_topics(date(2026, 11, 10)) == set()  # 14日を過ぎたら再び候補にできる

def test_line_payload():
    os.environ["LINE_CHANNEL_ACCESS_TOKEN"] = "tok"; os.environ["LINE_USER_ID"] = "U123"
    with mock.patch.object(notifier.requests, "post", return_value=Resp("{}")) as p:
        notifier.send_line("hello")
    _, kw = p.call_args
    assert p.call_args[0][0] == "https://api.line.me/v2/bot/message/push"
    assert kw["headers"]["Authorization"] == "Bearer tok"
    assert kw["json"] == {"to": "U123", "messages": [{"type": "text", "text": "hello"}]}

if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"): fn(); print("OK", name)
