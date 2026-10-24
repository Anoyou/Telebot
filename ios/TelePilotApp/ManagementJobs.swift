import SwiftUI
import TelePilotCore

struct ManagementJobView: View {
    let id: String
    var updateJob = false
    var liveness = false
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var scenePhase
    @State private var value: JSONValue?
    @State private var error: String?
    private var path: String {
        let id = ManagementText.pathSegment(id)
        if updateJob { return "/api/system/update-jobs/\(id)" }
        if liveness { return "/api/commands/llm-providers/liveness/\(id)" }
        return "/api/plugin-config-action-jobs/\(id)"
    }
    private var terminal: Bool {
        let state = value?["status"]?.stringValue ?? value?["state"]?.stringValue ?? ""
        return ["succeeded", "success", "failed", "completed", "cancelled", "canceled", "stopped", "done"].contains(state)
    }
    var body: some View {
        List {
            if let error { NoticeView(title: "任务进度读取失败", message: error) }
            if let value { ManagementValueView(value: value) }
            else if error == nil { ProgressView("正在读取任务进度…") }
            Section {
                Button("刷新任务进度", systemImage: "arrow.clockwise") { Task { await load() } }
                if !terminal, !updateJob {
                    if liveness { ManagementActionLink(action: .init(title: "取消测活", path: path + "/cancel", destructive: true)) }
                    else { ManagementActionLink(action: .init(title: "暂停或终止任务", path: path + "/control", destructive: true)) }
                }
            } footer: { Text("任务在服务器继续执行，离开此页面不会中断任务。页面处于前台时会自动更新进度。") }
        }
        .navigationTitle(updateJob ? "服务器升级进度" : liveness ? "模型测活进度" : "插件任务进度")
        .navigationBarTitleDisplayMode(.inline)
        .task(id: scenePhase) {
            guard scenePhase == .active else { return }
            repeat {
                await load()
                if terminal || Task.isCancelled { break }
                do { try await Task.sleep(for: .seconds(3)) } catch { break }
            } while !Task.isCancelled
        }
        .refreshable { await load() }
    }
    private func load() async {
        do { value = try await model.request(path: path); error = nil }
        catch { self.error = error.localizedDescription }
    }
}
