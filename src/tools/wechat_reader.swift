// macOS 微信只读适配器：精确会话校验，读取已加载消息，不进入输入区或发送消息。
import AppKit
import ApplicationServices
import Foundation

var responsePath: String?
var cancelPath: String?
let deadline = Date().addingTimeInterval(10)

/// 输出有界 JSON 后退出；不将整个辅助功能树或其他会话写入诊断。
func finish(_ value: [String: Any], code: Int32 = 0) -> Never {
    let data = try! JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
    if let path = responsePath {
        do { try data.write(to: URL(fileURLWithPath: path), options: [.atomic]) }
        catch { exit(3) }
    } else {
        FileHandle.standardOutput.write(data)
    }
    exit(code)
}

/// 读取单个 AX 属性；不可访问的属性不触发权限申请或替代操作。
func attribute(_ element: AXUIElement, _ name: String) -> CFTypeRef? {
    if Date() > deadline || (cancelPath != nil && FileManager.default.fileExists(atPath: cancelPath!)) {
        finish(["error": "微信读取已取消或超时"], code: 2)
    }
    var value: CFTypeRef?
    return AXUIElementCopyAttributeValue(element, name as CFString, &value) == .success ? value : nil
}

/// 读取文字属性，避免把不受支持对象的描述当作消息内容。
func string(_ element: AXUIElement, _ name: String) -> String {
    return attribute(element, name) as? String ?? ""
}

/// 有界遍历节点，只检查标识符；寻找目标时不收集其他会话的内容。
func find(_ root: AXUIElement, id: String) -> AXUIElement? {
    var queue: [(AXUIElement, Int)] = [(root, 0)]
    var count = 0
    while !queue.isEmpty && count < 2000 {
        let (element, depth) = queue.removeFirst()
        count += 1
        if string(element, kAXIdentifierAttribute) == id { return element }
        if depth < 18, let children = attribute(element, kAXChildrenAttribute) as? [AXUIElement] {
            queue += children.prefix(500).map { ($0, depth + 1) }
        }
    }
    return nil
}

/// 精确会话条目才允许点击；点击前再次核对身份与前台进程，不发送键盘事件。
func selectSession(_ element: AXUIElement, chat: String, app: NSRunningApplication) -> Bool {
    guard string(element, kAXIdentifierAttribute) == "session_item_" + chat else { return false }
    app.activate(options: [])
    Thread.sleep(forTimeInterval: 0.25)
    guard NSWorkspace.shared.frontmostApplication?.processIdentifier == app.processIdentifier else { return false }
    if AXUIElementPerformAction(element, kAXPressAction as CFString) == .success {
        Thread.sleep(forTimeInterval: 0.15)
        let root = AXUIElementCreateApplication(app.processIdentifier)
        if let title = find(root, id: "current_chat_name_label"), string(title, kAXValueAttribute) == chat { return true }
    }
    guard let p = attribute(element, kAXPositionAttribute), let s = attribute(element, kAXSizeAttribute),
          CFGetTypeID(p) == AXValueGetTypeID(), CFGetTypeID(s) == AXValueGetTypeID() else { return false }
    var point = CGPoint.zero
    var size = CGSize.zero
    guard AXValueGetValue(p as! AXValue, .cgPoint, &point), AXValueGetValue(s as! AXValue, .cgSize, &size),
          size.width > 0, size.height > 0 else { return false }
    point.x += min(size.width / 2, 80)
    point.y += size.height / 2
    guard string(element, kAXIdentifierAttribute) == "session_item_" + chat,
          NSWorkspace.shared.frontmostApplication?.processIdentifier == app.processIdentifier else { return false }
    CGEvent(mouseEventSource: nil, mouseType: .leftMouseDown, mouseCursorPosition: point, mouseButton: .left)?.post(tap: .cghidEventTap)
    CGEvent(mouseEventSource: nil, mouseType: .leftMouseUp, mouseCursorPosition: point, mouseButton: .left)?.post(tap: .cghidEventTap)
    return true
}

