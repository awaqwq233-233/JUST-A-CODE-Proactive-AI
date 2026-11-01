// 独立桌面验收窗口，仅使用合成文本，不访问用户文档。
import AppKit

class Fixture: NSObject, NSApplicationDelegate {
    var window: NSWindow!
    let input = NSTextField(string: "初始内容")
    let output = NSTextField(labelWithString: "等待更新")

    /// 显示稳定可访问控件，供实际桌面组件核验输入和点击。
    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(contentRect: NSRect(x: 200, y: 200, width: 500, height: 260),
                          styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "J.A.C. 独立桌面验收"
        input.frame = NSRect(x: 30, y: 160, width: 430, height: 30)
        input.setAccessibilityTitle("测试输入框")
        let button = NSButton(title: "更新预览", target: self, action: #selector(update))
        button.frame = NSRect(x: 30, y: 110, width: 120, height: 30)
        output.frame = NSRect(x: 30, y: 60, width: 430, height: 30)
        window.contentView?.addSubview(input)
        window.contentView?.addSubview(button)
        window.contentView?.addSubview(output)
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    /// 点击后显示实际输入值，使后置检查无需依赖模型的成功声明。
    @objc func update() { output.stringValue = "已确认：" + input.stringValue }

    /// 关闭测试窗口就退出，不留下验收后台程序。
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}

let app = NSApplication.shared
let delegate = Fixture()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
