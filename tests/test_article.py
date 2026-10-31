"""記事生成（Ver.1/2）のオフラインテスト。Tavily・LLM・WordPress・LINE はモック。
実行: python tests/test_article.py
"""
import base64, json, os, shutil, sys, tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import affiliate, article, research, roundup, shots, writer, wordpress  # noqa: E402
from datetime import datetime  # noqa: E402

SRC_MAIN = ("OpenAIは10月2日、ChatGPTにバーチャル試着機能を追加したと発表した。利用者が自分の写真をアップロードすると、"
            "商品の服やアクセサリーを合成した画像を表示する。まず米国のPlusユーザーから提供を始める。" * 8)
SRC_OTHER = ("ChatGPTの新しい試着機能は、ショッピング検索の結果に表示される商品カードから使える。"
             "日本での提供時期は明らかにされていない。iOSアプリでは書類のスキャン機能も追加された。" * 8)
COPIED = "利用者が自分の写真をアップロードすると、商品の服やアクセサリーを合成した画像を表示する。"

CAND = [{"topic": "ChatGPT-virtual-tryon", "total": 82, "detail": {"summary": "試着機能の追加"},
         "item": {"source": "ITmedia AI+", "title": "ChatGPTにバーチャル試着機能", "url": "https://www.itmedia.co.jp/a/1",
                  "summary": "", "signal": None, "signal_label": "", "extra": {}}}]


class Resp:
    def __init__(self, js=None, text="", status=200, content=b""):
        self._js, self.text, self.status_code = js, text or json.dumps(js), status
        self.content = content
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
        assert (kw["data"], kw["headers"]["Content-Type"]) == (b"JPEGDATA", "image/jpeg")
        fake_post.media += 1
        mid = 100 + fake_post.media
        return Resp({"id": mid, "source_url": f"https://nocode-ai.net/wp-content/uploads/{mid}.jpg"})
    if url.endswith("/wp-json/wp/v2/categories"):
        assert kw["json"] == {"name": "動画生成"}
        return Resp({"id": 7})
    if "/wp-json/wp/v2/media/" in url:
        return Resp({"id": 1})
    if url.endswith("/wp-json/wp/v2/posts"):
        assert kw["json"]["status"] == "draft" and kw["auth"] == ("nonpro", "xxxx xxxx")
        assert kw["json"]["featured_media"] == 101 and kw["json"]["categories"] == [7]
        return Resp({"id": 42, "link": "https://nocode-ai.net/?p=42"})
    raise AssertionError(url)
fake_post.images = fake_post.media = 0


BODY = ("<p>ChatGPTに試着機能が加わりました。この記事では、できることと注意点をまとめます。</p>"
        + writer.EASY_DIV + "<p><strong>ざっくり言うと</strong></p><ul><li>服を着た自分の姿を、写真で確かめられる道具です</li></ul></div>"
        "<h2>ChatGPTに試着機能が登場</h2><p>OpenAIが新しい機能を出しました。</p>"
        "<h2>何ができるのか</h2><p>" + COPIED + "</p>"
        + "<p>ネットで服を買う前に、似合うかどうかを確かめられます。</p>" * 30
        + "<h2>料金と使える地域</h2><p>まず米国のPlusユーザーから使えます。</p>"
        + "<h2>まとめ</h2><p>日本での提供時期は公式サイトで確認してください。</p>")


