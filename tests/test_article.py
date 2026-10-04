"""記事生成（Ver.1/2）のオフラインテスト。Tavily・LLM・WordPress・LINE はモック。
実行: python tests/test_article.py
"""
import base64, json, os, shutil, sys, tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import article, research, writer, wordpress  # noqa: E402

SRC_MAIN = ("OpenAIは10月2日、ChatGPTにバーチャル試着機能を追加したと発表した。利用者が自分の写真をアップロードすると、"
            "商品の服やアクセサリーを合成した画像を表示する。まず米国のPlusユーザーから提供を始める。" * 8)
SRC_OTHER = ("ChatGPTの新しい試着機能は、ショッピング検索の結果に表示される商品カードから使える。"
             "日本での提供時期は明らかにされていない。iOSアプリでは書類のスキャン機能も追加された。" * 8)
COPIED = "利用者が自分の写真をアップロードすると、商品の服やアクセサリーを合成した画像を表示する。"

CAND = [{"topic": "ChatGPT-virtual-tryon", "total": 82, "detail": {"summary": "試着機能の追加"},
         "item": {"source": "ITmedia AI+", "title": "ChatGPTにバーチャル試着機能", "url": "https://www.itmedia.co.jp/a/1",
                  "summary": "", "signal": None, "signal_label": "", "extra": {}}}]


class Resp:
    def __init__(self, js=None, text="", status=200):
        self._js, self.text, self.status_code = js, text or json.dumps(js), status
        self.apparent_encoding, self.encoding = "utf-8", "utf-8"
    def json(self): return self._js
    def raise_for_status(self):
        if self.status_code >= 400: raise RuntimeError(self.status_code)


def fake_post(url, **kw):
    if url == research.TAVILY_EXTRACT:
        return Resp({"results": [{"url": kw["json"]["urls"][0], "raw_content": SRC_MAIN}], "failed_results": []})
    if url == research.TAVILY_SEARCH:
        assert kw["json"]["include_raw_content"] == "text"
        return Resp({"results": [
            {"title": "同じサイトの別記事", "url": "https://itmedia.co.jp/b/2", "raw_content": SRC_OTHER},
            {"title": "短すぎる記事", "url": "https://short.example/x", "raw_content": "短い"},
            {"title": "はてなブックマーク - 新着エントリー", "url": "https://b.hatena.ne.jp/entrylist/it", "raw_content": SRC_OTHER},
            {"title": "AIニュースまとめ（51記事）", "url": "https://note.com/x/n/1", "raw_content": SRC_OTHER},
            {"title": "試着機能の解説", "url": "https://other.example/c", "raw_content": SRC_OTHER}]})
    if url == "https://api.openai.com/v1/images/generations":
        assert "no text" in kw["json"]["prompt"].lower() and kw["json"]["output_format"] == "jpeg"
        fake_post.images += 1
        return Resp({"data": [{"b64_json": base64.b64encode(b"JPEGDATA").decode()}]})
    if url.endswith("/wp-json/wp/v2/media"):
        assert kw["data"] == b"JPEGDATA" and kw["headers"]["Content-Type"] == "image/jpeg"
        fake_post.media += 1
        mid = 100 + fake_post.media
        return Resp({"id": mid, "source_url": f"https://nocode-ai.net/wp-content/uploads/{mid}.jpg"})
    if "/wp-json/wp/v2/media/" in url:
        return Resp({"id": 1})
    if url.endswith("/wp-json/wp/v2/posts"):
        assert kw["json"]["status"] == "draft" and kw["auth"] == ("nonpro", "xxxx xxxx")
        assert kw["json"]["featured_media"] == 101
        return Resp({"id": 42, "link": "https://nocode-ai.net/?p=42"})
    raise AssertionError(url)
fake_post.images = fake_post.media = 0


BODY = ("<p>ChatGPTに試着機能が加わりました。この記事では、できることと注意点をまとめます。</p>"
        "<h2>何ができるのか</h2><p>" + COPIED + "</p>"
        + "<h2>使い始め方</h2><p>商品カードから試着ボタンを押して写真を選びます。</p>" * 30
        + "<h2>【想定シナリオ】研修担当のAさんの場合</h2><p>以下は架空の例です。Aさんなら、制服の着用イメージ作りに使えそうです。</p>"
        + "<h2>まとめ</h2><p>日本での提供時期は公式サイトで確認してください。</p>")


def fake_llm(prompt, cfg, max_tokens=4000):
    if "事実」だけを抜き出して" in prompt:
        assert "参考1:" in prompt and "参考2:" in prompt and "参考3:" not in prompt  # 同ドメイン・短文は除外
        return json.dumps({"summary": "試着機能", "facts": [{"fact": "米国Plusから提供", "src": 1}],
                           "steps": [], "cautions": [], "unknowns": ["日本での提供時期"]}, ensure_ascii=False)
    if "記事の構成を作って" in prompt:
        return json.dumps({"title": "ChatGPTの試着機能でできること", "slug": "chatgpt-virtual-try-on",
                           "excerpt": "説明", "persona": "研修担当のAさん", "sections": []}, ensure_ascii=False)
    if "記事本文を書いて" in prompt:
        assert ("無料版は3回まで" in prompt) == fake_llm.with_memo
        return "```html\n" + BODY + "\n```"
    if "校閲者です" in prompt:
        return prompt.split("# 記事\n", 1)[1].split("\n\n# 出力")[0]
    if "似すぎています" in prompt:
        blocks = json.loads(prompt.split("# 段落（JSON配列）\n", 1)[1].split("\n\n# 出力")[0])
        return json.dumps(["<p>自分の写真を使って、服や小物を身に着けたイメージを確認できます。</p>" for _ in blocks],
                          ensure_ascii=False)
    raise AssertionError(prompt[:80])
