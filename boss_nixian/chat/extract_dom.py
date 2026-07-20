"""
BOSS 直聘聊天页 DOM 结构提取脚本（Playwright 直接启动，不依赖 CDP）

使用方式：
1. 关闭所有 Chrome 窗口
2. 运行：.\\.venv\\Scripts\\python.exe boss_nixian/chat/extract_dom.py
3. 脚本会打开 Chromium 并导航到 BOSS 聊天页
4. 你登录后按回车，脚本提取 DOM 数据
5. 结果保存到 boss_nixian/chat/dom_result.json
"""

import json
import os
import time
from playwright.sync_api import sync_playwright

OUTPUT_FILE = "boss_nixian/chat/dom_result.json"

EXTRACT_JS = """
() => {
    const data = {};
    data.url = location.href;

    // 所有 class 名称
    const allClasses = new Set();
    document.querySelectorAll('*').forEach((el) => {
        if (typeof el.className === 'string') {
            el.className.split(/\\s+/).forEach((cls) => {
                if (cls && /chat|conv|msg|message|input|send|friend|talk|editor|footer|list|item|name|text|time|wrap|content|header|body|panel|tab|btn|button|icon|img|avatar|figure|select|active|hover|label|search/i.test(cls)) {
                    allClasses.add(cls);
                }
            });
        }
    });
    data.chatClasses = [...allClasses].sort();

    // 对话列表容器
    data.conversationList = {};
    for (const sel of ['.user-list-content', '.chat-content', '.list-warp', '[role="group"]']) {
        const el = document.querySelector(sel);
        if (el) data.conversationList[sel] = { found: true, tagName: el.tagName, className: el.className, childCount: el.children.length, innerHTML: el.innerHTML.substring(0, 500) };
    }

    // 对话项
    data.conversationItems = {};
    for (const sel of ['.friend-content', 'li[role="listitem"]', '[class*="chat-item"]']) {
        const els = document.querySelectorAll(sel);
        if (els.length > 0) {
            const first = els[0];
            data.conversationItems[sel] = {
                found: true, count: els.length, tagName: first.tagName,
                className: first.className, parentClassName: first.parentElement?.className,
                innerHTML: first.innerHTML.substring(0, 800),
                childClasses: Array.from(first.querySelectorAll('*')).map((el) => ({
                    tag: el.tagName, class: el.className,
                    text: el.textContent?.trim().substring(0, 50),
                })).filter((c) => c.class),
            };
        }
    }

    // 消息区域
    data.messageArea = {};
    for (const sel of ['.conversation-message', '.message-item', '.chat-conversation', '[class*="message-content"]']) {
        const el = document.querySelector(sel);
        if (el) data.messageArea[sel] = { found: true, tagName: el.tagName, className: el.className, childCount: el.children.length, innerHTML: el.innerHTML.substring(0, 500) };
    }

    // 输入框
    data.inputArea = {};
    for (const sel of ['.input-wrap .input', '[contenteditable]', 'textarea', '.input-wrap']) {
        const el = document.querySelector(sel);
        if (el) data.inputArea[sel] = { found: true, tagName: el.tagName, className: el.className, contentEditable: el.getAttribute('contenteditable'), outerHTML: el.outerHTML.substring(0, 300) };
    }

    // 发送按钮
    data.sendButton = {};
    for (const sel of ['.send', 'button[type="send"]', 'button']) {
        const el = document.querySelector(sel);
        if (el) data.sendButton[sel] = { found: true, tagName: el.tagName, className: el.className, text: el.textContent?.trim().substring(0, 30), outerHTML: el.outerHTML.substring(0, 300) };
    }

    // 关键 HTML 片段
    const firstItem = document.querySelector('.friend-content') || document.querySelector('li[role="listitem"]');
    if (firstItem) data.firstConversationItemHTML = firstItem.outerHTML.substring(0, 2000);

    const msgContainer = document.querySelector('.conversation-message') || document.querySelector('.chat-conversation');
    if (msgContainer) data.messageAreaHTML = msgContainer.innerHTML.substring(0, 2000);

    const inputContainer = document.querySelector('.input-wrap') || document.querySelector('[class*="footer"]');
    if (inputContainer) data.inputAreaHTML = inputContainer.outerHTML.substring(0, 1000);

    return data;
}
"""


def main():
    # Chrome 用户数据目录（保留登录态）
    chrome_user_data = os.path.expanduser(r"~\AppData\Local\Google\Chrome\User Data")

    print(f"Chrome 用户数据目录: {chrome_user_data}")
    print("启动 Chromium（使用 Chrome 用户数据保留登录态）...")

    with sync_playwright() as p:
        # 用 persistent context 启动，复用 Chrome 的登录态
        context = p.chromium.launch_persistent_context(
            user_data_dir=chrome_user_data,
            channel="chrome",  # 使用已安装的 Chrome
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
            ],
        )

        page = context.pages[0] if context.pages else context.new_page()

        # 导航到 BOSS 聊天页
        print("导航到 BOSS 聊天页...")
        page.goto("https://www.zhipin.com/web/geek/chat", wait_until="domcontentloaded", timeout=30000)
        time.sleep(3)

        # 检查是否需要登录
        current_url = page.url
        print(f"当前 URL: {current_url}")

        if "login" in current_url.lower() or "passport" in current_url.lower():
            print("\n需要登录！请在浏览器中登录 BOSS 直聘")
            input("登录完成后按回车继续...")
            time.sleep(2)

        # 确保在聊天页
        if "chat" not in page.url:
            print("不在聊天页，重新导航...")
            page.goto("https://www.zhipin.com/web/geek/chat", wait_until="domcontentloaded", timeout=30000)
            time.sleep(3)

        print(f"当前页面: {page.url}")

        # 等待页面加载
        print("等待页面加载...")
        time.sleep(5)

        # 提取 DOM 数据
        print("提取 DOM 数据...")
        result = page.evaluate(EXTRACT_JS)

        # 保存结果
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"\n结果已保存到 {OUTPUT_FILE}")

        # 打印摘要
        print("\n=== 诊断摘要 ===")
        print(f"URL: {result['url']}")
        print(f"页面 class 数量: {len(result['chatClasses'])}")
        print("\n对话列表容器:")
        for sel, info in result["conversationList"].items():
            mark = "✓" if info["found"] else "✗"
            print(f"  {mark} {sel} → {info['tagName']}.{info['className']}")
        print("\n对话项:")
        for sel, info in result["conversationItems"].items():
            print(f"  ✓ {sel} ({info['count']}个) → {info['tagName']}.{info['className']}")
            for child in info.get("childClasses", [])[:10]:
                text = child['text'][:30] if child['text'] else ''
                print(f"      <{child['tag']} class=\"{child['class']}\"> {text}")
        print("\n消息区域:")
        for sel, info in result["messageArea"].items():
            print(f"  ✓ {sel} → {info['tagName']}.{info['className']}")
        print("\n输入框:")
        for sel, info in result["inputArea"].items():
            print(f"  ✓ {sel} → {info['tagName']} contenteditable={info['contentEditable']}")
        print("\n发送按钮:")
        for sel, info in result["sendButton"].items():
            print(f"  ✓ {sel} → {info['tagName']} text=\"{info['text']}\"")
        print("\n页面 chat 相关 class:")
        print("\n".join(result['chatClasses']))

        context.close()

    print("\n完成！")


if __name__ == "__main__":
    main()