def fake_llm(prompt, cfg, max_tokens=4000, *a, **kw):
    if "事実」だけを抜き出して" in prompt:
        assert "参考1:" in prompt and "参考2:" in prompt and "参考3:" not in prompt  # 同ドメイン・短文は除外
        links = prompt.split("# リンク候補", 1)[1].split("# 出力")[0]
        assert "twitter.com" not in links and "itmedia.co.jp/menu" not in links   # SNS・同じサイト内は候補にしない
        ok = "3. https://tryon.example/（公式サイト）" in links and "4. https://tryon.example/app" in links
        return json.dumps({"summary": "試着機能", "facts": [{"fact": "米国Plusから提供", "src": 1}],
                           "compare": [], "cautions": [], "unknowns": ["日本での提供時期"],
                           "official_link": 3 if ok else 0, "download_link": 4 if ok else 99, **fake_llm.facts_extra},
                          ensure_ascii=False)
    if "記事の構成を作って" in prompt:
        schema = a[0] if a else kw.get("schema")
        assert not schema or "動画生成" in schema["properties"]["category"]["enum"]   # 決めたカテゴリだけ選ばせる
        assert writer.STRUCTURE in prompt and "導入手順" not in prompt and "persona" not in prompt   # 新ツール解説の流れ
        return json.dumps({"title": "ChatGPTの試着機能でできること", "slug": "chatgpt-virtual-try-on",
                           "excerpt": "説明", "category": "動画生成", "sections": []}, ensure_ascii=False)
    if "記事本文を書いて" in prompt:
        assert ("無料版は3回まで" in prompt) == fake_llm.with_memo
        return "```html\n" + BODY + "\n```"
    if "校閲者です" in prompt:
        return prompt.split("# 記事\n", 1)[1].split("\n\n# 出力")[0]
    if "AIにくわしくない読者です" in prompt:
        assert "<" not in prompt.split("# 記事\n", 1)[1].split("\n\n# 出力")[0]   # タグを外して読ませる
        return json.dumps({"words": ["Plus"], "sentences": []}, ensure_ascii=False)
    if "分かるように直した完成版" in prompt:
        assert "Plus" in prompt
        fake_llm.clarified += 1
        return prompt.split("# 記事\n", 1)[1].split("\n\n# 出力")[0]
    if "似すぎています" in prompt:
        blocks = json.loads(prompt.split("# 段落（JSON配列）\n", 1)[1].split("\n\n# 出力")[0])
        return json.dumps(["<p>自分の写真を使って、服や小物を身に着けたイメージを確認できます。</p>" for _ in blocks],
                          ensure_ascii=False)
    raise AssertionError(prompt[:80])
fake_llm.with_memo = False
fake_llm.facts_extra = {}
fake_llm.clarified = 0
OFFICIAL_PAGE = ('<html><body><img src="/hero.png">'
                 '<iframe src="https://www.youtube.com/embed/abcdefghijk"></iframe></body></html>')
PAGE = ('<html><body><nav><a href="https://www.itmedia.co.jp/menu">メニュー</a></nav><article>'
        '<a href="https://twitter.com/share?u=x">ポスト</a><a href="/a/2">同じサイト</a>'
        '<a href="https://tryon.example/">公式サイト</a><a href="https://tryon.example/app#dl">アプリ</a></article></body></html>')


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
    w = writer.check_rules("<p>実際に使ってみると便利でした。</p><h3>ステップ1：登録</h3><h2>Aさんの場合</h2>", "")
    assert any("体験" in x for x in w) and any("ステップ" in x for x in w) and any("架空の人物" in x for x in w)
    assert any("ざっくり" in x for x in writer.check_rules("<p>あ</p>" * 3000, ""))
    ok = writer.check_rules(writer.EASY_DIV + f'</div><h2>何ができるか</h2><div class="{writer.NOTE_CLASS}"><p>使ってみた感想</p></div>' + "<p>あ</p>" * 3000, "メモ")
    assert ok == []
    hedgy = "<h2>料金</h2>" + "<p>上限は明らかにされていません。</p><p>詳しくは公式サイトでご確認ください。</p>" + "<p>あ</p>" * 3000
    assert any("2回" in x for x in writer.check_rules(hedgy, ""))
    calls = []
    def llm(prompt, cfg, *a, **kw):
        calls.append(prompt); return "<p>上限は明らかにされていません。</p>" + "<p>あ</p>" * 3000
    with mock.patch.object(writer, "call_llm", llm):
        fixed = writer.trim_hedges(hedgy, {})
        assert "公式サイト" not in fixed and len(calls) == 1
        assert writer.trim_hedges(fixed, {}) == fixed and len(calls) == 1   # 1回以下なら呼ばない
    leak = writer.check_rules("<h2>料金</h2><p>事実メモによると無料です。</p>", "")
    assert any("指示の言葉" in x for x in leak)


