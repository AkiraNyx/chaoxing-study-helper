# 文件清单

> 生成时间：2026-10-23。除 `dist/`、`docs/` 截图和测试夹具外，其余均为源码。

## 运行入口

| 文件 | 作用 |
| --- | --- |
| `dist/学习通刷课.exe` | 最终单文件桌面版（PyInstaller onefile + WebView2，约 19 MB） |
| `desktop.py` | 桌面窗口入口：pywebview、frameless、窗口居中、单实例互斥 |
| `server.py` | 本地 HTTP 服务与全部 JSON API（登录、刷课、做题、扫描、AI、诊断） |

## 业务模块

| 文件 | 作用 |
| --- | --- |
| `chaoxing.py` | 学习通客户端：登录、课程、章节、任务点、视频上报、测验读取/提交 |
| `ai_client.py` | OpenAI 兼容 AI 客户端：`chat`、`test`、`list_models`，支持 `reasoning_effort` |
| `font_decoder.py` | 测验页字体加密解码：抽取 base64 字体、字形哈希、映射回真字 |
| `session_store.py` | Windows DPAPI 加密的 Cookie 会话存储 |
| `font_map_table.json` | 字形哈希 → 字符映射表（30,884 条），随 exe 打包 |
| `crypto.py` | 纯 Python AES-128-CBC，用于账号密码登录加密 |

## 前端

| 路径 | 作用 |
| --- | --- |
| `web/index.html` | 单页结构：登录、刷课、做题、AI 接口、设置、关于 |
| `web/style.css` | 暖纸色浅色/深色主题、组件、动效 |
| `web/app.js` | 页面状态、API 调用、进度/日志、答题卡、anime.js 动效 |
| `web/vendor/` | anime.js v4、Lucide 图标 |
| `web/fonts/` | 内置英文 Sans/Serif/Mono + MiSans 子集 |
| `web/favicon.svg` | 页面图标 |

## 构建与资源

| 文件 | 作用 |
| --- | --- |
| `build_exe.py` | 重新打包 exe；包含 `web/`、`icon.ico`、`font_map_table.json` |
| `icon.ico` | 窗口 / exe 图标 |

## 测试与文档

| 路径 | 作用 |
| --- | --- |
| `tests/mock_chaoxing.py` | 本地模拟学习通服务（登录、课程、章节、视频、文档、测验） |
| `tests/test_pipeline.py` | 刷课端到端：完成数、跳过数、进度、enc 校验 |
| `tests/test_server_api.py` | HTTP API 刷课链路 |
| `tests/test_qr_login.py` | 扫码状态机 |
| `docs/*.png` | 界面截图（部分为早期版本，以实际界面为准） |
| `docs/hero.webp` | README 头图（项目横幅，HTML 渲染） |
| `docs/mascot.webp` | Q 版鲸鱼娘，文档吉祥物（CC BY-NC-SA 4.0） |
| `README.md` | 使用说明 |
| `LICENSE` | GNU AGPL-3.0 许可证全文 |
| `NOTICE` | 版权与许可声明（AkiraNyx） |
| `docs/FILES.md` | 本文件 |

## 常用命令

```powershell
# 自检
python server.py --selftest

# 测试
python tests/test_pipeline.py
python tests/test_server_api.py
python tests/test_qr_login.py

# 重新打包
python build_exe.py
```

## 维护约定

- 根目录只放源码、构建脚本和打包必需资源；临时抓取文件、`build/`、`__pycache__` 用完即删。
- 新增前端资源放 `web/`，新增打包资源同步更新 `build_exe.py` 的 `--add-data`。
- 抓取真实页面用的 Cookie 文件不留在项目中，使用后立即删除。
