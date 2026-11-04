// J.A.C. 独立桌面组件：应用/窗口/控件快照核验后只执行一次动作。
import AppKit
import ApplicationServices
import CryptoKit
import ScreenCaptureKit

var outputPath = ""
let deadline = Date().addingTimeInterval(12)
var pressedPoint: CGPoint?
var dispatched = false

/// 输出一次有界结果后退出，避免后台遗留自动化进程。
func finish(_ value: [String: Any], code: Int32 = 0) -> Never {
    if let point = pressedPoint {
        CGEvent(mouseEventSource: nil, mouseType: .leftMouseUp, mouseCursorPosition: point, mouseButton: .left)?.post(tap: .cghidEventTap)
    }
    var response = value
    if value["error"] != nil { response["dispatched"] = dispatched }
    let data = (try? JSONSerialization.data(withJSONObject: response, options: [.sortedKeys])) ?? Data("{}".utf8)
    if outputPath.isEmpty { FileHandle.standardOutput.write(data) }
    else { try? data.write(to: URL(fileURLWithPath: outputPath), options: .atomic) }
    exit(code)
}

/// 每个事件前检查取消标记、总时限和目标应用前台归属。
func check(_ app: NSRunningApplication? = nil) {
    if Date() > deadline || (!outputPath.isEmpty && FileManager.default.fileExists(atPath: outputPath + ".cancel")) {
        finish(["error": "操作已取消或超时"], code: 2)
    }
    if let app = app, NSWorkspace.shared.frontmostApplication?.processIdentifier != app.processIdentifier {
        finish(["error": "前台应用已改变，未继续操作"], code: 2)
    }
}

/// 读取辅助功能属性，缺失字段保持为空而不猜测控件身份。
func attribute(_ node: AXUIElement, _ key: String) -> CFTypeRef? {
    check()
    var value: CFTypeRef?
    return AXUIElementCopyAttributeValue(node, key as CFString, &value) == .success ? value : nil
}

/// 将可访问字符串裁剪为有界文本。
func string(_ node: AXUIElement, _ key: String) -> String {
    return String((attribute(node, key) as? String ?? "").prefix(200))
}

/// 提取控件实际屏幕矩形，避免用模型猜测的任意坐标发事件。
func rectangle(_ node: AXUIElement) -> CGRect? {
    guard let rawPoint = attribute(node, kAXPositionAttribute), let rawSize = attribute(node, kAXSizeAttribute),
          CFGetTypeID(rawPoint) == AXValueGetTypeID(), CFGetTypeID(rawSize) == AXValueGetTypeID() else { return nil }
    var point = CGPoint.zero, size = CGSize.zero
    guard AXValueGetValue(rawPoint as! AXValue, .cgPoint, &point),
          AXValueGetValue(rawSize as! AXValue, .cgSize, &size), size.width > 0, size.height > 0 else { return nil }
    return CGRect(origin: point, size: size)
}

/// 枚举当前窗口的有限控件，同时保留程序内部AX句柄和外部编号。
func observe(_ app: NSRunningApplication) -> ([String: Any], [AXUIElement]) {
    check(app)
    let root = AXUIElementCreateApplication(app.processIdentifier)
    AXUIElementSetMessagingTimeout(root, 0.5)
    guard let window = attribute(root, kAXFocusedWindowAttribute) else {
        finish(["error": "当前应用没有可核验的前台窗口"], code: 2)
    }
    let windowElement = window as! AXUIElement
    var queue: [(AXUIElement, Int)] = [(windowElement, 0)], nodes: [AXUIElement] = [], items: [[String: Any]] = []
    var visited = 0
    while !queue.isEmpty && visited < 350 && items.count < 100 {
        check(app)
        let (node, depth) = queue.removeFirst()
        visited += 1
        let role = string(node, kAXRoleAttribute), subrole = string(node, kAXSubroleAttribute)
        if let rect = rectangle(node), !role.isEmpty, role != "AXGroup" {
            let secure = subrole == kAXSecureTextFieldSubrole
            items.append(["target": String(nodes.count), "role": role, "subrole": subrole,
                          "name": string(node, kAXTitleAttribute), "description": string(node, kAXDescriptionAttribute),
                          "value": secure ? "" : string(node, kAXValueAttribute), "secure": secure,
                          "rect": [rect.origin.x, rect.origin.y, rect.size.width, rect.size.height]])
            nodes.append(node)
        }
        if depth < 12, let children = attribute(node, kAXChildrenAttribute) as? [AXUIElement] {
            queue += children.prefix(100).map { ($0, depth + 1) }
        }
    }
    var state: [String: Any] = ["app": app.bundleIdentifier ?? "", "pid": app.processIdentifier,
                              "title": string(windowElement, kAXTitleAttribute), "elements": items]
    let encoded = try! JSONSerialization.data(withJSONObject: state, options: [.sortedKeys])
    state["snapshot"] = SHA256.hash(data: encoded).map { String(format: "%02x", $0) }.joined()
    return (state, nodes)
}