def test_full_article(tmp=Path(tempfile.gettempdir()) / "ts_article"):
    _setup(tmp)
    os.environ.update({"TAVILY_API_KEY": "tvly-x", "WP_URL": "https://nocode-ai.net/", "WP_USER": "nonpro",
                       "WP_APP_PASSWORD": "xxxx xxxx", "LINE_CHANNEL_ACCESS_TOKEN": "t", "LINE_USER_ID": "U1",
                       "OPENAI_API_KEY": "sk-test"})
    sent, shot_urls = {}, []
    fake_llm.with_memo = True
    def no_net(url, **kw):
        if url == CAND[0]["item"]["url"]:
            return Resp(text=PAGE)   # 元記事のリンク（公式サイトの候補）
        if url == "https://tryon.example/":
            return Resp(text=OFFICIAL_PAGE)
        if url.endswith("/wp-json/wp/v2/categories"):
            return Resp([{"id": 3, "name": "画像生成・デザイン"}])   # 「動画生成」はまだない → 作る
        raise AssertionError(f"ネットに出ようとした: {url}")
    with mock.patch.object(research.requests, "post", fake_post), \
         mock.patch.object(research.requests, "get", no_net), \
         mock.patch.object(writer, "call_llm", fake_llm), \
         mock.patch.object(article, "send_line", lambda t: sent.setdefault("text", t)), \
         mock.patch.object(shots, "capture", lambda url: shot_urls.append(url) or b"JPEGDATA"), \
         mock.patch.object(article, "BASE", tmp),          mock.patch.object(affiliate, "PATH", tmp / "aff.yaml"):
        affiliate.command("add", "ChatGPT/チャットGPT", "https://px.a8.net/svt/ejp?a8mat=TEST")
        affiliate.command("add", "Gemini", "https://example.com/gemini")   # 記事に出てこない → 入らない
        affiliate.command("add", "ココナラ", "https://px.a8.net/svt/ejp?a8mat=COCO")
        affiliate.command("category", "ココナラ", "動画", categories=["動画生成", "画像生成・デザイン"])
        art = article.run(1, memo="無料版は3回まで", config_path=Path(article.__file__).parent / "config.yaml")
    assert art["wordpress"]["edit_url"] == "https://nocode-ai.net/wp-admin/post.php?post=42&action=edit"
    assert COPIED not in art["content"]                      # 似すぎた段落は書き直された
    assert art["similarity"]["ok"]
    assert art["content"].startswith(writer.PR_NOTE)
    assert "easy-box memo-box" in art["content"] and not any("ざっくり" in w for w in art["warnings"])
    assert "参考にした情報" in art["content"] and "https://other.example/c" in art["content"]
    assert "itmedia.co.jp/b/2" not in art["content"]          # 同じドメインは1件だけ
    assert "下書きができました" in sent["text"] and "?p=42&preview=true" in sent["text"] and "「公開」" in sent["text"]
    files = list((tmp / "data" / "articles").glob("2026-10-04-1-*.json"))
    assert len(files) == 1
    saved = json.loads(files[0].read_text(encoding="utf-8"))
    assert saved["wordpress"]["id"] == 42 and saved["memo"] == "無料版は3回まで"
    assert files[0].with_suffix(".html").exists()
    assert not any("参考記事が1件" in w for w in saved["warnings"])
    assert fake_post.images == 2 and saved["images"] == {"featured": 101, "media": [101, 102, 103]}
    c = art["content"]
    heads = [i for i in range(len(c)) if c.startswith("<h2", i)]
    # リンク：公式サイトと登録ページは「ざっくり言うと」の下、最初の見出しの前
    assert art["official_url"] == "https://tryon.example/" and art["download_url"] == "https://tryon.example/app"
    assert c.index("easy-box") < c.index('公式サイト：<a href="https://tryon.example/"') < c.index("登録ページ：") < heads[0]
    # 公式サイトの画面：登録ページを撮り、リンクの直後に入れる
    assert shot_urls == ["https://tryon.example/app"] and "公式サイトの画面あり" in sent["text"]
    assert c.index("登録ページ：") < c.index("uploads/103.jpg") < heads[0]
    assert "出典：公式サイト（tryon.example）" in c
    # 公式動画は「何ができるか」の節の手前、本文の画像はその見出しの直後
    assert "youtube-nocookie.com/embed/abcdefghijk" in c and heads[0] < c.index("official-video") < c.index("<h2>何ができるのか")
    assert "公式動画あり" in sent["text"]
    assert c.index("<h2>何ができるのか") < c.index("uploads/102.jpg") < c.index("料金と使える地域")
    assert "AI生成" in c and "operator-shot" not in c and "official-shot" not in c
    assert art["content"].index("affiliate-box") < art["content"].index("<h2>まとめ")
    assert art["content"].count("a8mat=TEST") == 2 and "example.com/gemini" not in art["content"]
    assert 'rel="sponsored noopener"' in art["content"] and art["affiliates"] == ["ChatGPT", "ココナラ"]
    assert art["content"].count("a8mat=COCO") == 1 and "プロに頼む方法" in art["content"]   # カテゴリ「動画生成」の記事
    assert art["content"].index("a8mat=COCO") > art["content"].rindex("a8mat=TEST")        # 最後に出す
    assert "アフィリンク 2件（ChatGPT、ココナラ）" in sent["text"]
    assert fake_llm.clarified >= 1 and art["clarity"]["words"] == ["Plus"]
    assert not any("ボツ推奨" in w for w in art["warnings"])
    print(sent["text"])


