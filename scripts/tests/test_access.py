"""Access-control contracts. Live observations are identified; synthetic cases aren't live proof."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from access import reader_issue, fallback_for
from book_ids import reader_hash
import corpus
import catalogue
import runtime

try:
    import export_runner
except ModuleNotFoundError as error:
    if error.name not in ("pyppeteer", "aiohttp"):
        raise
    export_runner = None


# Observed in the real anonymous reader on 2026-11-05, book 39479676, chapter 35.
ANONYMOUS_PREVIEW = {
    "buttons":["登录后获得专属福利 · 百万好书免费读", "下一章"],
    "footer":"登录后获得专属福利 · 百万好书免费读\n下一章",
    "dialogs":"", "paywalls":"", "paywall_visible":False, "trial_end_visible":False,
}


class AccessTests(unittest.TestCase):
    def test_observed_nonmember_gate_does_not_depend_on_repeated_footer_text(self):
        # Live book 3300235025 chapter 5: 193-character preview and a next button.
        prompt="会员卡 19元/月 · 开通后即可阅读"
        for state in ({"buttons":[prompt,"下一章"]},{"footer":prompt+"\n下一章"}):
            result=reader_issue(state)
            self.assertEqual(result["code"],"ACCESS_LIMITED")
            self.assertEqual(result["access"]["reason"],"membership_required")

    def test_platform_generated_rights_page_has_a_narrow_count_exception(self):
        text="# 版权信息\n\n书名：读毛选，走出人生困局\n作者：温伯陵\n出品时间：2026-10-14\n联合策划：果麦文化\n本书由微信读书出品并制作与发行\n版权所有·侵权必究"
        self.assertEqual(corpus.quality(text,429)["issues"],[])
        self.assertEqual(corpus.quality(text,429)["source_note"],"reader_generated_copyright")
        self.assertIn("shorter_than_catalogue",corpus.quality(text.replace("本书由微信读书出品并制作与发行",""),429)["issues"])
        self.assertIn("shorter_than_catalogue",corpus.quality(text.replace("# 版权信息","# 普通章节"),429)["issues"])
    def test_observed_preview_next_button_is_not_full_access(self):
        self.assertEqual(reader_issue(ANONYMOUS_PREVIEW)["code"], "WEB_LOGIN_REQUIRED")

    def test_trial_and_entitlement_templates_override_next_button(self):
        # Templates exist in WeRead's published reader JS; nonmember live validation is separate.
        cases = [
            ("试 读 结 束", "trial_ended"),
            ("试读结束 本书为付费会员卡可读 升级付费会员卡即可阅读", "membership_required"),
            ("试读结束 仅支持单本购买", "single_purchase_required"),
            ("你的免费体验卡已到期 送你一张体验卡 · 百万好书免费读", "reading_card_required"),
            ("付费会员卡19元/月 购买全书", "membership_or_purchase_required"),
            ("购买本书 余额不足 请充值", "purchase_required"),
        ]
        for prompt, reason in cases:
            with self.subTest(reason=reason):
                result = reader_issue({"buttons":["下一章"], "paywalls":prompt, "paywall_visible":True})
                self.assertEqual(result["code"], "ACCESS_LIMITED")
                self.assertEqual(result["access"]["reason"], reason)
                self.assertTrue(fallback_for(result)["fallback"]["requires_user_consent"])

    def test_footer_reading_card_gate_with_next_button(self):
        result = reader_issue({"buttons":["送你一张体验卡 · 百万好书免费读", "下一章"]})
        self.assertEqual(result["code"], "ACCESS_LIMITED")

    def test_normal_prose_and_membership_tips_are_not_gates(self):
        self.assertIsNone(reader_issue({"buttons":["下一章"],
            "text":"本章讨论试读结束、购买本书和会员权益。", "body":"开通会员 试读结束",
            "title":"验证码原理 - 安全验证 - 微信读书"}))
        self.assertIsNone(reader_issue({"buttons":["下一章"], "footer":"你正在使用会员卡阅读付费部分\n下一章"}))

    def test_verification_rate_and_login_are_not_membership_failures(self):
        for prompt, code in (("安全验证 请拖动滑块", "HUMAN_VERIFICATION"),
                             ("操作频繁，请稍后再试", "RATE_LIMITED"),
                             ("微信扫码登录后继续阅读", "WEB_LOGIN_REQUIRED")):
            result=reader_issue({"dialogs":prompt, "paywalls":"试读结束，升级付费会员卡即可阅读"})
            self.assertEqual(result["code"],code)
            self.assertEqual(fallback_for(result),{})

    def test_api_entitlement_failure_does_not_prompt_relogin(self):
        data={"error":{"name":"WeReadApiError", "status":403, "message":"没有阅读权限，请购买本书"}}
        self.assertEqual(runtime.classify_error(data),"ACCESS_LIMITED")
        self.assertEqual(runtime.classify_error({"error":{"status":403,"message":"Forbidden"}}),"ACCESS_DENIED")
        self.assertEqual(runtime.classify_error({"error":{"status":401,"message":"需要登录"}}),"LOGIN_REQUIRED")
        self.assertEqual(runtime.classify_error({"error":{"message":"验证阅读权限失败，没有阅读权限"}}),"ACCESS_LIMITED")
        self.assertEqual(runtime.classify_error({"error":{"errCode":-2010,"message":"unknown"}}),"UPSTREAM_ERROR")


@unittest.skipIf(export_runner is None, "Run with the skill's private Python to verify the export pipeline")
class ExportAccessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.book=self.root/"book"
        self.catalogue_patch=patch.object(catalogue,"DATA_HOME",self.root/"library")
        self.catalogue_patch.start()
        runtime.write_json(self.book/"api-info.json",{"bookId":"53","title":"权限验收样本","author":"测试", "isAutoPay":0})
        runtime.write_json(self.book/"api-catalogue.json",{"chapters":[
            {"chapterUid":1,"title":"免费章节","wordCount":40},
            {"chapterUid":2,"title":"后续章节","wordCount":40}]})
        self.args=argparse.Namespace(mode="export",account="nonmember",expected_vid="test",visible=False,
            directory=str(self.book),book="53",limit=0,interval=0,format=["epub"])
        self.reader=unittest.mock.Mock()
        self.reader.open=AsyncMock(); self.reader.close=AsyncMock(); self.reader.identity=AsyncMock(return_value="test")
        self.reader.session_source="omni"; self.reader.page=None
        self.reader.chapter=AsyncMock(side_effect=["这是已取得的完整免费章节。"*12,
            runtime.Failure("ACCESS_LIMITED","试读结束",access={"reason":"membership_required","trial_ended":True})])

    async def asyncTearDown(self):
        self.catalogue_patch.stop(); self.temp.cleanup()

    async def execute(self):
        with patch.object(export_runner,"Reader",return_value=self.reader),patch.object(export_runner,"emit"):
            return await export_runner.execute(self.args)

    async def test_gate_keeps_partial_book_without_checkpoint_or_fake_full_text(self):
        result=await self.execute()
        self.assertFalse(result["ok"])
        self.assertEqual(result["manifest"]["status"],"partial")
        self.assertEqual(result["manifest"]["captured_chapters"],1)
        self.assertEqual(result["manifest"]["missing_chapters"],1)
        self.assertTrue(result["manifest"]["artifacts"]["epub"].endswith("book.partial.epub"))
        self.assertEqual(corpus.validate_epub(result["manifest"]["artifacts"]["epub"])["spine_chapters"],1)
        self.assertNotIn("2",runtime.read_json(self.book/"checkpoints.json"))
        self.assertFalse((self.book/"chapters/0002-2.md").exists())
        self.assertEqual(self.reader.chapter.await_count,2)
        self.reader.close.assert_awaited_once()
        rebuilt=corpus.build(self.book)
        self.assertEqual(rebuilt["last_attempt"]["error"]["chapter_id"],"2")
        self.assertEqual(rebuilt["last_attempt"]["error"]["access"]["reason"],"membership_required")
        self.assertFalse(corpus.cache_status(self.book)["usable"])
        self.assertTrue(result["fallback"]["requires_user_consent"])

    async def test_auto_purchase_stops_before_loading_a_chapter(self):
        info=runtime.read_json(self.book/"api-info.json"); info["isAutoPay"]=1
        runtime.write_json(self.book/"api-info.json",info)
        result=await self.execute()
        self.assertEqual(result["error"]["code"],"AUTO_PURCHASE_ENABLED")
        self.reader.chapter.assert_not_awaited()
        self.assertEqual(result["manifest"]["captured_chapters"],0)

    async def test_rejected_session_does_not_fall_through_to_entitlement_checks(self):
        self.reader.identity.return_value=None
        result=await self.execute()
        self.assertEqual(result["error"]["code"],"WEB_LOGIN_REQUIRED")
        self.reader.chapter.assert_not_awaited()
        self.assertNotIn("fallback",result)

    async def test_delisted_flag_does_not_override_accessible_owned_content(self):
        info=runtime.read_json(self.book/"api-info.json"); info.update(soldout=1,paid=1)
        runtime.write_json(self.book/"api-info.json",info)
        self.reader.chapter.side_effect=None; self.reader.chapter.return_value="已购买用户仍可正常读取的正文。"*12
        result=await self.execute()
        self.assertTrue(result["ok"])
        self.assertEqual(result["manifest"]["status"],"catalogue_complete")

    async def test_visible_gate_prevents_completing_even_long_preview(self):
        with patch.object(export_runner,"DATA_HOME",self.root):
            reader=export_runner.Reader("test")
        reader.page=unittest.mock.Mock()
        reader.page.goto=AsyncMock(); reader.page.evaluate=AsyncMock()
        reader.page.url="https://weread.qq.com/web/reader/"+reader_hash("53")+"k"+reader_hash("2")
        reader.screen=AsyncMock(return_value={**ANONYMOUS_PREVIEW,"text":"看似完整但未获得权限的试读文本"*1000,"ready":True})
        with patch.object(asyncio,"sleep",new=AsyncMock()):
            with self.assertRaises(runtime.Failure) as raised:
                await reader.chapter(reader_hash("53"),"2")
        self.assertEqual(raised.exception.code,"WEB_LOGIN_REQUIRED")
        reader.page.evaluate.assert_not_awaited()

    async def test_horizontal_spread_is_discarded_before_reading_one_chapter(self):
        with patch.object(export_runner,"DATA_HOME",self.root):
            reader=export_runner.Reader("test")
        reader.page=unittest.mock.Mock(); reader.page.goto=AsyncMock(); reader.page.evaluate=AsyncMock()
        reader.page.url="https://weread.qq.com/web/reader/"+reader_hash("53")+"k"+reader_hash("2")
        spread={"horizontal_reader":True,"text":"两章混在同一跨页中","ready":True,"buttons":[]}
        chapter={"horizontal_reader":False,"text":"只有所请求章节的正文","ready":True,"buttons":["下一章"]}
        reader.screen=AsyncMock(side_effect=[spread]+[chapter]*6)
        reader.use_scroll_reader=AsyncMock()
        with patch.object(asyncio,"sleep",new=AsyncMock()):
            text=await reader.chapter(reader_hash("53"),"2")
        reader.use_scroll_reader.assert_awaited_once()
        self.assertEqual(text,chapter["text"])


if __name__=="__main__":
    unittest.main()