fake_llm.with_memo = False


def _setup(tmp: Path):
    shutil.rmtree(tmp, ignore_errors=True)
    (tmp / "data" / "candidates").mkdir(parents=True)
    (tmp / "data" / "candidates" / "2026-10-04.json").write_text(json.dumps(CAND, ensure_ascii=False), encoding="utf-8")


def test_similarity_detects_copy():
    srcs = [{"text": SRC_MAIN}]
    sim = writer.similarity("<p>" + COPIED + "</p>", srcs)
    assert sim["longest"] >= 40
    assert writer.flagged_blocks("<p>まったく別の文章です。</p><p>" + COPIED + "</p>", sim, 40) == ["<p>" + COPIED + "</p>"]
    assert writer.similarity("<p>自分で考えた全く新しい説明文がここに入ります。</p>", srcs)["longest"] < 40


def test_check_rules():
    w = writer.check_rules("<p>実際に使ってみると便利でした。</p>", "")
    assert any("体験" in x for x in w) and any("想定シナリオ" in x for x in w)
    ok = writer.check_rules(f'<h2>【想定シナリオ】</h2><div class="{writer.NOTE_CLASS}"><p>使ってみた感想</p></div>' + "<p>あ</p>" * 3000, "メモ")
    assert ok == []
    leak = writer.check_rules("<h2>【想定シナリオ】</h2><p>事実メモによると無料です。</p>", "")
    assert any("指示の言葉" in x for x in leak)


def test_full_article(tmp=Path(tempfile.gettempdir()) / "ts_article"):
    _setup(tmp)
    os.environ.update({"TAVILY_API_KEY": "tvly-x", "WP_URL": "https://nocode-ai.net/", "WP_USER": "nonpro",
                       "WP_APP_PASSWORD": "xxxx xxxx", "LINE_CHANNEL_ACCESS_TOKEN": "t", "LINE_USER_ID": "U1",
                       "OPENAI_API_KEY": "sk-test"})
    sent = {}
    fake_llm.with_memo = True
    def no_net(url, **kw): raise AssertionError(f"ネットに出ようとした: {url}")
    with mock.patch.object(research.requests, "post", fake_post), \
         mock.patch.object(research.requests, "get", no_net), \
         mock.patch.object(writer, "call_llm", fake_llm), \
         mock.patch.object(article, "send_line", lambda t: sent.setdefault("text", t)), \
         mock.patch.object(article, "BASE", tmp):
        art = article.run(1, memo="無料版は3回まで", config_path=Path(article.__file__).parent / "config.yaml")
    assert art["wordpress"]["edit_url"] == "https://nocode-ai.net/wp-admin/post.php?post=42&action=edit"
    assert COPIED not in art["content"]                      # 似すぎた段落は書き直された
    assert art["similarity"]["ok"]
    assert art["content"].startswith(writer.PR_NOTE)
    assert "参考にした情報" in art["content"] and "https://other.example/c" in art["content"]
    assert "itmedia.co.jp/b/2" not in art["content"]          # 同じドメインは1件だけ
    assert "下書きができました" in sent["text"] and "post=42" in sent["text"]
    files = list((tmp / "data" / "articles").glob("2026-10-04-1-*.json"))
    assert len(files) == 1
    saved = json.loads(files[0].read_text(encoding="utf-8"))
    assert saved["wordpress"]["id"] == 42 and saved["memo"] == "無料版は3回まで"
    assert files[0].with_suffix(".html").exists()
    assert not any("参考記事が1件" in w for w in saved["warnings"])
    assert fake_post.images == 2 and saved["images"] == {"featured": 101, "media": [101, 102]}
    assert "uploads/102.jpg" in art["content"] and "AI生成" in art["content"]
    assert art["content"].index("uploads/102.jpg") > art["content"].index("想定シナリオ")
    print(sent["text"])


def test_without_tavily_and_wp(tmp=Path(tempfile.gettempdir()) / "ts_article2"):
    """Tavily も WordPress も未設定：元記事だけで書き、保存だけする。"""
    _setup(tmp)
    for k in ("TAVILY_API_KEY", "WP_URL", "WP_USER", "WP_APP_PASSWORD"):
        os.environ.pop(k, None)
    page = "<html><body><nav>メニュー</nav><article><p>" + SRC_MAIN + "</p></article></body></html>"
    fake_llm.with_memo = False
    def no_search_llm(prompt, cfg, max_tokens=4000):
        if "事実」だけを抜き出して" in prompt:
            assert "参考1:" in prompt and "参考2:" not in prompt and "メニュー" not in prompt
            return json.dumps({"summary": "", "facts": []})
        return fake_llm(prompt, cfg, max_tokens)
    with mock.patch.object(research.requests, "get", lambda url, **kw: Resp(text=page)), \
         mock.patch.object(writer, "call_llm", no_search_llm), \
         mock.patch.object(article, "BASE", tmp):
        art = article.run(1, dry_run=True, config_path=Path(article.__file__).parent / "config.yaml")
    assert art["wordpress"] is None and len(art["sources"]) == 1
    assert any("参考記事が1件" in w for w in art["warnings"])


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"): fn(); print("OK", name)