/// 在已核验控件内部发送一对鼠标事件，发出前再次检查前台与取消。
func click(_ node: AXUIElement, app: NSRunningApplication, double: Bool = false) {
    guard let rect = rectangle(node) else { finish(["error": "目标没有可点击区域"], code: 2) }
    let point = CGPoint(x: rect.midX, y: rect.midY)
    for index in 1...(double ? 2 : 1) {
        check(app)
        for type in [CGEventType.leftMouseDown, CGEventType.leftMouseUp] {
            guard let event = CGEvent(mouseEventSource: nil, mouseType: type, mouseCursorPosition: point, mouseButton: .left) else {
                finish(["error": "鼠标事件创建失败"], code: 2)
            }
            event.setIntegerValueField(.mouseEventClickState, value: Int64(index))
            dispatched = true
            event.post(tap: .cghidEventTap)
        }
    }
}

/// 输入文字优先使用AXValue，并在回读确认失败时明确报错。
func fill(_ node: AXUIElement, text: String, app: NSRunningApplication) {
    check(app)
    guard text.utf16.count <= 4000, !text.contains("\0"), string(node, kAXSubroleAttribute) != kAXSecureTextFieldSubrole else {
        finish(["error": "输入超限或目标为密码控件"], code: 2)
    }
    dispatched = true
    let result = AXUIElementSetAttributeValue(node, kAXValueAttribute as CFString, text as CFString)
    guard result == .success else { finish(["error": "该控件不支持可靠文字设置，请使用可访问的输入框"], code: 2) }
    Thread.sleep(forTimeInterval: 0.1)
    check(app)
    guard attribute(node, kAXValueAttribute) as? String == text else {
        finish(["error": "文字输入后回读不一致，未确认完成"], code: 2)
    }
}

/// 发送固定的导航按键；不开放任意终端命令或系统快捷键。
func press(_ node: AXUIElement, key: String, app: NSRunningApplication) {
    let keys: [String: CGKeyCode] = ["Tab": 48, "Enter": 36, "Escape": 53, "ArrowLeft": 123,
                                    "ArrowRight": 124, "ArrowDown": 125, "ArrowUp": 126]
    guard let code = keys[key] else { finish(["error": "未开放该快捷键"], code: 2) }
    guard AXUIElementSetAttributeValue(node, kAXFocusedAttribute as CFString, kCFBooleanTrue) == .success else {
        finish(["error": "无法确认键盘焦点"], code: 2)
    }
    check(app)
    dispatched = true
    CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: true)?.post(tap: .cghidEventTap)
    CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: false)?.post(tap: .cghidEventTap)
}

/// 只捕获精确前台窗口，拒绝同名歧义与未授予的屏幕权限。
func capture(_ app: NSRunningApplication, state: [String: Any]) -> [String: Any] {
    guard CGPreflightScreenCaptureAccess() else {
        finish(["error": "请给 JACDesktopController.app 授予屏幕录制权限"], code: 2)
    }
    let ready = DispatchSemaphore(value: 0)
    var content: SCShareableContent?
    SCShareableContent.getExcludingDesktopWindows(true, onScreenWindowsOnly: true) { result, _ in
        content = result
        ready.signal()
    }
    guard ready.wait(timeout: .now()+3) == .success, let content = content else {
        finish(["error": "无法获取可捕获窗口"], code: 2)
    }
    let windows = content.windows.filter { $0.owningApplication?.processID == app.processIdentifier && $0.title == state["title"] as? String }
    guard windows.count == 1 else { finish(["error": "截图窗口身份有歧义"], code: 2) }
    let window = windows[0], config = SCStreamConfiguration()
    let scale = min(1, 1200 / window.frame.width, 900 / window.frame.height)
    config.width = Int(window.frame.width*scale)
    config.height = Int(window.frame.height*scale)
    config.showsCursor = false
    config.ignoreShadowsSingleWindow = true
    let done = DispatchSemaphore(value: 0)
    var image: CGImage?
    SCScreenshotManager.captureImage(contentFilter: SCContentFilter(desktopIndependentWindow: window), configuration: config) { result, _ in
        image = result
        done.signal()
    }
    guard done.wait(timeout: .now()+3) == .success, let image = image,
          let data = NSBitmapImageRep(cgImage: image).representation(using: .jpeg, properties: [.compressionFactor: 0.45]), data.count <= 90000 else {
        finish(["error": "截图失败或超过90KB预算"], code: 2)
    }
    check(app)
    guard observe(app).0["snapshot"] as? String == state["snapshot"] as? String else {
        finish(["error": "截图期间窗口已改变"], code: 2)
    }
    return ["snapshot": state["snapshot"]!, "image": data.base64EncodedString(), "width": image.width,
            "height": image.height, "frame": [window.frame.minX, window.frame.minY, window.frame.width, window.frame.height]]
}

