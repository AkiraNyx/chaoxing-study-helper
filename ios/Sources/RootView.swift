import SwiftUI

struct RootView: View {
    @AppStorage("home_url") private var homeURL: String = "https://mooc1.chaoxing.com/"
    @State private var showingSettings = false

    var body: some View {
        ZStack(alignment: .bottomTrailing) {
            ShellWebView(url: resolvedURL)
                .ignoresSafeArea(edges: .bottom)

            Button {
                showingSettings = true
            } label: {
                Image(systemName: "gearshape.fill")
                    .font(.system(size: 15, weight: .semibold))
                    .padding(11)
                    .background(.ultraThinMaterial, in: Circle())
            }
            .padding(.trailing, 14)
            .padding(.bottom, 14)
        }
        .sheet(isPresented: $showingSettings) {
            SettingsSheet(urlString: $homeURL)
        }
    }

    private var resolvedURL: URL {
        URL(string: homeURL) ?? URL(string: "https://mooc1.chaoxing.com/")!
    }
}

struct SettingsSheet: View {
    @Binding var urlString: String
    @Environment(\.dismiss) private var dismiss
    @State private var draft: String = ""

    var body: some View {
        NavigationStack {
            Form {
                Section("起始地址") {
                    TextField("http://192.168.1.10:8765/", text: $draft)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                }

                Section {
                    Text("填跑着本体的那台电脑的局域网地址，比如 http://192.168.1.10:8765/。保存后回到壳里会自动重新加载。")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }

                Section {
                    Text("注入脚本 inject.js 打包在 app 里，每次页面加载完成后自动执行。改脚本要重新构建一次。")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
            .navigationTitle("设置")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("取消") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("保存") {
                        let trimmed = draft.trimmingCharacters(in: .whitespacesAndNewlines)
                        if !trimmed.isEmpty { urlString = trimmed }
                        dismiss()
                    }
                }
            }
            .onAppear { draft = urlString }
        }
    }
}