def test_without_tavily_and_wp(tmp=Path(tempfile.gettempdir()) / "ts_article2"):
    """Tavily も WordPress も未設定：元記事だけで書き、保存だけする。"""
    _setup(tmp)
    for k in ("TAVILY_API_KEY", "WP_URL", "WP_USER", "WP_APP_PASSWORD"):
        os.environ.pop(k, None)
    page = "<html><body><nav>メニュー</nav><article><p>" + SRC_MAIN + "</p></article></body></html>"
    fake_llm.with_memo = False
    def no_search_llm(prompt, cfg, max_tokens=4000, *a, **kw):
        if "事実」だけを抜き出して" in prompt:
            assert "参考1:" in prompt and "参考2:" not in prompt and "メニュー" not in prompt
            return json.dumps({"summary": "", "facts": []})
        return fake_llm(prompt, cfg, max_tokens)
    with mock.patch.object(research.requests, "get", lambda url, **kw: Resp(text=page)), \
         mock.patch.object(writer, "call_llm", no_search_llm), \
         mock.patch.object(article, "BASE", tmp):
        art = article.run(1, dry_run=True, config_path=Path(article.__file__).parent / "config.yaml")
    assert art["wordpress"] is None and len(art["sources"]) == 1
    assert art["official_url"] == "" and art["download_url"] == "" and "official-link" not in art["content"]  # 範囲外の番号は捨てる
    assert any("参考記事が1件" in w for w in art["warnings"])


def test_irrelevant_source_and_coding(tmp=Path(tempfile.gettempdir()) / "ts_article3"):
    """事実抽出で「関係ない」とされた参考記事は出典から外し、コード前提の話題は警告する。"""
    _setup(tmp)
    os.environ.update({"TAVILY_API_KEY": "tvly-x"})
    for k in ("WP_URL", "WP_USER", "WP_APP_PASSWORD"):
        os.environ.pop(k, None)
    fake_llm.with_memo = False
    fake_llm.facts_extra = {"relevant_sources": [1], "needs_coding": True, "coding_reason": "JSONを書く必要がある", "tool_type": "app",
                            "reader_fit": False, "fit_reason": "HubSpotが前提"}
    try:
        with mock.patch.object(research.requests, "post", fake_post),              mock.patch.object(writer, "call_llm", fake_llm),              mock.patch.object(article, "BASE", tmp):
            art = article.run(1, dry_run=True, config_path=Path(article.__file__).parent / "config.yaml")
    finally:
        fake_llm.facts_extra = {}
    assert [s["url"] for s in art["sources"]] == ["https://www.itmedia.co.jp/a/1"]
    assert "個人がすぐ使えるツールではない可能性（ボツ推奨）: HubSpotが前提" in art["warnings"]
    assert art["tool_type"] == "app" and not any("ステップ" in w for w in art["warnings"])
    assert "other.example" not in art["content"]
    assert art["warnings"][0].startswith("コードを書く前提") and "JSON" in art["warnings"][0]
    assert any("参考記事が1件" in w for w in art["warnings"])


