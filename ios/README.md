# CourseShell —— 自用 iOS 壳

一个最小的 WKWebView 壳：打开一个地址，自动注入 `Resources/inject.js`，右上角齿轮改地址。
所有文件都是纯文本，Windows 上写完，构建交给 GitHub Actions 的 macOS runner，产物是**未签名 ipa**。

## 目录

```
ios/
├─ project.yml              # XcodeGen 工程描述（不手写 pbxproj）
├─ Sources/
│  ├─ App.swift             # @main
│  ├─ RootView.swift        # 地址配置 + 设置面板
│  └─ ShellWebView.swift    # WKWebView + WKUserScript 注入
└─ Resources/
   ├─ Info.plist            # 允许 http 局域网 / 中文名「刷课」
   └─ inject.js             # 你的注入脚本
```

## 一、手机上装 SideStore（iOS 27+ 全程不用电脑）

1. Safari 打开 <https://sideinstaller.net/>，用页面上的任一证书装 SideInstaller。
2. 设置 → 通用 → VPN 与设备管理 → 信任对应证书。
3. 打开 SideInstaller → 连上 LocalDevVPN → 用**免费 Apple ID** 登录 → 选 `Install SideStore + LiveContainer`。
4. 装完就能在手机上直接签名安装任意 ipa。

> iOS 27+ 不需要配对文件，也不需要电脑；26.4–26.7 反而要一台电脑生成 remote pairing 文件。
> 只从 `sideinstaller.net` 或 [FrizzleM/SideInstaller](https://github.com/FrizzleM/SideInstaller) 下载，
> 同名的 `sideinstaller.com` 是钓鱼站，会偷 Apple ID。

## 二、Windows 上出 ipa

```powershell
git add ios .github
git commit -m "ios: course shell"
git push
```

GitHub → Actions → `build-ipa` → Run workflow → 跑完从 Artifacts 下 `CourseShell-ipa`。

想换 SDK 版本时改 `runs-on: macos-26`（需要 iOS 27 SDK 时才有意义；目前用 Xcode 16 的 SDK 也能跑在 27 上）。

## 三、装到手机

1. ipa 传进「文件」（iCloud Drive / 微信文件传输都行）。
2. SideStore → My Apps → `+` → 选 ipa → 签名安装（免费账号：3 个 app、7 天有效）。
3. 想绕开 3 个和 7 天：把 ipa 装进 **LiveContainer**，未签名 app 在里面跑，不限个数。

## 四、接上本体

`server.py` 现在只绑 `127.0.0.1:8765`，手机连不上。要让它可达，得动三处：

| 位置 | 现在 | 改成 |
| --- | --- | --- |
| `server.py:1663` | `ThreadingHTTPServer(("127.0.0.1", chosen), Handler)` | `("0.0.0.0", chosen)` |
| `server.py:842` `_host_allowed` | 只认回环 Host | 放行本机局域网 IP |
| `server.py:850-852` `_json_request_allowed` | Origin 白名单只有回环 | 加上 `http://<本机IP>:<端口>` |

⚠️ 这三处原本就是防 DNS rebinding 和跨站提交的。放开 = 同一 Wi-Fi 下任何人都能操作你的账号，
所以务必同时加一层 token（URL 里带 `?t=...`，或自定义 header），别裸奔。

改完把壳的设置地址填成 `http://<电脑局域网IP>:8765/`。

如果暂时不想动后端：壳的默认地址是学习通网页版，注入脚本照样生效，只是没有本体那套 API。

## 已知限制

- 免费账号签的 app 不能推送，后台也留不住。
- 竖屏锁定、无图标（没放 Assets.xcassets，自用够用）。
- 改 `inject.js` 之后要重新走一遍 Actions。
