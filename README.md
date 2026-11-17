# 寰宇战情 · 世界新闻沙盘（World News Sandbox）

把国际重大新闻做成一张可交互的「战情沙盘」：游戏风格的世界地图（暗黑底色 + 发光节点 + 余烬粒子 + 战争迷雾开场），新闻按真实地理位置落在地图上，一眼看清事件发生在哪里、涉及哪些国家。

支持 **Linux 桌面应用**（WebKitGTK 原生窗口）与 **纯浏览器**（单文件 HTML）两种形态，共用同一份地图与数据。

## 功能特性

- **高精度世界地图**：Natural Earth 50m 矢量边界（241 国/地区），海岸线双层发光描边，跨 ±180° 经线正确拆分，支持拖拽平移、滚轮缩放、双击放大。
- **新闻节点交互**：点击查看事件详情（摘要、事发地点、涉及国家、关联事件、信源链接）；同一事件的跨国连线动态流动；选中时涉及国家整片高亮。
- **分类与日期筛选**：战争冲突 / 外交谈判 / 经济金融 / 科技商业 / 气候环境 / 政治内政 / 灾害事故 七类开关；按日筛选。
- **新闻卷轴**：左侧抽屉列出全部要闻，点击自动飞行定位。
- **指定日期（历史学习）**：顶栏日期选择器可切换到任意历史周，数据按周独立存储（`data/news-<起始日>_<截止日>.js`），无数据时提示用 workflow 补跑。
- **北京时间显示**：顶栏实时时钟（Asia/Shanghai，每秒刷新）。
- **数据自动更新**：页面检测「当前日期 − 新闻最新日期 ≥ 1 天」即触发热重载；数据源更新后自动刷新页面，全程状态提示。
- **更新中心**：顶栏「检查更新」按钮，手动补跑时自动生成更新指令并复制，TRAE 执行后页面自动提示更新完成。

## 快速开始

### 方式一：Linux 桌面应用（推荐）

```bash
# 一键启动
./启动国际新闻沙盘.sh

# 或直接运行
python3 desktop/news_sandbox.py
```

依赖：`python3-gi` 与 `gir1.2-webkit2-4.1`（Ubuntu 可 `sudo apt install gir1.2-webkit2-4.1 python3-gi`）。已内置 X11 后端，Wayland 桌面下同样可用。

系统应用菜单中也可搜索「国际新闻沙盘」启动（已附带 `.desktop` 启动器与图标）。

### 方式二：浏览器打开

直接用浏览器打开 `world-news-map.html` 即可（数据通过 `<script src>` 本地加载，无需服务器）。也可以：

```bash
python3 -m http.server 8123
# 访问 http://127.0.0.1:8123/world-news-map.html
```

## 数据与自动更新

新闻数据与视图完全分离，地图 HTML 无需修改：

- `news-data.js`：`window.NEWS_DATA = { latestDate, generatedAt, events }`，`events` 含标题、分类、经纬度、涉及国家、摘要、信源链接等字段。
- **每日定时任务**：每天自动检索近一周国际新闻 → 按日期归档到 `archive/` → 重写 `news-data.js`。
- **页面自动接力**：打开页面或停留期间，每分钟检测一次数据日期，过期则热重载并自动刷新。
- **手动补跑**：对 TRAE 说"更新新闻"，或点击页面「检查更新」获取更新指令；更新完成后页面自动提示。

### 独立更新 workflow（不依赖 TRAE）

`workflow/news_updater.py` 将更新规则固化为独立流程：读取 `latestDate` 检测过期（≥1 天）→ 调用带联网搜索的 LLM API → JSON 结构化校验 → 原子写入 `news-data.js` 与 `archive/`。已最新则跳过，失败保留旧数据并记录日志（`workflow/updater.log`）。

```bash
# 配置 API（当前默认 Perplexity Sonar，自带联网搜索，单次调用即可）
# 方式一：环境变量（推荐，不进任何文件）
export NEWS_API_KEY="你的key"
# 方式二：用户级私有配置 ~/.config/news-sandbox/config.json（600权限，不进git）
# 方式三：直接运行脚本，交互式输入（不回显，可选保存为600私有配置）
python3 workflow/news_updater.py

# 项目内 workflow/config.json 也可放非敏感配置（base_url/model/proxy），
# 该文件已被 .gitignore 排除，不会上传；key 请勿写入任何会被提交的文件。

# 安装 systemd 用户定时器（每天 07:00 / 21:00，Persistent=true，关机错过开机自动补跑）
bash workflow/install_timer.sh

# 手动执行一次（更新最新数据）
python3 workflow/news_updater.py

# 指定日期补跑（历史学习/测试 workflow）：检索该日期及其前7天新闻，
# 输出 data/news-<起始日>_<截止日>.js，不覆盖最新主数据
python3 workflow/news_updater.py --date 2026-11-15
```

## 目录结构

```
.
├── world-news-map.html      # 地图主页面（自包含，内嵌 50m 世界地图数据）
├── news-data.js             # 新闻数据（每日任务重写此文件）
├── desktop/
│   ├── news_sandbox.py      # Linux 桌面壳（GTK + WebKitGTK）
│   ├── icon.svg             # 应用图标
│   └── 国际新闻沙盘.desktop  # 桌面启动器
├── 启动国际新闻沙盘.sh       # 一键启动脚本
├── archive/                 # 按周归档的每日新闻（Markdown，附信源）
├── 国际新闻周总结_*.html     # 周总结报告
├── 国际新闻沙盘演示.mp4      # 操作演示视频
└── README.md
```

## 技术说明

- 前端：原生 HTML/Canvas（无框架、无构建），自包含离线可用。
- 桌面壳：Python + PyGObject + WebKitGTK 4.1。
- 地图数据：Natural Earth 50m（world-atlas TopoJSON 解码为 GeoJSON 内嵌）。
- 新闻来源：新华社/人民网/央视、TASS、ISW、联合国、Reuters 等公开报道，重要事件多信源交叉核实，单一来源消息标注"未获独立证实"。

## 说明

- 新闻数据为公开报道整理，战场数字以交战方通报口径为准。
- 演示视频由真实页面自动操作录制。