def test_roundup(tmp=Path(tempfile.gettempdir()) / "ts_roundup"):
    """1週間分の候補から評価の高い順に選び、個人向けでないものを外して、まとめを書いて公開する。"""
    shutil.rmtree(tmp, ignore_errors=True)
    folder = tmp / "data" / "candidates"
    folder.mkdir(parents=True)
    def cand(i, total, topic=None):
        return {"topic": topic or f"tool{i}", "total": total, "detail": {"summary": f"ツール{i}"},
                "item": {"source": "x", "title": f"ツール{i}が登場", "url": f"https://news.example/{i}",
                         "summary": "", "signal": None, "signal_label": "", "extra": {}}}
    days = {"2026-10-24": [cand(9, 99)],                                 # 8日前：範囲外
            "2026-10-27": [cand(1, 90), cand(2, 50), cand(3, 85)],
            "2026-10-30": [cand(4, 80), cand(5, 70, "tool1"), cand(6, 60)],   # 5 は 1 と同じ話題
            "2026-10-31": [cand(1, 90), cand(7, 40)]}                       # 同じURLの再掲
    for d, cs in days.items():
        (folder / f"{d}.json").write_text(json.dumps(cs, ensure_ascii=False), encoding="utf-8")
    today = datetime(2026, 10, 31).date()
    assert [c["topic"] for c in roundup.pick(folder, today, 7, 10)] == ["tool1", "tool3", "tool4", "tool6", "tool2", "tool7"]

    os.environ.update({"TAVILY_API_KEY": "tvly-x", "WP_URL": "https://nocode-ai.net", "WP_USER": "nonpro",
                       "WP_APP_PASSWORD": "xxxx xxxx", "OPENAI_API_KEY": "sk-test"})
    posted, sent = {}, {}
    def post(url, **kw):
        if url == research.TAVILY_EXTRACT:
            return Resp({"results": [{"url": kw["json"]["urls"][0], "raw_content": SRC_MAIN}]})
        if url.endswith("/wp-json/wp/v2/posts"):
            posted.update(kw["json"])
            return Resp({"id": 77, "link": "https://nocode-ai.net/weekly/"})
        if url.endswith("/wp-json/wp/v2/categories"):
            assert kw["json"] == {"name": "今週のAIツールまとめ"}
            return Resp({"id": 9})
        return fake_post(url, **kw)
    def get(url, **kw):
        if url.endswith("/wp-json/wp/v2/categories"):
            return Resp([])
        n = url.rsplit("/", 1)[1]
        return Resp(text=f'<html><body><article><a href="https://tool{n}.example/">公式</a></article></body></html>')
    def llm(prompt, cfg, max_tokens=4000, *a, **kw):
        if "記事から「事実」だけを抜き出して" in prompt:
            n = int(prompt.split("# 話題\nツール", 1)[1][0])
            assert f"2. https://tool{n}.example/" in prompt
            return json.dumps({"name": f"Tool{n}", "facts": [f"Tool{n}は無料で使える"], "reader_fit": n != 3,
                               "is_tool": True, "official_link": 2}, ensure_ascii=False)
        if "今週の新着AIツールまとめ" in prompt:
            names = [x["name"] for x in json.loads(prompt.split("# 今週のツール（事実メモ。番号順に紹介する）\n", 1)[1].split("\n\n# 守ること")[0])]
            assert names == ["Tool1", "Tool4", "Tool6"]   # 3 は個人向けでないので外れ、上限3件
            return json.dumps({"title": "今週の新着AIツール3選", "slug": "weekly-ai-tools", "excerpt": "説明",
                               "intro": "<p>今週は3つ。</p>", "easy": ["Tool1：何かを作る道具"],
                               "items": [{"n": i, "heading": f"{nm}：できること", "body": f"<p>{nm}の説明です。</p>",
                                          "who": "初めての人", "price": "無料"} for i, nm in enumerate(names, 1)],
                               "closing": "<p>気になるものから見てください。</p>", "image_featured": "desk"}, ensure_ascii=False)
        if "AIにくわしくない読者です" in prompt:
            return json.dumps({"words": [], "sentences": []})
        raise AssertionError(prompt[:80])
    cfg_path = tmp / "config.yaml"
    import yaml
    cfg = yaml.safe_load((Path(article.__file__).parent / "config.yaml").read_text(encoding="utf-8"))
    cfg["roundup"]["max_items"] = 3
    cfg_path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    with mock.patch.object(research.requests, "post", post), mock.patch.object(research.requests, "get", get), \
         mock.patch.object(roundup, "call_llm", llm), mock.patch.object(writer, "call_llm", llm), \
         mock.patch.object(roundup, "send_line", lambda t: sent.setdefault("text", t)), \
         mock.patch.object(roundup, "BASE", tmp), mock.patch.object(affiliate, "PATH", tmp / "aff.yaml"):
        art = roundup.run(config_path=cfg_path, today=today)
    assert art["published"] and posted["status"] == "publish" and posted["categories"] == [9]
    assert posted["slug"] == "weekly-ai-tools-20261031"
    c = art["content"]
    # 各ツールの節の終わりに、そのツールの公式サイト
    for n, nxt in ((1, "2. Tool4"), (4, "3. Tool6"), (6, "<h2>まとめ")):
        assert c.index(f"Tool{n}の説明です") < c.index(f"https://tool{n}.example/") < c.index(nxt)
    assert "easy-box" in c and "<strong>料金：</strong>無料" in c and "参考にした情報" in c
    assert "公開しました" in sent["text"] and "・Tool6" in sent["text"] and "https://nocode-ai.net/weekly/" in sent["text"]
    assert (tmp / "data" / "roundups" / "2026-10-31.json").exists()
    # 体験表現があれば公開しない
    assert roundup.check("<p>使ってみると便利</p>") and not roundup.check("<p>便利です</p>")
    # 紹介できるものが少なければ作らない
    for f in folder.glob("*.json"):
        f.unlink()
    with mock.patch.object(roundup, "send_line", lambda t: sent.update(text=t)), mock.patch.object(roundup, "BASE", tmp):
        assert roundup.run(config_path=cfg_path, today=today) is None and "作りませんでした" in sent["text"]


