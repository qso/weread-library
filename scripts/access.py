"""Classify access gates from reader UI or explicit API errors, never from book prose."""
from __future__ import annotations

import re


def compact(text):
    return re.sub(r"\s+", "", str(text or ""))


def entitlement(text, *, gate=False):
    text = compact(text)
    trial = bool(re.search(r"试读(?:已)?结束|试读到此结束|免费(?:体验卡|阅读).*(?:到期|用完)|体验卡已到期", text))
    single = "仅支持单本购买" in text or "需单独购买" in text or "不支持会员" in text
    purchase = single or bool(re.search(r"购买(?:本书|全书|章节|后|即可)|单本购买|付费后阅读|余额不足|充值|(?:书币|元)购买", text))
    member = bool(re.search(r"(?:开通|升级|续费|购买|激活).{0,16}(?:会员|无限卡)|本书为.{0,12}会员卡可读|(?:付费)?会员卡.{0,20}(?:元/月|元/年|开通后即可阅读)", text))
    experience = bool(re.search(r"(?:送你一张|领取|激活).{0,12}体验卡|免费体验卡(?:可读|已到期)", text))
    denied = bool(re.search(r"(?:无|没有|缺少|不足|不具备).{0,8}(?:阅读权限|阅读权益)|(?:阅读权限|阅读权益).{0,8}(?:不足|失效)|not.entitled|insufficient.entitlement|permission.denied.*read", text, re.I))
    if not (trial or purchase or member or experience or denied or gate):
        return None
    options = (["purchase"] if single else
               (["membership"] if member else []) + (["purchase"] if purchase else []) +
               (["trial_card"] if experience else []))
    if single:
        reason, message = "single_purchase_required", "此书仅支持单本购买，会员身份不能替代购买权限。"
    elif member and purchase:
        reason, message = "membership_or_purchase_required", "当前账号需要相应会员权益或购买本书才能继续。"
    elif member:
        reason, message = "membership_required", "当前账号缺少此书所需的会员权益。"
    elif purchase:
        reason, message = "purchase_required", "当前章节要求购买或可用书币，已停止自动读取。"
    elif experience:
        reason, message = "reading_card_required", "当前阅读权益不足，页面提示体验卡；未自动领取或激活。"
    elif trial:
        reason, message = "trial_ended", "已到试读边界，未把试读内容认作完整章节。"
    else:
        reason, message = "entitlement_required", "阅读被权限提示拦截，页面未明确说明所缺权益。"
    return {"code": "ACCESS_LIMITED", "message": message,
            "access": {"reason": reason, "trial_ended": trial, "available_options": options}}


def reader_issue(state):
    """Only visible, dedicated UI controls are eligible evidence. Ignore body/canvas text."""
    buttons = [compact(x) for x in state.get("buttons", [])]
    footer = compact(state.get("footer", ""))
    dialogs = compact(state.get("dialogs", ""))
    walls = compact(state.get("paywalls", ""))
    ui = " ".join(buttons) + footer + dialogs + walls
    # Book titles may themselves discuss CAPTCHA or access control.
    title = compact(state.get("title", ""))
    security = ui + (title if re.fullmatch(r"(?:腾讯)?(?:安全验证|人机验证|验证码|访问验证)", title) else "")
    if re.search(r"安全验证|拖动滑块|验证码|人机验证", security):
        return {"code":"HUMAN_VERIFICATION", "message":"网页要求人工验证，已停止自动请求。"}
    if re.search(r"访问过于频繁|操作频繁|请求过于频繁|请求频率|稍后再试.*频繁", security):
        return {"code":"RATE_LIMITED", "message":"微信读书限制了请求频率，已停止自动重试。"}
    if any("登录" in b for b in buttons) or re.search(r"(?:扫码|微信|重新|请先|立即)登录|登录后(?:继续)?阅读", dialogs + walls):
        return {"code":"WEB_LOGIN_REQUIRED", "message":"阅读器要求登录；先检查 Omni 会话复用，不能把登录提示当作会员不足。"}
    if re.search(r"(?:下架|版权).{0,16}(?:无法|不能|不支持|暂不提供)阅读|本书(?:已下架|暂不可读)|书籍不存在", ui):
        return {"code":"UNAVAILABLE", "message":"页面明确提示此书当前不可阅读，已保留此前章节。"}
    # A paywall / explicit trial ending overrides even a stale “下一章” button.
    gate = bool(state.get("paywall_visible") or state.get("trial_end_visible"))
    result = entitlement(walls + dialogs + footer, gate=gate)
    if result and (gate or result["access"]["trial_ended"] or dialogs):
        result["access"]["source"] = "visible_reader_ui"
        return result
    # Live preview pages can retain “下一章”; navigation alone never overrides a gate.
    result = entitlement(" ".join(buttons) + footer)
    if result:
        result["access"]["source"] = "visible_reader_ui"
        return result
    navigation = any(re.fullmatch(r"下一页|下一章|全书完|已读完|阅读完成", b) for b in buttons)
    if not navigation:
        if re.search(r"(?:暂不支持|无法).{0,12}(?:网页版|网页端)|(?:去|请在|打开)微信读书App(?:继续)?阅读", ui, re.I):
            return {"code":"UNSUPPORTED_READER", "message":"页面要求在官方 App 阅读，当前网页无法提供可核验的全文。"}
    return None


def fallback_for(error):
    if error.get("code") not in ("ACCESS_LIMITED", "UNAVAILABLE", "NO_TEXT", "CATALOGUE_UNAVAILABLE", "UNSUPPORTED_READER"):
        return {}
    return {"next_action":"ask_web_fallback", "fallback":{
        "requires_user_consent":True, "reason":error["code"],
        "question":"微信读书目前无法提供这本书的可读原文，要尝试在网上寻找 PDF 或 EPUB 版本吗？"}}