/// 只读消息列表中的静态文本，保留原顺序、重复消息和媒体占位，不读草稿。
func messages(_ list: AXUIElement) -> [[String: String]] {
    var result: [[String: String]] = []
    var queue: [AXUIElement] = [list]
    var visited = 0
    while !queue.isEmpty && visited < 1000 && result.count < 200 {
        let element = queue.removeFirst()
        visited += 1
        let identifier = string(element, kAXIdentifierAttribute)
        let role = string(element, kAXRoleAttribute)
        if identifier == "chat_input_field" { continue }
        if identifier == "chat_bubble_item_view" || role == kAXStaticTextRole {
            let value = string(element, kAXValueAttribute)
            let title = value.isEmpty ? string(element, kAXTitleAttribute) : value
            let text = title.isEmpty ? string(element, kAXDescriptionAttribute) : title
            if !text.isEmpty {
                result.append(["kind": identifier == "chat_bubble_item_view" ? "message" : "label", "text": String(text.prefix(2000))])
                continue
            }
        }
        if let children = attribute(element, kAXChildrenAttribute) as? [AXUIElement] {
            queue.insert(contentsOf: children.prefix(500), at: 0)
        }
    }
    return result
}

/// 只向经两次身份核对的消息区域向上滚动，不点击输入框或发送按键。
func olderPage(_ list: AXUIElement, root: AXUIElement, chat: String, app: NSRunningApplication) -> Bool {
    guard let before = find(root, id: "current_chat_name_label"), string(before, kAXValueAttribute) == chat,
          string(list, kAXIdentifierAttribute) == "chat_message_list" else { return false }
    app.activate(options: [])
    Thread.sleep(forTimeInterval: 0.15)
    guard NSWorkspace.shared.frontmostApplication?.processIdentifier == app.processIdentifier,
          let p = attribute(list, kAXPositionAttribute), let s = attribute(list, kAXSizeAttribute),
          CFGetTypeID(p) == AXValueGetTypeID(), CFGetTypeID(s) == AXValueGetTypeID() else { return false }
    var point = CGPoint.zero
    var size = CGSize.zero
    guard AXValueGetValue(p as! AXValue, .cgPoint, &point), AXValueGetValue(s as! AXValue, .cgSize, &size),
          size.width > 80, size.height > 80 else { return false }
    point.x += size.width / 2
    point.y += size.height / 2
    guard let after = find(root, id: "current_chat_name_label"), string(after, kAXValueAttribute) == chat,
          NSWorkspace.shared.frontmostApplication?.processIdentifier == app.processIdentifier else { return false }
    CGEvent(mouseEventSource: nil, mouseType: .mouseMoved, mouseCursorPosition: point, mouseButton: .left)?.post(tap: .cghidEventTap)
    guard let check = find(root, id: "current_chat_name_label"), string(check, kAXValueAttribute) == chat,
          NSWorkspace.shared.frontmostApplication?.processIdentifier == app.processIdentifier else { return false }
    CGEvent(scrollWheelEvent2Source: nil, units: .pixel, wheelCount: 1,
            wheel1: Int32(min(size.height * 0.6, 500)), wheel2: 0, wheel3: 0)?.post(tap: .cghidEventTap)
    Thread.sleep(forTimeInterval: 0.3)
    return true
}

/// 只接受唯一连续重叠，重复消息不靠全局去重丢弃；无法关联时停止扩展范围。
func mergePage(_ earlier: [[String: String]], _ current: [[String: String]]) -> [[String: String]]? {
    if earlier == current { return current }
    let maximum = min(earlier.count, current.count)
    guard maximum >= 2 else { return nil }
    for length in stride(from: maximum, through: 2, by: -1) {
        if Array(earlier.suffix(length)) == Array(current.prefix(length)) {
            let overlap = Array(current.prefix(length))
            var occurrences = 0
            for start in 0...(earlier.count - length) {
                if Array(earlier[start..<(start + length)]) == overlap { occurrences += 1 }
            }
            if occurrences == 1 { return Array(earlier.dropLast(length)) + current }
        }
    }
    return nil
}