def test_wp_command(tmp=Path(tempfile.gettempdir()) / "ts_wpcmd"):
    import wp_command
    shutil.rmtree(tmp, ignore_errors=True)
    (tmp / "data" / "articles").mkdir(parents=True)
    for name, pid, created in (("a", 22, "2026-10-04T10:44:49+09:00"), ("b", 23, "2026-10-04T10:49:31+09:00"),
                               ("c", None, "2026-10-04T11:00:00+09:00")):
        (tmp / "data" / "articles" / f"{name}.json").write_text(json.dumps(
            {"created_at": created, "wordpress": {"id": pid} if pid else None}), encoding="utf-8")
    os.environ.update({"WP_URL": "https://nocode-ai.net", "WP_USER": "nonpro", "WP_APP_PASSWORD": "xxxx xxxx"})
    state = {23: "draft", 30: "publish"}
    calls = []
    def get(url, **kw):
        pid = int(url.rsplit("/", 1)[1])
        return Resp({"id": pid, "status": state[pid], "link": f"https://nocode-ai.net/p{pid}/", "title": {"raw": f"記事{pid}"}})
    def post(url, **kw):
        pid = int(url.rsplit("/", 1)[1]); state[pid] = kw["json"]["status"]; calls.append(("post", pid))
        return get(url)
    def delete(url, **kw):
        calls.append(("delete", int(url.rsplit("/", 1)[1]))); return Resp({"deleted": False})
    with mock.patch.object(wordpress.requests, "get", get), mock.patch.object(wordpress.requests, "post", post), \
         mock.patch.object(wordpress.requests, "delete", delete), mock.patch.object(wp_command, "BASE", tmp):
        assert "公開しました" in wp_command.run("publish") and calls == [("post", 23)]   # 最新=23
        assert "すでに公開済み" in wp_command.run("publish", "23")
        assert "削除しません" in wp_command.run("trash", "30") and len(calls) == 1      # 公開済みは消さない
        state[23] = "draft"
        assert "ゴミ箱" in wp_command.run("trash", "23") and calls[-1] == ("delete", 23)