/// 解析单次请求并执行；结果必须来自执行后的最新观察。
func main() {
    let args = Array(CommandLine.arguments.dropFirst())
    if args == ["--check"] {
        finish(["platform": "macos", "accessibility": AXIsProcessTrusted(), "screen_recording": CGPreflightScreenCaptureAccess()])
    }
    guard args.count == 4, args[0] == "--request", args[2] == "--output" else { finish(["error": "参数无效"], code: 2) }
    outputPath = args[3]
    guard let data = try? Data(contentsOf: URL(fileURLWithPath: args[1])), data.count < 32000,
          let request = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
          let op = request["operation"] as? String, let params = request["parameters"] as? [String: Any],
          let bundle = params["app"] as? String else { finish(["error": "请求无效"], code: 2) }
    if op == "check" {
        finish(["platform": "macos", "accessibility": AXIsProcessTrusted(), "screen_recording": CGPreflightScreenCaptureAccess()])
    }
    guard AXIsProcessTrusted() else { finish(["error": "请给 JACDesktopController.app 授予辅助功能权限"], code: 2) }
    let apps = NSRunningApplication.runningApplications(withBundleIdentifier: bundle)
    guard apps.count == 1 else { finish(["error": "请先打开授权应用"], code: 2) }
    let app = apps[0]
    if op == "observe" {
        app.activate(options: [])
        Thread.sleep(forTimeInterval: 0.2)
        finish(observe(app).0)
    }
    let (state, nodes) = observe(app)
    if op == "screenshot" { finish(capture(app, state: state)) }
    if op == "click_point" {
        guard params["snapshot"] as? String == state["snapshot"] as? String,
              let x = params["x"] as? Double, let y = params["y"] as? Double,
              let window = nodes.first, let rect = rectangle(window), rect.contains(CGPoint(x: x, y: y)) else {
            finish(["error": "截图目标已失效或坐标不在目标窗口内"], code: 2)
        }
        check(app)
        dispatched = true
        for type in [CGEventType.leftMouseDown, CGEventType.leftMouseUp] {
            CGEvent(mouseEventSource: nil, mouseType: type, mouseCursorPosition: CGPoint(x: x, y: y), mouseButton: .left)?.post(tap: .cghidEventTap)
        }
        Thread.sleep(forTimeInterval: 0.15)
        finish(observe(app).0)
    }
    guard params["snapshot"] as? String == state["snapshot"] as? String,
          let token = params["target"] as? String, let index = Int(token), nodes.indices.contains(index) else {
        finish(["error": "窗口或控件已改变，请重新观察"], code: 2)
    }
    let node = nodes[index]
    if string(node, kAXSubroleAttribute) == kAXSecureTextFieldSubrole {
        finish(["error": "密码控件须手动操作"], code: 2)
    }
    check(app)
    switch op {
    case "click":
        dispatched = true
        if AXUIElementPerformAction(node, kAXPressAction as CFString) != .success { click(node, app: app) }
    case "double_click": click(node, app: app, double: true)
    case "fill": fill(node, text: params["text"] as? String ?? "", app: app)
    case "press": press(node, key: params["key"] as? String ?? "", app: app)
    case "scroll":
        guard let dy = params["dy"] as? Int, abs(dy) <= 1000, let rect = rectangle(node) else {
            finish(["error": "滚动参数无效"], code: 2)
        }
        CGEvent(mouseEventSource: nil, mouseType: .mouseMoved, mouseCursorPosition: CGPoint(x: rect.midX, y: rect.midY), mouseButton: .left)?.post(tap: .cghidEventTap)
        check(app)
        dispatched = true
        CGEvent(scrollWheelEvent2Source: nil, units: .pixel, wheelCount: 1, wheel1: Int32(dy), wheel2: 0, wheel3: 0)?.post(tap: .cghidEventTap)
    case "drag":
        guard let value = params["destination"] as? String, let endIndex = Int(value), nodes.indices.contains(endIndex),
              let start = rectangle(node), let end = rectangle(nodes[endIndex]) else { finish(["error": "拖动目标无效"], code: 2) }
        let from = CGPoint(x: start.midX, y: start.midY), to = CGPoint(x: end.midX, y: end.midY)
        pressedPoint = from
        dispatched = true
        CGEvent(mouseEventSource: nil, mouseType: .leftMouseDown, mouseCursorPosition: from, mouseButton: .left)?.post(tap: .cghidEventTap)
        defer {
            CGEvent(mouseEventSource: nil, mouseType: .leftMouseUp, mouseCursorPosition: to, mouseButton: .left)?.post(tap: .cghidEventTap)
            pressedPoint = nil
        }
        for step in 1...10 {
            check(app)
            let t = CGFloat(step) / 10
            let point = CGPoint(x: from.x+(to.x-from.x)*t, y: from.y+(to.y-from.y)*t)
            pressedPoint = point
            CGEvent(mouseEventSource: nil, mouseType: .leftMouseDragged, mouseCursorPosition: point, mouseButton: .left)?.post(tap: .cghidEventTap)
            Thread.sleep(forTimeInterval: 0.02)
        }
    default: finish(["error": "未开放的桌面操作"], code: 2)
    }
    Thread.sleep(forTimeInterval: 0.15)
    var after = observe(app).0
    after["action_receipt"] = "事件已发送；请根据最新控件状态核验业务结果"
    finish(after)
}

// 建立AppKit与窗口服务器连接，ScreenCaptureKit的窗口过滤器依赖该连接。
_ = NSApplication.shared
NSApp.setActivationPolicy(.prohibited)
main()
