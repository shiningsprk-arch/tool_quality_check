# 质量体检（MyBooks Toolbox 外置工具）

把 calibre 插件 **Quality Check**（[kiwidude68/calibre_plugins](https://github.com/kiwidude68/calibre_plugins)，GPL-3，Copyright 2011 Grant Drake）改写成 MyBooks 的工具箱工具：**一次体检跑完一批检查项，出一份按书聚合的报告**。

覆盖 **EPUB / MOBI / AZW3 / PDF / TXT** 五种格式：上游那 75 项（EPUB 结构、MOBI 的 EXTH、封面、元数据）原样移植；PDF / AZW3 / TXT 的 16 项常见检查是本仓新写的（上游一个都没有），见下面「PDF / AZW3 / TXT 检查」。

**只读**：体检不改书库任何数据、不碰书文件；只在宿主的工具工作目录里放报告与临时的封面副本。

---

## 安装

1. 系统设置 → 高级配置项，打开 `ENABLE_TOOLBOX_DEV_MODE`。
2. `/admin/toolbox` 上传 `dist/quality_check-0.2.1.zip`。
3. **重启服务**（外置工具的 import 与路由挂载只在进程启动时跑一次；禁用/启用才是即时生效）。
4. 工具页 `/toolbox/quality_check`。

卸载同样需要重启。

## 用法

范围四选一：**全库** / **选书**（搜索勾选，可多选）/ **查询串**（Calibre 语法，如 `formats:epub and tags:小说`）。
各类检查只跑有对应格式的书：EPUB 类只跑有 EPUB 的、MOBI/AZW3 类只跑有 MOBI/AZW/ZAW3 的、PDF 与 TXT 同理。

检查项按组勾选（EPUB / MOBI·AZW3 / PDF / TXT / 封面 / 元数据 / 缺失项，共 93 项、支持 91 项），带 6 个预设：**推荐（默认，81 项）**、全选、只看结构、只看元数据、只看排版杂项、清空。
清单顶部有**过滤框**（按名称/键筛选，93 项里找一项不用再翻），分组标题可折叠（折叠状态记在本机），每组标题显示"已选 n/总数"。
不满足内置预设时可以**保存自己的预设**：勾好清单 → 填个名字 → 「保存当前选择」，之后从下拉里一键套用（存在浏览器本地，不上传）。
封面那一组可以选**判定方式**：尺寸（宽×高）/ 文件大小（KB）/ 宽高比（比例±容差）/ 缺封面，再配一个**条件**（小于 / 大于阈值）。判定方式没有选中时（复选框关掉）封面检查整个不跑——它不会"用默认值偷偷跑一遍"。

**默认预设是"推荐"而不是"全选"**：有 10 项检查本身没错、但对一个直接入库、没经 calibre 处理过的中文书库会命中几乎全部书（"有没有 calibre 插入的 SVG 封面"、"哪些书没被 calibre 转换过"、"作者名里有没有逗号"这类），一起跑会把真正的问题淹掉。这些项在清单里带虚线**「噪声」徽章**（悬停给出原因）、默认不勾，需要时可以单独勾上；报告里若出现，明细旁边也会写明"为什么这项会命中很多书"。

严重度的含义：**错误**只给结构性损坏（zip 坏、缺 container.xml、清单里的文件缺失、目录/guide 链接断裂、图片链接断裂、DRM），**警告**是可能需要处理的数据问题（缺元数据、ISBN 无效、重复 ISBN/丛书、异常排版痕迹等），**提示**是信息与取向类（字体、边距、护封、取向对偶的检查）。
点「开始体检」后任务进宿主后台队列，页面每 2 秒轮询进度（当前检查项、第几本、已用时长与粗估剩余；当前书可直接点开）；也可在宿主后台任务面板看到它。刷新页面会从 `/progress` / `/report` 把上一次任务的状态和报告捡回来。

**上次的结果不会因为重开工具页而消失**：宿主只把后台任务放在内存里（安装/更新工具、重启 MyBooks 都会清掉），所以每次跑完会在工具共享目录补写一个 `latest.json`，指向刚写下的那份 `report.json`；`/progress`、`/report`、`/summary` 在内存里找不到任务时回退读它，页面入口会显示「体检完成 · 上次体检结果」并直接打开报告（报告顶部本来就带生成时间）。标记按 `task_id` + `generated_at` 校验，只认自己写的那一份——新进程里 task_id 会从 1 重来，指向旧目录时不会被当成新结果。确实还没跑过（`task.not_found`）才显示空态，其它错误会明说，不再静默留白。

报告有两个视角：

- **按书**：一行一本书 + 该书的全部问题；点书名弹出**单本聚焦**（一次看完这本书的每条明细），点行本身展开内联明细；可按严重度/检查项筛选、翻页（每页 50/100/200）、导出 CSV、书名可跳宿主书籍详情页。
  逐条明细只对**上游会记日志**的检查项存在（实测 13 本真书上 28 个命中的检查项里有 7 个有明细，例如"CSS 书级边距冲突"会写出具体命中了哪条 margin 规则、"未登记文件"会列出多余文件名）；其余检查项上游只 `return True/False` 标记命中、不写日志，报告里会如实显示"仅标记命中"。明细里的 HTML 标签、实体和制表缩进会在入库报告前清成纯文本（上游那些字符串是给富文本对话框看的）。
- **按检查项**：一张"哪个检查项最脏"的表（命中书数、明细条数、前几本），每行有"只看这一项"，点了跳到按书视角并带上该筛选。

窄屏（< 640px）下报告表自动变成卡片式；深色模式跟随宿主主题。键盘：`/` 聚焦检索框，`Esc` 关弹层，报告行 `Enter`/空格展开。

---

## 这是怎么移植的（为什么检查逻辑一行没改）

```
backend/
├── qc/                 上游 Quality Check 的检查代码（GPL-3）
│   ├── check_epub.py        2127 行，41 项 EPUB 检查
│   ├── check_metadata.py    19 项元数据检查
│   ├── check_covers.py / check_missing.py / check_mobi.py / helpers.py
│   ├── check_pdf.py / check_txt.py / check_azw3.py   ← **本仓新写**（上游没有；文件头已注明）
│   ├── mobi6.py            上游原样 + 本仓的只读扩展（EXTH 记录表、加密/KF8/越界事实）
│   ├── menus.py            上游 config.py 的菜单/常量部分（Qt 部分去掉；末尾 16 条是本仓新增）
│   ├── dialogs.py          ← 唯一新写的"运行时替身"，见下
│   └── shim/               ← 最小 calibre API 替身
├── adapter.py           CoreAPI → 上游期望的 db/gui 形状
├── driver.py            范围、进度、结果收集、报告
└── tool.py              BaseTool + 7 条 @js @is_admin 路由
```

两个接缝是这次移植的全部工作量：

1. **`qc/dialogs.py`**：上游用 `QProgressDialog` + QTimer 驱动逐书循环。这里换成一个**同步、可取消、容错**的循环，并顺手把每本书的日志增量归属到那本书——报告里每本书的明细就是这么来的（另一根必须接对的线：检查项写日志用的是自己的 `BaseCheck.log`，所以 `driver` 在跑每个检查项之前会把它指到 `gui.current_log` 上；接错就会像早期那样，每条问题都只剩"该检查项上游不输出逐条明细"）。上游的 `check_all_files()` 因此可以逐字不动。
2. **`qc/shim/`**：上游会 `import calibre.*` 的一小撮 API（zipfile / logging / chardet / oeb.XPath / parse_html / metadata 工具函数 / Encryption / six / polyglot）。这里逐个补上最小实现；`adapter.py` 再把宿主 CoreAPI 包装成上游期望的 `db` / `gui`。

检查结果如何收集：上游把命中的书用 `set_marked_ids` + `marked:xxx` 搜索呈现给 GUI。这里把 `set_marked_ids` 收进报告；`MissingDataCheck` 那 11 项上游只是设一个 calibre 搜索串让 GUI 过滤，这里优先把该查询串转发给宿主的 `search_ids()`，失败再本地判定。

---

## PDF / AZW3 / TXT 检查（本仓新增的 16 项）

上游 Quality Check 只有 EPUB 与 MOBI 的检查，PDF/TXT 一个都没有；AZW3 也只有"缺 EBOK / 缺 ASIN / 分享开关 / 摘录限制"这 4 项。下面这 16 项是本仓新写的，判定标准都是"读得了吗、内容在不在"，**只读文件**，不改书、不落盘。

| 组 | 检查项 | 查什么 | 分级 |
|---|---|---|---|
| PDF | 打不开 | 开不了、0 页、或**要靠 PyMuPDF 修复才打开**（实测把 `%PDF` 头改坏就是这个） | 错误 |
| PDF | 已加密 | 需要口令或权限受限（对应 EPUB 的"含 DRM"） | 错误 |
| PDF | 无文字层 | 抽样的页里一个字都没有（扫描件）——搜索、朗读、转 EPUB 全都会失败 | 警告 |
| PDF | 页面尺寸混用 | 同一文档里混着不同页面尺寸（容差 5%） | 提示 |
| PDF | 单页体积过大 | 平均每页 > 1.5 MB（图片没压/DPI 过高） | 提示 |
| PDF | 无目录 | 取不到 outline | 提示 |
| TXT | 是空文件 | 0 字节或只有空白 | 错误 |
| TXT | 不是 UTF-8 | 严格 UTF-8 解码失败（GBK/Big5/UTF-16…），日志里给出编码猜测 | 警告 |
| TXT | 带 BOM | 开头有 BOM（阅读器常把它显示成一个乱码字） | 提示 |
| TXT | 含控制字符 | NUL 等控制字符；NUL 基本等于"这不是文本" | 提示 |
| TXT | 换行符混用 | CRLF / LF / CR 混着用 | 提示 |
| TXT | 单行超长 | 单行 > 5000 字符，或整本没有换行（网页抓取文本的典型畸形，分章会失败） | 提示 |
| AZW3 | 有 DRM | PalmDOC 头的加密类型非 0（MOBI 的 DRM **只**标在这里） | 错误 |
| AZW3 | 文件截断 | 记录表越界/倒挂，或记录表本身超出文件 | 错误 |
| AZW3 | 缺封面记录 | EXTH 201/202/203 一个都没有 | 警告 |
| AZW3 | 没有 KF8 层 | 是 `.azw3` 却只有 MOBI6 头（version 6）——多半是 `.mobi` 改了扩展名 | 提示 |

几个刻意的取舍：

- **加密件不报"没有文字层/没有目录"**：口令挡着的时候这些事实是"读不到"而不是"没有"，所以 digest 里用 `None` 当哨兵区分两者。素材库刚建好时正是这里误报过（加密 PDF 一次报出三条）。
- **加固过的文件只报"读不了"**：PyMuPDF 会尽力修复损坏的 PDF，修好了也能打开；这时页级数据是抢救出来的碎片，再说"没有文字层"只是把一个结论稀释成三条。
- **页面尺寸容差 5%**：阈值是拿真书定的——参考书库里那本扫描绘本 30 页里有 29 页尺寸抖动几个百分点（扫描/拍照的常态），2% 容差会把整本书判成"混用"。
- **AZW3 那四项只判 AZW3 文件**：它们挂在「MOBI / AZW3」组里（沿用上游的分组），但 `.mobi` 本来就是 MOBI6，不该报"没有 KF8 层"。
- **PyMuPDF 不在就整组跳过**：宿主镜像里本来就有（宿主自己的 `minify_pdf` 就在用），万一没有，PDF 那 6 项会记进报告的 skipped 并写明原因，不会假装"PDF 全干净"。

---

## 与上游的差异（诚实清单）

**明确不做**：
- 上游 `Fix` 子菜单 11 项全部不做（那是写操作：改元数据、删孤儿文件、物理改名、重写 MOBI 字节）。本工具只读。
- `Search ePubs`（正则全文检索）不做——它依赖一个 Qt 对话框。
- `Check titles for title case` 摘除：需要 calibre 的 titlecase 词表，对中文书名无意义且误报高。清单里仍列出并标注为不支持。

**近似实现**（全部集中在 `qc/shim/`，与上游行为可能有偏差）：
- `title_sort`：**宿主在的时候直接用宿主自己的 `webserver.utils.get_title_sort()`**。因为 MyBooks 存的标题排序是拼音小写，用 calibre 的冠词规则重算的话，每本中文书都会和库里对不上、`check_title_sort` 会把全库标成问题（离线没有宿主时才退回"英文冠词后置、其余原样"的近似实现）。委托之后这个检查的含义变成"库里的排序串还是不是 MyBooks 现在会算出来的值"，也就是"改过书名但没重跑拼音排序"。`check_author_sort` 仍用作者名当排序串，不做换序推断。
- 宿主字段名：`adapter.py` 严格按宿主 `CoreAPI.calibre.get_data_as_dict()` 真实的键名取值——标题排序列叫 `sort`（calibre 的列名，不是 `title_sort`），未评分的 `rating` 是数值 `0` 而不是缺键，格式列表是 `available_formats`。`scripts/smoke_offline.py` 里的假 CoreAPI 必须照抄这些约定，否则它会连"键名写错"这种 bug 一起掩盖掉。
- `get_udc`：用 Unicode NFKD 折叠，替代 calibre 的手工字符表。
- `Encryption.is_encrypted`：自己解析 `META-INF/encryption.xml` 收集 CipherReference。
- `parse_html` / `XPath`：lxml 顶上（上游用 calibre 的 OEB 解析栈）。
- `load_defaults`（CSS 页边距检查的"期望值"）：固定四边 5pt，因为宿主没有 calibre 的转换页边距设置。
- 封面检查靠把封面按需物化成 `<工作目录>/covers/<book_id>/cover.jpg`，让上游那段 `os.path.join(library_path, path, 'cover.jpg')` 原样可用。
- 元数据字段缺失时返回**不可相等的哨兵**而不是 `None`，避免 `pubdate == timestamp` 这类判等把整库误报；缺失项检查在宿主不暴露该字段时**报"跳过"而不是报"命中"**。

**本仓对上游文件的两处扩展**（其余上游文件仍是逐字原样 + 只改 import）：

- `qc/mobi6.py`：EXTH 解析从"只认 4 个 id"扩成保留完整记录表，并多读 121/125/201/202/203；`MinimalMobiReader` 多给"PalmDOC 加密类型、记录表越界、MOBI 版本（=KF8 与否）"这些只读事实。同时给 EXTH 循环加了一道保护——上游遇到损坏的 `num_items`/`size` 会空转或越界切片。**只加读，不碰写路径**（`MinimalMobiUpdater` 依旧被封死）。
- `qc/menus.py`：末尾加了 16 条本仓新增的检查项（PDF 6 / TXT 6 / AZW3 4），字段形状与上游一致。

**已知代价**与上游一致，每个检查项会自己遍历一遍书库（上游也是 77 次、这里 93 次串行遍历）。整库跑全选会重复打开同一个 EPUB 很多次，几千本的库建议用预设缩小检查项。

---

## 前端

```
frontend/
├── index.html             静态页（宿主用 iframe 载入，无构建步骤）
├── app.js                 全部逻辑：事件改 state → render([parts])
├── lib/i18n.js            脚手架的 i18n 胶水（字串在 frontend/locales/*.json）
├── lib/theme.css          脚手架的共享变量（--mb-color-*，深浅跟随宿主主题）
└── lib/quality_check.css  本工具自己的样式（严重度配色变量对、移动端卡片、聚焦弹层）
```

两条约定：

- **检查项的 key 同时就是 i18n 键**（例如 `check_epub_fonts`）：后端只回 key，前端 `t(key)` 取三语文案，取不到再退回后端给的英文名。加检查项时只需在三个 locale 里各加一条。
- 事件回调只改 `state`，然后调 `render([parts])` 重画（只改一处就传对应的 part）；不要绕过它直接改 DOM，否则语言切换时会漏画。

本机没有 MyBooks 也能看前端：把 `frontend/` 拷到临时目录，补一个 stub `static/toolbox-bridge.js`（实现 `fetch`/`theme`/`locale`/`notify`，`fetch` 返回假的 checks / progress / report / summary），再用 `python -m http.server` 起静态服务即可在浏览器里点。本次 UI 改版（清单检索、双视角报告、聚焦弹层、移动端卡片、深色）就是这样验的。

## 开发

```bash
python -m pytest tests/                   # 回归测试（不依赖 MyBooks/calibre，秒级）
python scripts/smoke_offline.py            # 对真实 calibre 书库离线跑全部 91 项检查（不需要 MyBooks/calibre）
python scripts/make_format_library.py --verify   # 用本机 calibre 造多格式素材库并按预期命中表逐条校验
python scripts/smoke_offline.py --verbose --checks=check_epub_corrupt_zip
python scripts/build.py                    # → dist/quality_check-0.2.1.zip（含产物形状校验）
python scripts/make_icon.py                # 重绘 icon.png（1024 超采样 → 256 调色板，6KB）
python scripts/port_shim.py                # 重新生成 qc/shim/** 的规范内容（不碰 dialogs.py）
```

两层校验分工不同，两个都该跑：

- `tests/` 守**宿主这一侧的约定**——`get_data_as_dict()` 的键名、封面判定方式到上游四个选项的映射、报告筛选计数、缺字段时的哨兵语义。`smoke_offline.py` 替换掉的正是宿主，所以这类问题它天然看不见。
- `scripts/make_format_library.py` 用本机 calibre（`ebook-convert` / `calibredb`）现造一个多格式素材库：拿参考书库的 EPUB 转出 PDF / AZW3(KF8) / MOBI(MOBI6) / TXT，再定向造缺陷——截断的 AZW3 与 PDF、把 `%PDF` 头改坏的 PDF、加密 PDF、纯图片 PDF、尺寸混用与固定抖动的 PDF、去封面的 AZW3、GBK/BOM/CRLF 混用/NUL/单行超长/空白的 TXT。入库时逐本显式指定书名，最后打印一张**预期命中表**并写进 `expected.json`；`--verify` 会跑全部检查、逐本比对新检查的命中是否与预期一致（多报、漏报都算失败）。素材库落在系统临时目录（体积与版权原因不进仓库）。
  它是**新格式检查的主要验证手段**：与参考书库各 1 本的样本量不同，这里每个检查项都有正反两面样本。实测它抓出过两个真问题——加密 PDF 被顺带报成"没有文字层/没有目录"，以及页面尺寸容差太紧（2% 会把真书的扫描抖动判成"混用"）。参考书库那 13 本真书则用来守相反的一面：新检查在真书上**不该**误报（AZW3/PDF/TXT 三类都验过）。

- `smoke_offline.py` 直接用 sqlite3 读 calibre 的 `metadata.db` 并把 CoreAPI 假掉，默认跑 MyBooks 克隆里的 `tests/library`（13 本真书），跑完全部检查后**给结论**：有未处理异常或检查项漏跑就非零退出（`--allow-errors` 可放宽）。它是打包前的预检：**换任何 `qc/` 代码后都该先跑它**。

`port_shim.py` 只会重写 `qc/shim/**`；`qc/dialogs.py` 是手写的接缝，脚本不碰它（想改就改，不会被覆盖）。

## 还没验证的部分

离线环境没有 MyBooks 也没有 calibre，所以下面这些只做了静态核对（导入符号、签名、路由形状、对着 4.3.0 源码逐个核对字段名），装进真实实例才算数：

- `tool.py` 的 7 条路由、任务进度/取消（含"受理到开跑之间"的取消）、报告分页与筛选；
- 新格式检查在**宿主环境**里的两件事：① 宿主 Python 里 `import pymupdf`（或 `fitz`）是否可用——不可用时 PDF 那 6 项会整体 skipped，报告里会写明；② 真机上一批真实 PDF/TXT/AZW3 的命中率与阈值是否合适（素材库只能证明"判定对、不误报"，证明不了"阈值贴合真实脏数据"）；
- 「重开工具页自动打开上次报告」这条链路：`latest.json` 的读写逻辑本身在离线用假宿主跑过（报告落盘写标记、标记与报告对不上/文件缺失时都不冒充、内存里有任务时标记不顶班），但**"MyBooks 重启之后"这个真实场景**只能装进实例验证——重启一次 MyBooks，再打开工具页看是否直接显示上次报告并标着「上次体检结果」；
- `adapter.py` 的键名已对着宿主源码核过，但真机上 `get_data_as_dict()` 是否还有别的出入，值得看第一批报告——尤其是 `check_title_sort` 是否安静（它依赖在宿主进程里 import 到 `webserver.utils.get_title_sort`；这条不成立时会退回近似实现，中文书会全量误报）；
- 封面检查依赖 `CoreAPI.calibre.cover()` 返回可解码的图片字节；
- 全库规模下的耗时与内存。

## 许可

GPL-3（见 `LICENSE`）。检查逻辑移植自 Grant Drake 的 Quality Check 插件，衍生作品同以 GPL-3 发布；
`qc/shim/` 中若干实现思路来自 PoxenStudio/mybooks（BSD 2-Clause），BSD-2 与 GPL-3 单向兼容。