def test_affiliate(tmp=Path(tempfile.gettempdir()) / "ts_aff.yaml"):
    tmp.unlink(missing_ok=True)
    assert "まだ登録がありません" in affiliate.command("list", path=tmp)
    assert "登録しました" in affiliate.command("add", "Clips by Plastic／Clips", "https://a.example/x", path=tmp)
    assert "更新しました" in affiliate.command("add", "clips", "<a href='https://px.a8.net/y'>Clips</a><img src='p.gif'>", path=tmp)
    es = affiliate.load(tmp)
    assert len(es) == 1 and es[0]["name"] == "Clips by Plastic" and es[0]["link"].startswith("<a")  # 別名でも同じツールとして上書き
    es = [{"name": "Clef", "aliases": [], "link": "https://c.example"}, {"name": "動画メーカー", "aliases": [], "link": "https://d.example"}]
    assert affiliate.match(es, "Clefairyの話", "") == []                   # 単語の途中には一致しない
    assert [e["name"] for e in affiliate.match(es, "", "clef と動画メーカー。Clef")] == ["Clef", "動画メーカー"]
    body = "<p>導入</p><h2>【想定シナリオ】</h2><p>例</p><h2>まとめ</h2><p>終わり</p>"
    out = affiliate.insert(body, es[:1])
    assert out.count("affiliate-box") == 2 and out.index("affiliate-box") < out.index("まとめ")
    assert "<img src='p.gif'>" in affiliate.box([{"name": "x", "link": "<a href='h'>x</a><img src='p.gif'>"}])  # ASPのHTMLはそのまま
    try:
        affiliate.command("add", "X", "ftp://bad", path=tmp); raise AssertionError
    except ValueError:
        pass
    cats = ["動画生成", "画像生成・デザイン", "音声・ナレーション"]
    assert "画像生成・デザイン、動画生成" in affiliate.command("category", "Clips", "画像、動画", path=tmp, categories=cats)
    affiliate.command("add", "Clips", "https://new.example", path=tmp)                  # リンク更新でカテゴリは消えない
    assert affiliate.load(tmp)[0]["categories"] == ["画像生成・デザイン", "動画生成"]
    assert "カテゴリ:" in affiliate.command("list", path=tmp)
    try:
        affiliate.command("category", "Clips", "料理", path=tmp, categories=cats); raise AssertionError
    except ValueError:
        pass
    assert "解除" in affiliate.command("category", "Clips", "なし", path=tmp, categories=cats)
    assert "先に" in affiliate.command("category", "Notta", "動画", path=tmp, categories=cats)
    e = {"name": "ココナラ", "link": "https://c", "categories": ["動画生成"]}
    assert affiliate.by_category([e], "動画生成") == [e] and affiliate.by_category([e], "動画生成", [e]) == []
    assert "削除しました" in affiliate.command("remove", "Clips by Plastic", path=tmp)
    assert "台帳にありません" in affiliate.command("remove", "Clips", path=tmp)


def test_worker_parse():
    """Cloudflare Worker の返信の読み取り（node があるときだけ）。"""
    import subprocess
    worker = Path(__file__).resolve().parents[1] / "worker" / "line_webhook.js"
    js = (f"import {{ parseCommand as p }} from {json.dumps(worker.as_uri())};"
          "console.log(JSON.stringify(['1','２ 無料版は3回まで','3：メモ\\n2行目','4','公開','公開 23','削除','ボツ　23','こんにちは','リンク Clips by Plastic/Clips https://px.a8.net/a?b=1','リンク一覧','リンク削除 Clips','リンク Clips','リンク Clips <a href=x>x</a>','リンクカテゴリ ココナラ 画像、動画','スクショ確認','スクショ取消'].map(p)));")
    try:
        out = subprocess.run(["node", "--input-type=module", "-e", js], capture_output=True, text=True,
                             encoding="utf-8", timeout=30)
    except FileNotFoundError:
        print("  (node がないので省略)"); return
    assert out.returncode == 0, out.stderr
    r = json.loads(out.stdout)
    assert r[0] == {"type": "write-article", "payload": {"n": "1", "memo": "", "date": ""}}
    assert r[1]["payload"] == {"n": "2", "memo": "無料版は3回まで", "date": ""}
    assert r[2]["payload"]["memo"] == "メモ\n2行目"
    assert r[3]["type"] == "help" and r[8]["type"] == "help"
    assert r[4] == {"type": "wp-command", "payload": {"action": "publish", "id": ""}}
    assert r[5]["payload"]["id"] == "23" and r[6]["payload"]["action"] == "trash" and r[7]["payload"]["id"] == "23"
    assert r[9] == {"type": "affiliate-link", "payload": {"action": "add", "name": "Clips by Plastic/Clips",
                                                          "link": "https://px.a8.net/a?b=1"}}
    assert r[10]["payload"]["action"] == "list" and r[11]["payload"] == {"action": "remove", "name": "Clips", "link": ""}
    assert r[12]["type"] == "help" and r[13]["payload"]["link"] == '<a href=x>x</a>'
    assert r[14]["payload"] == {"action": "category", "name": "ココナラ", "link": "画像、動画"}
    assert r[15] == {"type": "line-shot", "payload": {"action": "list", "id": ""}} and r[16]["payload"]["action"] == "clear"


