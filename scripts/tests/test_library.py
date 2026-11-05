"""Offline behavioral checks. These do not claim to validate WeRead's live UI."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import corpus
import library
import runtime
import catalogue
from book_ids import reader_hash,web_id


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.catalogue_home=patch.object(catalogue,"DATA_HOME",self.root/"library")
        self.catalogue_home.start()

    def tearDown(self):
        self.catalogue_home.stop()
        self.temp.cleanup()

    def fixture(self, complete=True):
        book = self.root / "book"
        runtime.private_dir(book / "chapters")
        chapters = [{"id": "1", "title": "理解反馈", "words": 50}, {"id": "2", "title": "学习计划", "words": 40}]
        runtime.write_json(book / "meta.json", {"title": "离线验收样本", "author": "测试作者", "web_id": "sample", "chapters": chapters})
        texts = ["# 理解反馈\n\n" + "具体反馈帮助学习者理解自己的进步，反馈应关注尝试的方法。" * 20,
                 "# 学习计划\n\n" + "学习计划包含每天的练习时间与复盘方式。" * 30]
        points = {}
        for i, text in enumerate(texts[:2 if complete else 1], 1):
            runtime.atomic_text(book / "chapters" / f"{i:04d}-{i}.md", text)
            points[str(i)] = {"finished": True, "sha256": hashlib.sha256(text.encode()).hexdigest()}
        runtime.write_json(book / "checkpoints.json", points)
        return book

    def test_complete_epub_and_chinese_evidence(self):
        book = self.fixture()
        result = corpus.build(book)
        self.assertEqual(result["status"], "catalogue_complete")
        self.assertEqual(result["completeness"], "unverified")
        self.assertEqual(corpus.validate_epub(result["artifacts"]["epub"])["spine_chapters"], 2)
        answer = corpus.retrieve(book, "反馈如何促进进步")
        self.assertEqual(answer["matches"][0]["chapter_id"], "1")
        passage = answer["matches"][0]
        text = Path(passage["path"]).read_text()
        self.assertEqual(text[passage["char_start"]:passage["char_end"]], passage["text"])

    def test_missing_chapter_is_partial(self):
        result = corpus.build(self.fixture(False))
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["missing_chapters"], 1)
        self.assertTrue(result["artifacts"]["epub"].endswith("book.partial.epub"))

    def test_changed_file_is_not_verified_checkpoint(self):
        book = self.fixture()
        corpus.build(book)
        (book / "chapters/0001-1.md").write_text("# 错误页面\n\n请购买本书后继续阅读。")
        self.assertEqual(library.verify(book)["damaged_chapters"], [1])
        result = corpus.build(book)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["suspect_chapters"], 1)

    def test_empty_header_is_not_text(self):
        self.assertIn("empty_body", corpus.quality("# 第一章\n\n")["issues"])
        self.assertIn("shorter_than_catalogue", corpus.quality("# 第一章\n\n只有一句", 1000)["issues"])

    def test_catalogue_count_tolerates_short_headings_but_detects_long_truncation(self):
        self.assertEqual(corpus.quality("# 第一部分 学习兴趣带来内驱力\n\n" + "文"*71,107)["issues"], [])
        self.assertIn("shorter_than_catalogue", corpus.quality("正文"*2000,7500)["issues"])
        self.assertIn("shorter_than_catalogue", corpus.quality("文"*6601,7526)["issues"])

    def test_licensed_reader_copyright_requires_complete_template(self):
        page = ("# 版权信息\n\n## 版权信息\n\n书名：离线样本\n作者：测试作者\n\n"
                "出版社：测试出版社\n\n出版时间：2023-11-01\n\nISBN：9787559672346\n\n"
                "品牌方：测试出版公司\n\n本书由测试出版公司授权微信读书进行制作与发行\n版权所有·侵权必究\n")
        result = corpus.quality(page, 454)
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["source_note"], "reader_generated_copyright")
        for incomplete in (page.replace("版权所有·侵权必究", ""),
                           page.replace("ISBN：9787559672346", ""),
                           page.replace("出版时间：2023-11-01", ""),
                           page.replace("版权信息", "第一章")):
            with self.subTest(page=incomplete):
                self.assertIn("shorter_than_catalogue", corpus.quality(incomplete, 454)["issues"])

    def test_builtin_ids_match_verified_reader_urls(self):
        ident="9ea32760725a697c9ea04e6"
        self.assertEqual(reader_hash("39479676"),ident)
        self.assertEqual(web_id(f"https://weread.qq.com/web/reader/{ident}k123"),ident)
        self.assertEqual(reader_hash("53"),"d8232f00235d82c8d161fb2")

    def test_metadata_is_authoritative_manifest_is_derived(self):
        book=self.fixture(); corpus.build(book)
        raw=json.loads((book/"manifest.json").read_text())
        self.assertEqual(raw["version"],2)
        self.assertNotIn("title",raw)
        self.assertNotIn("author",raw)
        self.assertNotIn("title",raw["chapters"][0])
        meta=json.loads((book/"meta.json").read_text()); meta["title"]="更正书名"
        runtime.write_json(book/"meta.json",meta)
        self.assertEqual(corpus.load_manifest(book)["title"],"更正书名")
        self.assertEqual(catalogue.search("更正书名")[0]["title"],"更正书名")

    def test_local_cache_hit_never_calls_auth_api_or_browser(self):
        book=self.fixture()
        meta=runtime.read_json(book/"meta.json")
        meta.update(book_id="39479676",web_id=reader_hash("39479676"))
        runtime.write_json(book/"meta.json",meta); corpus.build(book)
        with patch.object(library,"omni",side_effect=AssertionError("must stay offline")),patch.object(library,"browser_command",side_effect=AssertionError("must not launch")),patch.object(library,"default_account",side_effect=AssertionError("must not need login")):
            found=library.find_book("离线验收样本",online=True)
            self.assertEqual(found["next_action"],"read_local_book")
            result=library.export_book("39479676",formats=["txt","html"])
        self.assertTrue(result["cached"])
        self.assertFalse(result["network_used"])
        self.assertEqual(set(result["artifacts"]),{"txt","html"})
        self.assertIn("具体反馈",Path(result["artifacts"]["txt"]).read_text())

    def test_damaged_download_is_not_a_complete_cache_hit(self):
        book=self.fixture(); corpus.build(book)
        (book/"chapters/0002-2.md").unlink()
        self.assertFalse(corpus.cache_status(book)["usable"])
        self.assertIsNone(library.cached_result(book))

    def test_missing_artifact_is_rebuilt_without_network(self):
        book=self.fixture(); m=corpus.build(book)
        Path(m["artifacts"]["epub"]).unlink()
        with patch.object(library,"omni",side_effect=AssertionError("offline")):
            result=library.cached_result(book)
        self.assertTrue(result["cached"])
        self.assertTrue(library.verify(book)["ok"])

    def test_corrected_metadata_updates_cached_epub_without_network(self):
        book=self.fixture();corpus.build(book)
        meta=runtime.read_json(book/"meta.json");meta["title"]="修订后的书名"
        runtime.write_json(book/"meta.json",meta)
        with patch.object(library,"omni",side_effect=AssertionError("offline")):
            result=library.cached_result(book)
        with zipfile.ZipFile(result["artifacts"]["epub"]) as archive:
            self.assertIn("修订后的书名",archive.read("OEBPS/content.opf").decode())

    def test_default_epub_and_selected_text_format(self):
        book=self.fixture(); result=corpus.build(book,formats=["md"])
        self.assertEqual(set(result["artifacts"]),{"md"})
        self.assertTrue(library.verify(book)["ok"])
        result=corpus.build(book)
        self.assertIn("epub",result["artifacts"])

    def test_local_near_title_and_duplicate_editions_need_confirmation(self):
        book=self.fixture(); corpus.build(book)
        self.assertTrue(library.find_book("离线验收")['selection_required'])
        import shutil
        second=self.root/"second"; shutil.copytree(book,second)
        meta=runtime.read_json(second/"meta.json");meta["author"]="另一作者"
        runtime.write_json(second/"meta.json",meta); corpus.build(second)
        result=library.find_book("离线验收样本")
        self.assertEqual(result["next_action"],"confirm_edition")
        self.assertIsNone(result["selected"])

    def test_empty_weread_search_requests_consent_for_web_fallback(self):
        with patch.object(library,"default_account",return_value="default"),patch.object(library,"omni",return_value={"books":[]}):
            result=library.find_book("不存在的验收书",online=True)
        self.assertEqual(result["next_action"],"ask_web_fallback")
        self.assertTrue(result["fallback"]["requires_user_consent"])

    def test_pdf_empty_pages_are_not_silently_dropped(self):
        source=self.root/"sample.pdf"; source.write_bytes(b"fixture")
        result=corpus.ingest(source,self.root/"pdf",pdf_data={"pages":["Text on the first page.","","Text on the third page."],"title":"Page coverage"})
        self.assertEqual(result["expected_chapters"],3)
        self.assertEqual(result["missing_chapters"],1)
        self.assertEqual(result["status"],"partial")

    def test_omni_session_reuse_is_account_scoped_and_excludes_refresh_token(self):
        directory = self.root / "accounts/reader"
        runtime.write_json(directory/"account.json", {"version":1,"client":"eink"})
        runtime.write_json(directory/"clients/eink.json", {"version":1,"eink":{
            "vid":"12345","accessToken":"fake-access-for-test","refreshToken":"do-not-copy","deviceId":"test"}})
        with patch.dict(os.environ, {"WEREAD_CONFIG_DIR":str(self.root)}):
            self.assertEqual(runtime.omni_web_session("reader","12345"), {"vid":"12345","access_token":"fake-access-for-test"})
            self.assertIsNone(runtime.omni_web_session("other"))
            with self.assertRaises(runtime.Failure) as mismatch:
                runtime.omni_web_session("reader","67890")
            self.assertEqual(mismatch.exception.code,"ACCOUNT_MISMATCH")
            with self.assertRaises(runtime.Failure):
                runtime.omni_web_session("../reader")

    def test_existing_omni_alias_is_used_without_another_login(self):
        with patch.dict(os.environ,{"WEREAD_CONFIG_DIR":str(self.root)}):
            self.assertEqual(runtime.default_account(),"default")
            runtime.write_json(self.root/"accounts/personal/account.json",{"version":1,"client":"eink"})
            self.assertEqual(runtime.default_account(),"personal")
            runtime.write_json(self.root/"accounts/work/account.json",{"version":1,"client":"eink"})
            with self.assertRaises(runtime.Failure) as multiple:
                runtime.default_account()
            self.assertEqual(multiple.exception.code,"ACCOUNT_SELECTION_REQUIRED")
            runtime.write_json(self.root/"config.json",{"version":1,"defaultAccount":"work"})
            self.assertEqual(runtime.default_account(),"work")

    def test_cover_asset_is_embedded_and_checked(self):
        book = self.fixture()
        images = runtime.private_dir(book / "images")
        name = "a"*64 + ".png"
        (images / name).write_bytes(b"test image payload")
        path = book / "images-test.epub"
        corpus.make_epub(path,"含封面","测试作者",[("封面",f"![封面](images/{name})")],"test",book)
        self.assertEqual(corpus.validate_epub(path)["spine_chapters"],1)
        with zipfile.ZipFile(path) as z:
            self.assertEqual(z.read("OEBPS/images/"+name),b"test image payload")
        self.assertEqual(corpus.quality(f"# 封面\n\n![封面](images/{name})",4)["issues"],[])

    def test_missing_image_is_not_valid_epub(self):
        path = self.root / "broken.epub"
        with self.assertRaises(runtime.Failure):
            corpus.make_epub(path,"缺图","测试作者",[("封面","![封面](images/"+"b"*64+".png)")],"test",self.root)

    def test_epub_round_trip_preserves_spine_and_text(self):
        book = self.fixture()
        original = corpus.build(book)
        result = corpus.ingest(Path(original["artifacts"]["epub"]), self.root / "imported")
        self.assertEqual(result["title"], "离线验收样本")
        self.assertEqual(result["expected_chapters"], 2)
        matches = corpus.retrieve(self.root / "imported", "复盘")
        self.assertEqual(matches["matches"][0]["chapter_index"], 2)

    def test_reindex_discards_old_search_hits(self):
        book = self.fixture()
        corpus.build(book)
        (book / "chapters/0002-2.md").unlink()
        corpus.build(book)
        self.assertEqual(corpus.retrieve(book, "复盘")["matches"], [])

    def test_chunk_boundaries_cover_long_paragraph(self):
        text = "这是没有换行的很长一段文字。" * 600
        chunks = list(corpus.chunk_text(text))
        self.assertEqual(chunks[0][0], 0)
        self.assertEqual(chunks[-1][1], len(text))
        for start, end, value in chunks:
            self.assertEqual(value, text[start:end])
        self.assertTrue(all(a[1]>=b[0] for a,b in zip(chunks,chunks[1:])))

    def test_no_evidence_is_explicit(self):
        book = self.fixture(); corpus.build(book)
        result = corpus.retrieve(book, 'xylophonist unlikelytoken')
        self.assertEqual(result["status"], "no_evidence")

    def test_cursor_repetition_stops_search(self):
        response = {"hasMore": 1, "books": [{"searchIdx": 20, "bookInfo": {"bookId": "1", "title": "样本", "author": "甲"}}]}
        with patch.object(library, "omni", return_value=response) as call:
            result = library.search("样本", None, "default")
        self.assertEqual(call.call_count, 2)
        self.assertEqual(result["pagination_terminal"], "missing_or_repeated_cursor")
        self.assertEqual(len(result["candidates"]), 1)

    def test_same_title_multiple_authors_needs_selection(self):
        response = {"books": [{"bookInfo": {"bookId": str(i), "title": "样本", "author": name}} for i,name in enumerate(("甲", "乙"))]}
        with patch.object(library, "omni", return_value=response):
            self.assertTrue(library.search("样本", None, "default")["selection_required"])
            self.assertFalse(library.search("样本", "乙", "default")["selection_required"])

    def test_login_required_string_error_and_manual_verification(self):
        self.assertEqual(runtime.classify_error({"error": 'account "default" could not be read'}), "LOGIN_REQUIRED")
        self.assertEqual(runtime.classify_error({"error": {"errCode": -2041, "status":401}}), "HUMAN_VERIFICATION")
        self.assertEqual(runtime.classify_error({"error": {"status":429}}), "RATE_LIMITED")
        self.assertEqual(runtime.classify_error({"error":"request rejected", "errCode":-2041, "status":400}), "HUMAN_VERIFICATION")

    def test_archive_traversal_is_rejected(self):
        zpath = self.root / "bad.zip"
        with zipfile.ZipFile(zpath,"w") as z:
            z.writestr("../escape", "unsafe")
        with self.assertRaises(runtime.Failure):
            runtime.unpack(zpath,self.root/"unpacked")
        self.assertFalse((self.root / "escape").exists())

    def test_account_path_injection_rejected(self):
        with self.assertRaises(runtime.Failure):
            runtime.account_name("../../someone")

    def test_cli_runs_without_runtime_for_local_ingest(self):
        source=self.root/"自己的书.md"
        source.write_text("# 导言\n\n这是离线编写的测试内容，可以在没有浏览器或登录时导入。\n\n# 方法\n\n每天复盘记录。",encoding="utf-8")
        result=subprocess.run([sys.executable,str(Path(library.__file__)),"ingest",str(source),"--out",str(self.root/"cli")],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(json.loads(result.stdout)["expected_chapters"],2)


if __name__ == "__main__":
    unittest.main()
