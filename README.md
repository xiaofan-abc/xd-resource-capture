# 西电超星课程资源本地工具

一个面向西安电子科技大学超星/学习通课程页面的本地 Web 工具，用于读取课程资源、解析可下载链接，并下载课程资料或课程回放视频。

项目由 FastAPI 后端和 Vue 前端组成。后端负责读取本地浏览器登录态、请求超星页面、解析资源链接和执行下载任务；前端提供单页操作界面。

## 功能

- 课程资源抓取：按学期读取课程，选择章节后解析 PDF、文档或视频资源。
- 课程回放下载：按学期读取可回放课程，选择课时画面后下载视频。
- 课件与课堂同步说明：课程回放页提供 GridPlayer 使用说明，帮助同时播放课件画面和课堂画面。
- 本地登录态复用：通过 Playwright 打开登录窗口，登录后状态保存在本地 profile 中。
- 搜索过滤：课程支持按课程名、教师、课号搜索；章节支持按章节名或章节 ID 搜索。
- 异步任务日志：解析和下载过程通过页面日志实时反馈。
- 可配置参数：浏览器通道、并发数、输出目录、文件命名方式等可在页面左上角设置。

## 目录结构

```text
web_app/
  app.py                 FastAPI 应用入口和 API 路由
  xidian_service.py      课程、章节、回放、资源解析和下载逻辑
  task_manager.py        后台任务和 SSE 日志管理
  login_runner.py        登录辅助浏览器进程
  crawler_runner.py      子进程任务启动辅助
  frontend/              Vue + Vite 前端源码
  static/                已构建的前端静态文件，后端直接服务这里
requirements.txt         Python 依赖
install_requirements.bat Windows 一键安装依赖并启动
start_project.bat        Windows 仅启动后端
README.md                项目说明
```

## 环境要求

- Python 3.10+
- Node.js 18+，仅在需要重新构建前端时使用
- Windows、macOS 或 Linux 桌面环境
- 本机可打开 Chrome、Edge 或 Playwright Chromium

## 安装

Windows 下可以直接双击：

- `install_requirements.bat`：安装 Python 依赖、安装 Playwright Chromium，并在完成后直接启动项目。
- `start_project.bat`：仅启动项目，适合依赖已经安装好的情况。

安装 Python 依赖：

```bash
pip install -r requirements.txt
playwright install chromium
```

如果你需要修改前端源码并重新构建：

```bash
cd web_app/frontend
npm install
npm run build
```

构建产物会输出到 `web_app/static/`。

## 运行

Windows 下如已安装依赖，直接双击 `start_project.bat` 即可。

在项目根目录启动后端：

```bash
python -m uvicorn web_app.app:app --host 127.0.0.1 --port 8000
```

然后打开：

- 课程资源：http://127.0.0.1:8000/
- 课程回放：http://127.0.0.1:8000/replay

**关闭后端：**
在运行该命令的终端（命令行窗口）中，按下 `Ctrl + C` 即可停止后端服务。

## 使用流程

1. 点击页面左上角的 `登录`，再点击 `打开登录`。
2. 在弹出的浏览器窗口中完成西电统一身份认证或超星登录。
3. 登录完成后回到页面，点击 `释放窗口` 或 `检查状态`。
4. 在 `全局参数` 中按需调整 profile、浏览器、并发数、输出目录等。
5. 在主页面依次选择学期、课程、章节或课时，解析并下载选中的资源。

## 登录和隐私

登录态会保存在本地 profile 目录中，例如 `.xidian-profile/`。这些目录包含 Cookie 或浏览器缓存，不能上传到 GitHub。

本仓库的 `.gitignore` 已忽略以下本地内容：

- `.xidian-profile/`
- `.browser-profile/`
- `.playwright-cli/`
- `downloaded_xidian/`
- `downloaded_replays/`
- `node_modules/`
- `__pycache__/`

## 注意事项

- 本工具仅用于个人课程资料备份和学习用途。
- 下载资源时请遵守学校、课程平台和授课教师的相关规定。
- 如果页面显示未登录，通常是 Cookie 过期或 profile 选择不一致，重新打开登录即可。
- 如果更换输出目录，下载任务会保存到项目根目录下对应路径。