def test_worker_image():
    """LINE に画像を送ると line-shot を起動し、まとめ送りなら最後の1枚にだけ返事する（node があるときだけ）。"""
    import subprocess
    worker = Path(__file__).resolve().parents[1] / "worker" / "line_webhook.js"
    js = f"""
import w, {{ verify }} from {json.dumps(worker.as_uri())};
const calls = [];
globalThis.fetch = async (url, opt) => {{ calls.push([url, JSON.parse(opt.body)]); return {{ status: 204, ok: true }}; }};
const ev = (i, set) => ({{ type: "message", replyToken: "r" + i, source: {{ userId: "U1" }},
  message: {{ type: "image", id: "55" + i, ...(set ? {{ imageSet: {{ id: "s", index: i, total: 2 }} }} : {{}}) }} }});
const body = JSON.stringify({{ events: [ev(1, true), ev(2, true), ev(3, false)] }});
const key = await crypto.subtle.importKey("raw", new TextEncoder().encode("sec"), {{ name: "HMAC", hash: "SHA-256" }}, false, ["sign"]);
const sig = btoa(String.fromCharCode(...new Uint8Array(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(body)))));
await w.fetch(new Request("https://x/", {{ method: "POST", body, headers: {{ "x-line-signature": sig }} }}),
  {{ LINE_CHANNEL_SECRET: "sec", LINE_CHANNEL_ACCESS_TOKEN: "t", LINE_USER_ID: "U1", GITHUB_TOKEN: "g", GITHUB_REPO: "a/b" }});
console.log(JSON.stringify(calls));
"""
    try:
        out = subprocess.run(["node", "--input-type=module", "-e", js], capture_output=True, text=True,
                             encoding="utf-8", timeout=30)
    except FileNotFoundError:
        print("  (node がないので省略)"); return
    assert out.returncode == 0, out.stderr
    calls = json.loads(out.stdout.strip().splitlines()[-1])
    disp = [b for u, b in calls if u.endswith("/dispatches")]
    assert [b["client_payload"] for b in disp] == [{"action": "save", "id": f"55{i}"} for i in (1, 2, 3)]
    assert all(b["event_type"] == "line-shot" for b in disp)
    replies = [b for u, b in calls if u.endswith("/reply")]
    assert [r["replyToken"] for r in replies] == ["r2", "r3"] and "（2枚）" in replies[0]["messages"][0]["text"]


def test_shots_prune(tmp=Path(tempfile.gettempdir()) / "ts_shots"):
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    for name in ("20261020-090000-1.jpg", "20261028-090000-2.png", "memo.txt"):
        (tmp / name).write_bytes(b"x")
    old = shots.prune(tmp, datetime(2026, 10, 29, tzinfo=article.JST))
    assert [p.name for p in old] == ["20261020-090000-1.jpg"] and [p.name for p in shots.pending(tmp)] == ["20261028-090000-2.png"]
    assert shots.mime_of(b"\x89PNG\r\n\x1a\nabc") == "image/png" and shots.mime_of(b"\xff\xd8") == "image/jpeg"
    try:
        shots.save("../etc", tmp); raise AssertionError("数字以外のIDを通した")
    except ValueError:
        pass
    assert shots.clear(tmp) == 1 and shots.pending(tmp) == []


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"): fn(); print("OK", name)