/// 检查能力或读取一个明确授权会话；未匹配标题时零消息输出。
func main() {
    var args = Array(CommandLine.arguments.dropFirst())
    if args.count >= 3 && args[0] == "--output" {
        responsePath = args[1]
        cancelPath = args[1] + ".cancel"
        args.removeFirst(2)
    }
    let apps = NSRunningApplication.runningApplications(withBundleIdentifier: "com.tencent.xinWeChat")
    if args == ["--check"] {
        finish(["platform": "macos", "accessibility": AXIsProcessTrusted(), "running": !apps.isEmpty])
    }
    guard ((args.count == 2 && args[0] == "--read") || (args.count == 3 && args[0] == "--read-pages")), args[1].count >= 2, args[1].count <= 120 else {
        finish(["error": "须指定完整会话名"], code: 2)
    }
    let pageLimit = args.count == 3 ? (Int(args[2]) ?? 0) : 1
    guard (1...6).contains(pageLimit) else { finish(["error": "分页须为1至6页"], code: 2) }
    guard AXIsProcessTrusted() else {
        finish(["error": "需要给 JACWeChatReader.app 授予 macOS 辅助功能权限"], code: 2)
    }
    guard apps.count == 1 else { finish(["error": "请先打开并登录微信"], code: 2) }
    let app = apps[0], chat = args[1]
    let root = AXUIElementCreateApplication(app.processIdentifier)
    AXUIElementSetMessagingTimeout(root, 1)
    var title = find(root, id: "current_chat_name_label")
    if title == nil || string(title!, kAXValueAttribute) != chat {
        guard let item = find(root, id: "session_item_" + chat), selectSession(item, chat: chat, app: app) else {
            finish(["error": "请先在微信中打开完整名称为「\(chat)」的会话；当前列表未找到精确目标"], code: 2)
        }
        for _ in 0..<12 {
            Thread.sleep(forTimeInterval: 0.1)
            title = find(root, id: "current_chat_name_label")
            if title != nil && string(title!, kAXValueAttribute) == chat { break }
        }
    }
    guard let before = title, string(before, kAXValueAttribute) == chat,
          let list = find(root, id: "chat_message_list") else {
        finish(["error": "微信会话标题或消息列表无法确认，本次未读取"], code: 2)
    }
    var result = messages(list)
    var pagesRead = 1
    var stopReason = "page_limit"
    if pageLimit > 1 {
        for _ in 1..<pageLimit {
            guard olderPage(list, root: root, chat: chat, app: app),
                  let checked = find(root, id: "current_chat_name_label"), string(checked, kAXValueAttribute) == chat,
                  let updated = find(root, id: "chat_message_list") else {
                finish(["error": "分页时会话或消息区域无法核验，结果已丢弃"], code: 2)
            }
            let page = messages(updated)
            if page == result { stopReason = "no_change"; break }
            guard let merged = mergePage(page, result) else { stopReason = "ambiguous_overlap"; break }
            if merged == result { stopReason = "no_change"; break }
            if merged.count > 1000 { stopReason = "message_limit"; break }
            result = merged
            pagesRead += 1
        }
    }
    guard let after = find(root, id: "current_chat_name_label"), string(after, kAXValueAttribute) == chat else {
        finish(["error": "读取时会话已切换，本次结果已丢弃"], code: 2)
    }
    guard !result.isEmpty else { finish(["error": "指定会话没有可读取的消息文字"], code: 2) }
    finish(["chat": chat, "messages": result, "scope": "当前微信已加载消息，非完整历史；媒体和附件仅为占位，发送者可能不可见", "complete_history": false, "pages_read": pagesRead, "stop_reason": stopReason])
}

main()
