# Atomized Reference Finder — MVP 实施计划

更新日期：2026-11-19

正文深度更新：新研究的 What It Is 目标 30–50 英文词，说明已核实的作品形式、主题与背景；What to Borrow 目标 50–80 词，说明 2–3 个具体可迁移原则及应用方式；Why It Fits Your Brief 目标 40–60 词，连接用户的主题、目标、受众、风格或约束。通过现有同一次候选分析请求生成，模型字段描述与提示词共同约束内容层次；目标字数不作为硬性最小长度，证据不足时允许更短。卡片仍使用短预览，详情展示完整正文，已有研究不自动重写，不增加 LLM 请求次数。

输入简化：移除 Advanced Options 折叠栏，Sources 与 Reference Count 直接显示在 Describe your creative idea 主输入框下方。独立 Creative Constraints 输入栏已删除；所有创意、受众、形式、语气、偏好、预算及排除项均写入主输入，现有同一次 LLM 策略分析提取明确约束并保存到后端 Brief.constraints，继续用于搜索和制作可行性评分，不凭空补充限制，不增加单独调用。后端可选 constraints 字段继续保留以兼容 API，已有独立约束草稿在界面更新时并入主输入框，原始完整 Prompt 不改写。

来源覆盖与最终排序更新：用户勾选的每一类 Sources 至少安排一次检索，LLM 来源优先级只决定检索顺序；完成首轮类别覆盖后再安排可选的其他研究方向。每轮请求额度按所选类别数自动保证至少「每类一次检索 + 一次候选分析」，重试只能使用剩余额度，不能挤占后续类别；鉴权、额度或总超时中断时记录未检索类别及原因。最终对核验、去重后的候选作品按现有评分降序取 Reference Count 指定的前 N 个（最多 20），不再为来源、创作者、研究方向或角色多样性调换低分作品；有效候选不足时如实报告。进度明确区分所选类别数量、实际检索任务数量和当前阶段编号。

标题生成更新：点击 Find References 后，在现有 Brief 分析的同一次 LLM 请求中生成简短、完整的英文主题标题，通常 3–6 个词；保留关键主题、专有名称和作品类型，不再截取输入开头。标题随研究策略保存，并由 Recent Research 与结果标题共同使用；重开、刷新及查看详情不额外调用 LLM，人工重命名优先保留。

缩略图修复：文章和作品页优先复用已验证、唯一关联的 YouTube/Vimeo 内嵌视频官方封面；保留 Vimeo 访问 hash 并支持隐私增强版 YouTube 嵌入地址，多视频页面不随意挑选封面。旧失败缓存自动失效，临时失败采用短缓存并允许后续浏览交互重试；按结果数分配预览整批时间预算，避免后排卡片无剩余时间。原有网页图片许可、robots、公开地址与图片校验继续保留，未获许可或接口受限时使用占位图，在详情中说明原因；缩略图流程不调用 OpenAI，PPTX 继续复用卡片缓存。

正文图片兜底：没有可用封面时，按正文顺序使用第一张获准使用且尺寸合适的图片；优先 article/main 区域，支持普通 src、懒加载属性与 srcset，跳过头像、标识、隐藏内容、追踪像素及不安全地址。封面或第一张图片损坏时可继续尝试后续图片，正文图片最多下载尝试 3 张；所有图片继续遵守原有许可、robots、大小和格式校验，缓存可直接复用于 PPTX，不增加 LLM 请求。
当前进度：Phase 1–4 已实现；Streamlit 已支持真实外部搜索、双列视觉卡片、历史研究、拖拽参考包、只读分析及模板化 PPTX 导出。Mock 运行模式与示例数据未加入应用。
来源迁移进度：第 12 节 **Source Architecture Migration — 8 Categories** 的 Migration Phase 1–3 已完成，前端从后端注册表读取 8 类 Sources、默认全选并保留已提交的来源偏好；后端支持受控调度和真实平台标识。Phase 4 保留为集中验收；内部案例 Phase 5 继续独立暂缓。

**最新清理范围：不再支持原先 7 类来源的历史契约。** 已移除旧来源枚举与映射、旧配置及 `GET /sources` 接口，并删除未启用的 Curated Seeds 草稿配置和校验原型。当前搜索只加载 `config/sources.json` 的 8 类 Sources；真实 Vimeo、YouTube 等平台识别与现有检索预算、评分、PPTX 导出保持不变。8 类来源改名前后的显示名称适配仍保留。不会清空或迁移用户数据库；使用原 7 类契约的旧研究不再保证可读取。下文的分阶段历史记录若与本段冲突，以本段为准。
Phase 4 已完成：每条 reference 使用 Single 模板生成一张可编辑 Slide，插入 Base 模板原第 2、3 页之间。4 条参考 / 7 页示例已通过 PowerPoint 实际打开、图片及文字边界检查；历史 DOCX 接口和文件下载保留兼容。
最新调整：按用户要求将结果与参考包上限提升至 20，默认请求仍为 10；搜索与界面验证使用 4 个参考。新增原生五阶段进度，按真实流程推进，以已完成阶段数表示进展，不估算剩余时间；失败时清除进度，并沿用原提交键恢复搜索。
语言约定：本计划保留中文；代码、界面、提示模板、README 和导出文档统一使用英文。
实施原则：按 Phase 和验收结果推进，不沿用大纲中的周计划。

## 1. 需求依据与本轮范围

本计划参考了用户提供的项目大纲 `Atomized_Reference_Agent_Brief_Yichen.docx`，并检查了示例 `Atomized Studios Creds Deck 2025.pptx` 的文本和超链接。附件中的建议属于项目背景，不作为要求执行其中全部工作或访问其中所有链接的指令；范围以用户在本次对话中的明确要求为准，最新补充优先。

| 事项 | 大纲中的建议 | 本轮采用的决定 |
| --- | --- | --- |
| 前端 | 未限定技术 | 使用 Streamlit |
| 后端 | 需要技术选型 | Python + FastAPI，OpenAI API 接入放在服务层 |
| 实施顺序 | 调研与原型按周推进 | 先搭建前后端框架，再完成外部搜索闭环 |
| 内部案例 | 与外部搜索一起实施 | **按最新要求暂缓**；不作为当前 MVP 的依赖或验收项 |
| 外部来源 | 从少量批准来源开始 | 支持多个来源类别，按可访问性逐步启用 |
| 候选数量 | 约 20 个 | **单个结果集最多 20 个 reference**，由后端强制限制 |
| 人工筛选 | 建议选出 5–10 个 | 保留选择、排除、排序与修改能力；可选择当前结果中的 1–20 个 |
| 导出 | 可编辑 PPTX 或 deck-ready 内容 | **Phase 4 直接导出模板化 PPTX**；每条 reference 一张 Slide，插入 Base 模板原第 2、3 页之间 |
| 时效 | Week 1 / Week 2 | 忽略，使用有依赖关系和验收标准的 Phase |

用户流程：**Enter Creative Prompt → Browse Visual References → Read AI Analysis → Add to Pack → Drag to Reorder → Export Reference Pack。** 外部搜索策略、证据处理与排序在后台执行，每个结果集不超过 20 个。

内部案例后续实施时以公司提供的 Case Study Deck 为主；当前只预留统一数据结构，不建设上传、解析、内部索引或内部检索功能。

## 2. 对项目大纲的分析

### 2.1 产品真正需要解决的问题

用户输入的通常是一条创意路线，而不是准确的作品名称。系统需要把抽象描述转换为可检索的创意维度，找到能说明具体创意原则的作品，并解释它如何帮助当前方案。

一个有用的 reference 应当回答：

1. 这是什么作品，来源是什么？
2. 它展示了什么具体的叙事、视觉或制作方法？
3. 当前项目可以借鉴哪一点？
4. 这一点为什么适合当前 Brief、受众或交付形式？
5. 已知信息有哪些证据，还有哪些内容需要人工确认？

因此，搜索质量、来源真实性、推荐理由和人工筛选是 MVP 的核心；自动排版 PPTX、视频下载与复杂 Agent 编排不应挤占第一阶段的实现。

### 2.2 MVP 的关键难点

- **创意理解：** 不能只把用户输入变成一条关键词查询，需要拆分 tone、camera、edit、performance、design、sound、format、technique 等维度。
- **来源覆盖：** 各平台的公开索引、页面访问、嵌入和 API 条件不同，不能把支持某个平台等同于保证检索其全部内容。
- **证据边界：** 搜索摘要和作品介绍可以支持部分判断，不能据此声称已观看影片，或编造镜头、剪辑、音乐、奖项和传播数据。
- **结果差异：** 15 个高度相似的作品价值有限，需要兼顾直接匹配、制作方法、跨领域启发和不同执行规模。
- **交付排版：** 直接填充指定 PPTX 模板，并保留可编辑正文、原始链接、来源与待核实信息。

### 2.3 当前不纳入的工作

- 内部 Case Study Deck 导入、OCR、图片理解、内部案例库和内部检索。
- 指定两个模板以外的任意 PPTX 模板适配、自由版式生成与自动生成新设计。
- 自动下载完整视频、绕过登录或访问限制、批量抓取受限平台。
- 自动生成 moodboard、style frame 或参考视频。
- 精确预算、排期报价和自动生产方案。
- 根据少量反馈自动训练模型或宣称系统已学习公司审美。
- 第一版即建设 Redis、Celery、独立向量数据库、多租户或企业 SSO。

## 3. 技术框架与边界

### 3.1 推荐架构

```text
用户浏览器
    ↓
Streamlit 前端
    ↓ HTTP / JSON
FastAPI 后端
    ├── Brief 与搜索策略服务
    ├── 外部搜索 Provider → OpenAI Responses API / Web Search
    ├── 来源核验、去重、排序与注释服务
    ├── 人工选择与编辑保存服务
    └── 导出服务：现有 DOCX；Phase 4 新增指定模板的 PPTX
             ↓
       SQLite + 本地工作目录
```

Streamlit 负责输入和交互；FastAPI 负责数据校验、任务状态、OpenAI 调用、搜索、结果数量限制和导出。明确分层有利于独立调试，也便于以后替换前端或增加内部检索。

MVP 采用两个本地进程，默认仅监听 `127.0.0.1`。用户通过 Streamlit 使用应用，前端服务调用后端；部署到共享网络前再补充身份验证和访问控制。

### 3.2 技术组成

| 层级 | 选择 | 用途 |
| --- | --- | --- |
| 前端 | Streamlit | Brief 表单、状态展示、结果选择与编辑、文件下载 |
| 后端 | FastAPI + Uvicorn | 统一 HTTP 接口和任务编排 |
| 数据结构 | Pydantic | 请求、模型输出和结果数量校验 |
| 模型接入 | OpenAI Python SDK / Responses API | Brief 分析、搜索、证据整理与推荐文案 |
| 搜索 | 优先 OpenAI Web Search，封装可替换 Provider | 首版尽量复用用户可提供的 OpenAI 账号能力 |
| 页面核验 | HTTPX + 有界的公开网页读取 | 获取页面状态、标题和可见文字；不绕过平台限制 |
| 数据保存 | SQLite + JSON 字段 | 保存运行记录、证据、结果、选择与编辑 |
| 导出 | lxml + Pillow（复用已锁定依赖）；python-docx 保留兼容 | 主交付为模板化 PPTX；不要求运行机器安装 PowerPoint，PowerPoint 仅用于可选视觉验收 |
| 配置 | 环境变量 / `.env` 示例 | API Key、模型、超时、搜索与数量上限 |
| 验证 | pytest + FastAPI 测试客户端 | 覆盖数量上限、证据引用、异常降级和导出等关键行为 |

使用 OpenAI 的结构化输出约束 Brief、策略和注释对象，并在后端再次校验。检索与最终结构化整理拆成两个步骤，避免把某个模型支持结构化输出误认为它必然支持所有搜索工具组合。模型名称通过配置提供，在真实接入时验证账号权限和工具兼容性。[OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)

Web Search 支持来源信息和域名过滤，可用于受控的多来源查询。实际可用性取决于用户账号、所选模型及工具权限；只有基础文本模型调用成功，不能视为外部搜索已接通。[OpenAI Web Search](https://developers.openai.com/api/docs/guides/tools-web-search)

### 3.3 运行模式与配置

- 当前仅支持 `live`：调用真实 OpenAI API 和搜索工具；缺少 Key、模型权限或搜索能力时明确返回错误，不返回虚构结果。
- Mock Provider 和运行时示例文件已删除。为兼容旧记录，保留原有元数据字段；旧演示记录不进入近期真实研究列表，且不能再次用于真实搜索或导出。
- Key 仅由后端读取，不保存到 SQLite、日志、导出文件或代码仓库；仓库只提交 `.env.example`。
- 初始配置包含 `OPENAI_API_KEY`、`OPENAI_MODEL`、`OPENAI_SEARCH_MODEL`、`SEARCH_PROVIDER`、`APP_MODE`、`BACKEND_URL`、超时和调用次数上限。
- `MAX_REFERENCES=20` 为不可被客户端调高的服务端硬上限；用户可请求更少结果，默认建议 10 个。
- 第一版的模型和工具版本在实施时验证并固定依赖，不在此计划中假定具体模型型号或 API 价格。

## 4. 前端页面与用户流程

### 4.1 Brief 输入

首屏直接显示 Describe your creative idea、Sources、Reference Count 和 Find References，不使用 Advanced Options 折叠栏。创意、约束、受众、形式、语气和目的统一由现有模型从完整输入理解，不再逐项要求填写。下面的可选 Brief 字段继续保留在后端契约中，兼容 API 使用方。

| 字段 | 要求 | 说明 |
| --- | --- | --- |
| 创意路线 / 核心概念 | 必填 | 要做什么，核心想法是什么 |
| 用途 | 可选 | Pitch、treatment、视觉方向、制作方法研究等 |
| 受众与传播目标 | 可选 | 面向谁，希望产生什么感受或行动 |
| 形式与平台 | 可选 | Film、social、animation、experiential 等 |
| 风格与创意维度 | 可选 | 幽默、情绪、摄影、剪辑、声音、设计等 |
| 制作约束 | 可选 | 预算区间、时长、地点、技术、交付比例等 |
| 已知参考 / 排除项 | 可选 | 已喜欢的作品、要避开的类型或来源 |
| 来源偏好与结果数量 | 可选 | 多选来源类别；结果数量 1–20 |
| 输出语言 | 固定 | 按最新要求仅使用英文 |

默认自动生成策略并立即搜索；主用户流程移除手动 Search Strategy 编辑。新搜索统一通过侧栏 New Research 进入，结果页不再重复放置表单；旧研究的会话草稿继续保留，提交响应丢失时使用原有幂等键恢复。

### 4.2 搜索与结果

- 默认仅显示易懂的搜索进度；技术诊断与 API 配置不进入主界面，通过后端 `/health`、研究 JSON 和 `/docs` 独立检查。侧栏不显示重复的 Live search 标签。
- 保留三面板：左侧研究导航、中央 Reference Results、右侧 Your Pack；主工作区采用 70/30，为双列卡片和拖拽列表提供空间。右侧尽量固定在可见区域，较长参考包可独立滚动。
- 视口宽度不超过 1100px 时改为上下堆叠，提供 View Your Pack 跳转入口。
- 卡片展示可用且通过技术校验的缩略图、标题、来源/创意类别、最多两句的已有作品简介（复用 what_it_is，不展示字段标签）、原始链接、Add to Pack 和 View Details；旧记录缺少简介时显示中性提示，不生成额外内容。What to Borrow 保留在只读详情中。
- 首页、活动/已保存研究及搜索进度页统一显示 Reference Finder 和固定英文副标题。卡片底栏按参考图加入淡分隔线，使用比例列：左侧加宽的描边 Details、居中的轻量 Link、最右侧 40×40 加号按钮，保持同一基线，并预留 Link 的最小宽度以避免截断；选中后显示高亮勾号，再次点击移出参考包，沿用原有选择回调、排序和注释。
- 底栏使用明确的 CSS Grid 约束，Details 占据过半宽度，右侧图标与边缘对齐，统一可见文字行高；较窄的结果区域改为单列卡片，避免控制项挤压。样式移至 `frontend/workspace.css` 并在每次完整重运行时读取，解决已运行服务仍缓存旧等宽样式的问题。图标隐藏标签整体移出布局，并清除默认间距，修复加号左偏 4px 的问题；加号和勾号均已测量确认水平、垂直居中。
- Reference Details 为只读报告，依次只展示 What It Is、What to Borrow、Why It Fits Your Brief，支持加入或移出参考包。Source & Rights 默认折叠，保留证据、来源限制、核验及使用权说明。Production Considerations 与 Recommendation details and assumptions 不再出现在详情 UI，后端制作说明、评分、假设、保存记录及 PPTX 导出数据保留。打开详情使用已加载的数据，不追加后端、缩略图或 OpenAI 请求。
- 自动从官方 YouTube/Vimeo oEmbed 获取封面；网页依次尝试单个关联视频封面、og:image、首张合适的正文图片，不再以图片许可元数据或管理员域名白名单作为提取条件，已有许可和来源信息继续保留。支持 JPEG、PNG、WebP、GIF、AVIF；原图下载上限为 10 MB、1200 万像素，预览最长边不超过 1280 像素、文件不超过 500 KB，较大图片在后端缩放压缩为 WebP，卡片和 PPTX 共用缓存，导出时转换为 PNG。允许尝试 Instagram、Pinterest 等页面无需登录即可返回的图片元数据，不绕过真实的登录或 HTTP 访问限制。保留 URL、DNS、重定向、robots、图片类型、像素及大小检查，不伪造封面。升级预览缓存版本以重试旧的缺图结果，已有预览在本地压缩迁移，保留来源信息及参考包草稿。所有预览为 16:9，缺失时使用干净占位图。
- 侧栏以可点击标题直接激活研究，高亮当前研究。3–6 词标题从原始 prompt 提炼后单独持久化，不为命名增加模型请求，不覆盖历史研究内容。
- 每条研究右侧提供三点菜单：Rename 直接持久化人工标题，不更改原始 prompt、结果或参考包；Delete 先弹窗确认。删除采用独立软删除操作，保留原记录、选择及文件，但从历史隐藏并禁止原研究链接、保存/导出及关联文件下载。删除当前研究后返回新研究页，取消或请求失败时保留草稿；正在运行的任务完成后才能删除。
- Your Pack 使用离线 Streamlit 原生组件，按唯一 reference ID 拖拽排序；每行含拖拽柄、标题和移除操作，支持键盘和触控。移除 Up / Down 按钮，检查事件草稿版本及 ID 集合；不同研究之间切换时保留本会话选择、顺序和历史注释。
- Export Reference Pack 自动保存最新选择、顺序和注释，再按该版本生成 PPTX，不要求手动保存；保留选择版本校验、冲突处理和历史 DOCX 下载兼容性。旧会话里的 DOCX 下载缓存不会被错误标记为 PPTX。
- 数量不足时返回已有的合格结果，并说明来源覆盖和证据缺口；不为凑足 20 个补入低质量作品。

前端使用 Session State 保存当前交互状态；持久化记录以 SQLite 为准，避免刷新或重新连接后丢失已保存的选择。Session State 用于同一会话的脚本重运行状态管理，不承担跨会话持久化。[Streamlit Session State](https://docs.streamlit.io/develop/api-reference/caching-and-state/st.session_state)

## 5. 外部搜索设计

### 5.1 来源覆盖策略

下表为迁移前 7 个来源的历史记录，仅供理解早期设计。当前搜索已使用第 12 节的 8 类注册表，旧配置与旧契约兼容层已移除。

| 来源类别 | MVP 检索方式 | 限制与处理 |
| --- | --- | --- |
| Vimeo | 公开作品页、创作者页、域名限定查询 | 私密或未收录作品可能不可访问；保留必要的访问 hash |
| YouTube | 公开作品、频道、品牌或制作方原始发布 | 优先原始发布，减少转载；不把摘要当成已观看视频 |
| Instagram | 公开可检索的作品/Reel 页面，结合制作方项目页 | 登录或索引受限时提示覆盖不足，不保证站内完整检索 |
| Pinterest | 公开 Pin、来源页面与风格线索 | 尽量追溯原创作品；来源不明时降低可信度 |
| Studio portfolios | 官方项目页、导演和设计工作室作品集 | 优先单个作品页，记录署名与项目描述 |
| Production company sites | 制作公司、导演、品牌作品页 | 优先具体项目及可核对的制作信息 |
| Award archives | 可公开检索的官方获奖项目页 | 奖项、年份、类别必须有对应证据；不能只依据宣传性描述 |

以上是来源类别覆盖目标，不意味着首版拥有各平台的专用 API。MVP 优先实现通用 Web Search + 来源类别/域名策略；YouTube 等专用 API 以后在覆盖不足且有账号权限时补充。

来源列表通过配置扩展。来源可靠性优先考虑原作者、品牌、制作公司、工作室和官方奖项页面，不把来源数量本身当作质量指标。

### 5.2 搜索管线

1. **理解 Brief：** 生成结构化摘要、显式约束和待确认假设。
2. **拆分意图：** 形成约 3–5 个互补搜索方向，例如情绪叙事、剪辑节奏、技术方法、社交形式和跨类别启发。
3. **生成查询：** 每个方向产生少量关键词和来源限定查询，必要时使用英文搜索，并明确查询目的。
4. **发现候选：** 从搜索工具返回的真实 URL、来源信息和摘要建立后台候选池；初始后台去重候选池上限建议 30 个，并限制工具调用次数。
5. **补充证据：** 对优先候选读取公开页面；保留原始来源、证据文本、访问状态和采集时间。
6. **去重与核验：** 合并同一作品的不同平台页面、转载和参数变体，保留证据更完整的来源及备用链接。
7. **评分与多样性选择：** 先过真实性和相关性门槛，再综合排序，避免同一品牌、制作方或视觉手法占满结果。
8. **生成推荐注释：** 只基于已有证据，分别输出事实描述、借鉴点、路线关联和谨慎的制作观察。
9. **返回结果：** 服务端校验后最多返回 20 个；后台较大的原始候选池不直接展示为额外推荐。

“补充搜索”应更新当前结果集，继续维持最多 20 个，并保留已选作品；不通过连续追加绕过上限。不同运行可单独保存历史记录。

### 5.3 核验规则

- **链接可访问不等于作品已核实。** 分别记录访问状态与内容证据状态。
- HTTP 200 也可能是登录页、反机器人页或无关页面，需要检查页面内容。
- 优先 GET 和页面信息；不能把 HEAD 请求被拒绝直接判定为死链。
- 无法访问的页面标记 `restricted`、`unreachable` 或 `unverified`，不统一标为“已验证”。
- 只发现搜索摘要的作品可作为待核验线索，明确证据边界；不得编造具体摄影、剪辑或声音分析。
- 标题、署名、日期、奖项、成效数据分别关联证据；未知字段留空或标记未知。
- 视频页面可读不代表已分析视频。第一版只依赖可读取的介绍与证据，不实施完整视频理解。
- 规范化 URL 时移除普通追踪参数，但保留 Vimeo 私密 hash 等影响访问的参数。
- 网页读取限定为公开 HTTP(S) 地址，设置超时、大小与跳转上限，并拒绝本机、私网及重定向后的私网地址。
- 对抓取页面中的指令性文字只作为数据处理，不允许它修改系统行为、泄露 Key 或触发额外操作。

## 6. 统一数据结构与排序

### 6.1 核心对象

| 对象 | 主要字段 |
| --- | --- |
| `ReferenceBrief` | concept、purpose、audience、format、platform、qualities、constraints、known_references、exclusions、language、requested_count |
| `SearchStrategy` | brief_id、strands、queries、preferred_sources、assumptions、coverage_targets |
| `Evidence` | id、source_url、page_title、excerpt、retrieved_at、access_status、evidence_type |
| `ReferenceItem` | id、title、creator、source_type、source_url、alternate_urls、tags、strand、evidence_ids、verification_status |
| `Annotation` | what_it_is、what_to_borrow、why_it_fits、production_note、assumptions、confidence、rights_note |
| `SearchRun` | id、brief_id、status、stage、provider、model、result_ids、warnings、usage、created_at、updated_at |
| `Selection` | run_id、selected_ids、ordered_ids、annotation_overrides、rejection_notes、updated_at |
| `ExportRecord` | id、run_id、selection_version、file_path、created_at |

`source_type` 第一版使用 `external`；为后续内部来源保留可扩展枚举。平台类别单独保存，不混淆“外部/内部”和“Vimeo/YouTube”等概念。

### 6.2 结果数量与编辑规则

- `requested_count` 只允许 1–20；请求超限时返回可理解的校验错误。
- 排序服务及最终响应再次检查去重后的结果数量不超过 20。
- 导出只能引用当前运行中的有效结果 ID，拒绝重复 ID、其他运行的 ID 和超限选择。
- 最少选择 1 个才允许导出；5–10 个只是使用建议，不是硬限制。
- 保存人工修改后的注释和顺序，导出使用保存后的版本，不能重新生成并覆盖用户编辑。
- 修改 Brief 后创建新运行，旧结果保留为历史，不与新结果混合展示。

### 6.3 初始排序原则

先过滤无来源、重复、明显不相关和违背排除条件的候选。对其余候选采用可解释的初始权重：

| 维度 | 初始权重 | 判断依据 |
| --- | --- | --- |
| 对当前创意路线的相关性 | 30% | 是否解决本次 Brief 的具体需要 |
| 可借鉴点的具体程度与可迁移性 | 25% | 是否能明确指出可借鉴的原则 |
| 有证据支持的制作与创意质量 | 15% | 作品介绍、制作说明、官方评价等；缺证据时降低置信度 |
| 来源与身份可信度 | 20% | 原始来源、署名、可读取的证据 |
| 与制作约束的适配程度 | 10% | 是否适合已有条件，或明确标记为高目标启发 |

这是待实际 Brief 测试后调整的产品启发式，不是客观评分标准。多样性在最终组合时额外处理；获奖和流行度不能替代与 Brief 的相关性。制作提示只提供复杂度驱动因素和简化思路，不给出无依据的报价。

## 7. 后端接口与任务运行

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/health` | 服务、运行模式和配置是否就绪；不返回密钥 |
| GET | `/source-categories` | 8 类元数据，按显示顺序返回；前端 Sources 已接入，默认全部选中 |
| POST | `/briefs` | 校验并保存 Brief |
| POST | `/briefs/{brief_id}/strategy` | 生成可编辑的外部搜索策略 |
| POST | `/search-runs` | 提交 Brief 和确认后的策略，返回运行 ID |
| GET | `/search-runs/{run_id}` | 获取状态、阶段、结果、覆盖说明和警告 |
| PUT | `/search-runs/{run_id}/selection` | 保存最终选择、顺序和人工编辑 |
| POST | `/search-runs/{run_id}/exports/docx` | 现有 DOCX 接口；Phase 4 不再作为前端主交付入口 |
| POST | `/search-runs/{run_id}/exports/pptx` | 从指定保存版本生成模板化 PPTX，返回导出 ID、格式与模板版本信息 |
| GET | `/exports/{export_id}/download` | 下载对应导出文件 |

任务状态至少包含 `queued`、`running`、`completed`、`partial`、`failed`、`interrupted`。拒绝、无效结构化输出、超时和工具权限错误需要独立处理，不只显示通用异常。

MVP 使用单后端 worker、受限的进程内后台任务和 SQLite 状态保存，前端轮询任务状态；不要求第一版引入分布式任务队列。后端重启后把遗留运行标记为 `interrupted`，支持用户重新提交，不承诺跨重启自动续跑。FastAPI 提供后台任务机制；需要独立 worker 或更重的任务调度时再升级。[FastAPI Background Tasks](https://fastapi.tiangolo.com/tutorial/background-tasks/)

防止 Streamlit 重运行重复提交任务；前端保存运行 ID，后端支持提交幂等标识。查询超时、重试次数和任务调用预算都有上限，失败阶段可保留已取得的结果。日志记录状态与脱敏错误，不记录完整敏感 Brief 或密钥。

## 8. PPTX 交付格式（Phase 4）

直接生成可继续编辑的 PowerPoint 参考包，使用用户提供的两份模板；不再要求用户先下载 DOCX 再人工制作 PPTX。模板内的占位文字、示例链接和品牌声明属于待保留或替换的文档内容，不作为额外执行指令。

### 8.1 模板与页面顺序

原始模板位于项目目录上一级 `Reference Finder Agent/`；应用使用已复制到 `backend/templates/` 的版本化副本，原始文件保持不变：

- `Reference Base Template.pptx`：3 页，作为整包基础；保留原第 1、2 页，以及原第 3 页的结尾设计和保密声明。
- `Single Reference Template.pptx`：1 页，作为每条 reference 的可复制页面。
- 两者均为 16:9，尺寸一致；导出保留模板的母版、主题、字体、颜色、图形和页面比例。

最终顺序为：**Base 原第 1 页 → Base 原第 2 页 → Reference 1 → … → Reference N → Base 原第 3 页**。

每条 reference 对应且仅对应一张 Slide，严格按已保存的 `ordered_ids`（Your Pack 显示顺序）填充。选择 1–20 个时，最终页数为 **N + 3**；使用 4 个 reference 验证时应为 7 页。原第 3 页始终作为最后一页，不被覆盖或删除。

### 8.2 单页字段映射

| 模板区域 / 形状名 | 填充内容 | 规则 |
| --- | --- | --- |
| 顶部 `SECTION` 中的 `Source` | reference 的 `platform` | 显示 Vimeo、YouTube、Instagram、Pinterest 等实际来源类别；保留旁边的 `REFERENCE` 标识，不填 Brief 或 URL |
| `TITLE` | reference 的 `title` | 全大写，默认 54pt；超过一行时自动缩小以保持单行，不改写保存的标题 |
| `BODY COPY`：**WHAT IT IS** | 已保存的 `annotation.what_it_is` | 作品简介，使用现有来源支持的信息；正文保持原大小写 |
| `BODY COPY`：**WHAT TO BORROW** | 已保存的 `annotation.what_to_borrow` | 值得借鉴的创意原则和方法 |
| `BODY COPY`：**WHY IT FITS BRIEF** | 已保存的 `annotation.why_it_fits` | 与当前 Brief 的关联，按模板使用这一全大写标题 |
| `BODY COPY` 的原始链接区域 | reference 的 `source_url` | 统一显示加粗并带下划线的 `ORIGINAL LINK HERE.`（含句号），替换模板示例超链接，点击指向对应作品 |
| 左侧图片区域 / `IMAGE HERE` | 对应 Reference Card 已验证且已缓存的缩略图（若有） | 按 reference ID 复用同图并保持比例；无图或坏缓存时使用干净占位，不保留示例图片或 `IMAGE HERE` 文案 |
| `PAGE NUMBER` | 最终演示文稿中的页码 | 随插入顺序更新，不能全部复用 Single 模板的页码 |

正文只使用上述三段分析，保持英文、清晰分段和模板样式。已有保存版本中的人工注释覆盖优先；不重新调用 OpenAI 改写，不从缺失信息推断情节、角色、署名或制作细节。缺失正文使用中性提示，完整原始记录仍保留在数据库中。

制作提示、证据状态、使用权说明、reference ID 和来源 URL 等辅助信息放入各参考页的演讲者备注，不额外挤占三段正文或新增参考页。未知署名留空；核验不完整的信息不能被提升为已证实。图片的网页预览许可不能自动作为 PPTX 复制或分发许可。

### 8.3 模板合并与排版规则

- 从 `backend/templates/` 的版本化模板副本生成新文件，使用 `manifest.json` 核对版本与 SHA-256；不依赖上级目录或用户机器的绝对路径，不搬移或修改原模板。
- 按已确认的形状名定位填充区域，替换内容时保留文本段落和文字片段的字体、粗体、颜色、段落间距及对齐；不把整页转换为图片。
- 跨模板合并时保留并正确重映射页面、布局、母版、主题、图片及超链接等必要关系；不能只复制页面 XML。移除无关示例链接，不按每个 reference 重复打包整份 Single 模板的无关资源。
- 作品标题默认 54pt，宽度不足时自动缩小（最低 18pt）；仍超宽时直接在本地按完整单词截断并加省略号，再按模板尺寸校验，不调用 API、不读取或写入标题缓存。不改写原始标题，始终保持单行及原正文、图片位置。正文最低 9pt，三段标题保持 12pt；正文仍无法容纳时保留明确错误，不静默截断或拆页。
- 复用现有“保存选择 → 校验选择版本 → 生成文件”的安全流程，保留冲突处理与失败重试；导出失败不丢失已保存的选择、注释或历史文件。
- 输出扩展名为 `.pptx`，下载类型为 `application/vnd.openxmlformats-officedocument.presentationml.presentation`。前端主按钮保持 Export Reference Pack，成功后提供 Download Reference Pack (.pptx)；历史 DOCX 文件仍可保留读取。

## 9. 实施 Phase

### Phase 1 — 优先搭建前后端框架

目标：先让 Streamlit → FastAPI → 服务层 → 结构化结果 → 最小 DOCX 的链路可运行。

- [x] 建立项目目录、依赖配置、`.gitignore`、`.env.example` 和启动说明。
- [x] 搭建 FastAPI 应用、健康检查、配置读取和统一错误响应。
- [x] 定义 Brief、策略、reference、任务和选择的 Pydantic 模型。
- [x] 搭建 Streamlit Brief 表单、结果区域、状态展示和下载入口。
- [x] 封装前端 HTTP 客户端，避免页面代码直接调用 OpenAI。
- [x] 跑通最多 20 个结果的显示、选择、顺序保存与最小 DOCX 下载；初期调试用 Mock 已按最新用户要求移除。
- [x] 建立 SQLite 初始化和运行记录，处理前端重运行与任务 ID。
- [x] 创建 OpenAI Provider 接口与显式连接检查入口，并完成模拟成功与失败场景测试；Phase 2 已补充真实模型与搜索工具验证。

Phase 1 验证结果：16 项测试通过，包含 API 流程、结果数量限制、重复提交、选择与注释保存、DOCX 内容与超链接、后端重启恢复和 Streamlit 交互。代码检查、格式检查及依赖一致性检查通过。前后端已实际启动，健康检查均返回 HTTP 200；真实账号调用由后续 Phase 2 完成。

启动命令：`.\.venv\Scripts\python.exe run.py`。界面默认位于 `http://127.0.0.1:8501`，API 文档位于 `http://127.0.0.1:8000/docs`。详细英文说明见 `README.md`。

Windows 点击启动：双击根目录 `Start Reference Finder.bat`，等待前后端就绪后自动打开浏览器；使用期间保留启动窗口，通过 Ctrl+C 停止服务。

当前验收约定：使用真实 API Key 运行；前后端独立启动，数量上限和错误提示均生效。自动化测试使用测试专用 Provider，不作为应用运行模式。

### Phase 2 — 接通真实外部多来源搜索

依赖：Phase 1 的 API 契约和 Provider 接口。

- [x] 实现 Brief 分析、互补搜索维度与可编辑策略。
- [x] 接入 OpenAI Web Search，验证账号、模型与工具权限。
- [x] 实现来源类别配置与域名限定查询，覆盖公开视频平台、作品集、制作公司与官方奖项页面。
- [x] 对 Instagram、Pinterest 等来源展示实际覆盖限制，而不是宣称已完整支持。
- [x] 保存真实来源 URL、搜索证据、采集时间和使用量。
- [x] 实现超时、有限重试、调用预算和部分成功的返回。
- [x] 无搜索权限时明确返回错误，不用纯模型生成的链接代替；当前账号已通过真实验证，因此无需接入替代搜索服务。

Phase 2 实施结果：

- 策略与结构化整理默认使用 `gpt-4.1-mini`，联网发现使用 `gpt-5.4-mini`（low reasoning）；模型名保留在本地配置中，可随时更换。
- `config/sources.json` 管理七类来源，按原创作品页、视频平台、奖项档案、社交来源分组搜索。域名过滤用于固定平台，作品集和制作公司采用开放网络发现。
- 默认最多 4 组搜索、6 次搜索/整理 API 请求（含重试），后台来源池最多 30 条，单请求超时上限 75 秒，每次调用前检查 240 秒运行预算；Brief 策略生成独立计为模型请求。
- 推荐 URL 只能从已完成搜索的来源元数据与引文映射取得；整理模型只能引用证据 ID，不能自行提交新 URL。未知 ID、不匹配标题和不允许的来源类别会被剔除。
- Phase 2 证据明确标记为搜索生成摘要或仅咨询过的来源，不冒充独立网页核验。网页文本核验、排序与作品去重现已由 Phase 3 补齐；完整视频理解不在本轮 MVP 范围内。
- 流程已保存来源、时间、来源覆盖、API 请求数、已完成的工具调用数、输入/输出 token 和耗时；某来源失败时可返回部分结果。
- 最终自动化测试共 32 项通过，包含 Live 页面搜索、人工筛选与导出，以及部分失败、重试、调用预算、来源约束和旧功能回归。
- 公开 Brief 的真实四组搜索已完成，得到 3 个 reference 并生成 DOCX。验证记录位于 Git 忽略的 `data/live-verification/`；未打印或提交密钥。

本地 `.env` 已切换为 `APP_MODE=live`。双击启动方式保持不变；修改代码或配置后须重启旧启动窗口，重复双击只会打开已经运行的进程。

### UI 视觉浏览与参考包更新 — 已完成

本次以用户最新要求覆盖附件中保留 Mock 的条款，移除了演示运行模式与示例文件；真实搜索和现有 API 路径保留。

- [x] 首页保留标题、副标题、创意输入、直接可见的 Sources、Reference Count 和 Find References。
- [x] 默认自动生成策略并执行搜索；主流程不提供手动策略编辑，创意约束统一写入主输入框。
- [x] 结果保留侧栏、中央双列卡片和右侧参考包；只读 Reference Details 按需打开，窄屏自动堆叠。
- [x] 自动获取官方授权元数据中的真实封面；网站图片需额外使用依据，缺失则显示统一占位图。
- [x] 可点击的历史标题持久化，当前研究高亮；保留历史记录。
- [x] Your Pack 拖拽排序并在每行移除，使用唯一 ID 和草稿版本保护选择。
- [x] Export Reference Pack 自动保存最新选择及注释，再按保存版本生成 DOCX；新增可选的导出版本校验参数，旧调用方式继续有效。
- [x] 技术诊断与用户界面分离，通过后端接口查看；侧栏保留研究导航。
- [x] 草稿与临时控件状态分开保存，支持切换研究后保留本会话编辑；已导出的选择可通过 URL 在新会话恢复。
- [x] 删除 Mock Provider 与运行时示例数据；保留旧记录读取兼容，旧演示记录只读且不能导出为真实参考包。

上一轮 UI 调整通过 105 项测试。本轮累计 131 项测试通过，新增标题持久化、官方封面解析、网站图片许可、缓存、危险重定向和拖拽事件校验。Chrome 在 1440、1200、1024、768、390px 检查双列与堆叠布局；真实鼠标拖拽、同名条目排序、行内移除、历史切换、详情零额外 API 请求及 DOCX 正文顺序均已通过。YouTube 和 Vimeo 各一个公开示例的官方元数据与真实 JPEG/WebP 封面已联网验证成功，未使用 OpenAI Key。

浏览器验收脚本为 `scripts/verify_ui.py`，使用独立数据库和明确标注的测试预览，不改写用户研究、不消耗 OpenAI 额度；截图位于 Git 忽略的 `data/ui-review/`。代码与格式检查通过。未新增排序包；使用锁定的 Streamlit 1.67 原生组件，并明确声明已有 Pillow 图片验证依赖。后端仅增添独立展示缓存表及 `/search-runs/{id}/presentation`，历史列表追加标题字段；搜索提示、检索流程、排序、研究数据契约和安全导出流程不变。

拖拽修复：连续移动条目时，原绑定在移动节点上的指针捕获可能丢失，导致松手顺序无法提交。已改为固定列表容器捕获，支持拖拽柄及标题区域、容器外松手、触控与键盘；取消操作恢复原序。浏览器验收现在使用分步连续移动，并等待会话草稿版本确认后检查顺序，避免只观察临时 DOM 顺序。组件资源按内容版本注册，重运行后可加载更新并保留选择。131 项回归通过，当前运行服务已只读确认加载新组件。

本轮定向 UI 更新已加入全页面页头、对称三操作卡片，以及研究重命名/删除菜单。新增 PATCH `/search-runs/{id}/title` 与 DELETE `/search-runs/{id}`，软删除标记单独存储，不改写研究内容。已验证标题持久化、取消与确认删除、失败时保留草稿、关联文件保留及访问撤销；Chrome 实测桌面和窄屏按钮对齐、图标选择、菜单、拖拽与 DOCX 顺序。检索、AI 提示、模型和排序算法保持原样。

本轮最终 145 项测试通过，代码与格式检查通过；后端管理接口在重启服务后加载，重启前导出可保存当前会话草稿。

验收：使用一个真实 Brief 找到可追溯作品，展示多来源和多创意维度；无权限或来源不足时明确说明，不返回虚构链接。

### Phase 3 — 结果核验、推荐解释与人工筛选

依赖：Phase 2 的真实候选和证据。

- [x] 实现公开页面核验、登录墙识别、证据状态和 URL 安全检查。
- [x] 实现作品级去重，合并同一作品的多平台来源。
- [x] 实现可解释排序、多样性选择和服务端最多 20 个结果的最终校验。
- [x] 根据证据生成描述、借鉴点、路线关联与谨慎的制作提示。
- [x] 完成前端筛选、排除、顺序调整和注释修改，持久化人工选择。
- [x] 实现少于目标数量、所有来源失败和部分证据不足时的界面状态。

Phase 3 实施结果：

- 新增 `page_verification.py`，使用公开、无认证的 GET 读取页面文本。先检查 robots.txt，并遵守禁止访问与抓取间隔；登录墙、验证码、软 404 和 JavaScript 空壳不当作作品核验成功。
- 每次连接与重定向均校验协议、端口及所有 DNS 地址；拒绝私网、回环、保留和组播地址，实际连接已批准的 IP，同时保留原域名的 Host 和 TLS/SNI 证书检查，防止 DNS 重绑定。不转发 Key、Cookie 或认证头。
- 默认核验最多 18 个页面、60 次 HTTP 尝试，4 个并发任务；单次超时上限 8 秒、总核验窗口 45 秒、响应体上限 1 MB、最多 3 次重定向。robots 与重定向也占 HTTP 预算。
- 核验结果分别保存为可访问、受限、不可达、未核验或阻止。正文证据与搜索生成摘要分开保存；只有实际页面中可识别的作品才标记为 `supported`，不使用搜索标题冒充页面身份。
- 结合视频 ID、具体项目页面、嵌入链接及保守的标题/制作方比较合并作品，保留备用链接和证据。通用标题、不同制作方或明确不同年份不直接合并。
- 依据证据锚定的创意评分，按相关性 30%、具体性/迁移价值 25%、制作质量 15%、来源可信度 20%、制作约束适配 10% 排序，再组合不同创意维度、角色、制作方与来源。原创作品页的来源权重高于易转载平台，获奖和流行度不自动加分。
- 事实描述增加数字与奖项来源约束；不支持的事实、未知证据 ID 和不相关候选被剔除。制作提示明确为初步观察，证据不足时降低置信度，不输出无依据的制作报价。
- Reference Results 标题右侧的滑杆图标打开筛选弹层，保留来源和创意类别控件及其状态键；图标显示已选条件值的总数，卡片立即更新，Clear Filters 清空两组条件。筛选与清空不会改变参考包顺序、注释、排除记录或已准备的导出；桌面保留双列卡片，窄屏沿用响应式布局，无新增依赖或 CSS。证据、核验理由、排序依据及备用来源集中在 Reference Details 弹窗，并可排除候选；排除反馈随导出的参考包一同保存。
- DOCX 增加来源状态、排序理由和备用链接；原始网页文字核验不代表已观看完整视频、认证每项来源声明或取得素材使用权。
- 公开 Brief 的真实流程已核验 18 个候选，其中 15 个取得可读正文；最终 3 个推荐均有页面文字证据，并成功导出 DOCX。随后对最终事实与身份约束完成了样例回放检查。
- 自动化测试共 102 项通过，覆盖 DNS/IP 与重定向安全、robots、限流后停止同来源读取、超时与大小限制、作品身份、去重、多样性、评分、筛选/排除、注释保留以及原有搜索和导出功能。代码、格式及依赖检查通过，无新增依赖。

历史研究不静默改写；上述核验与排序应用于新搜索。旧记录仍保留当时的来源状态。

验收：用户能理解每条为何被推荐，辨认已证实信息和推测，并保存自己的最终选择；不能通过补搜或请求参数突破 20 个上限。

### Phase 4 — 指定模板的 PPTX 导出与 MVP 验证

依赖：Phase 3 已保存的选择、顺序及注释，以及已提供的 `Reference Base Template.pptx` 和 `Single Reference Template.pptx`。以第 8 节为交付契约；不修改搜索、核验或排名逻辑。

**步骤 1：模板接入与合并验证**

- [x] 将两份模板的版本化副本加入项目模板资源目录，检查 16:9 尺寸、3 页 Base / 1 页 Single、必需文字框、母版和 Safiro 系列字体样式。
- [x] 先用已保存的 4 个 reference 验证“Base 第 1–2 页 + 4 张 Single 页面 + Base 原第 3 页”的合并，共 7 页；所有参考页均插在原第 2、3 页之间。
- [x] 验证跨文件页面、母版、主题、图片、备注及链接关系正确，原模板保持只读，输出文件打开时不出现 PowerPoint 修复提示。

**步骤 2：逐条内容填充与版式**

- [x] 按 Your Pack 的 `ordered_ids` 为每个 reference 生成且仅生成一张 Single 页面，填入作品标题和顶部实际来源类别，例如 Vimeo / YouTube。
- [x] 作品标题默认 54pt，过长时自动缩小以保持单行；与正文子标题 **WHAT IT IS → WHAT TO BORROW → WHY IT FITS BRIEF** 均全大写。三段正文保持原大小写，原始链接为加粗并带下划线的 ORIGINAL LINK HERE.。保留人工注释覆盖，不新增 AI 调用或虚构事实。
- [x] 将模板的示例 YouTube 链接替换为该 reference 的原始来源链接；处理可导出图片、无图占位、页码及来源/证据/使用说明备注。
- [x] 检查长标题、长正文、缺失字段、自动换行和字体缩放；维持一条 reference 一页，并对无法容纳的条目给出明确反馈。

**步骤 3：接入现有保存与下载流程**

- [x] 实现 PPTX 导出服务和 `/search-runs/{run_id}/exports/pptx` 接口，输出文件记录关联研究 ID、保存版本及模板版本。
- [x] 前端 Export Reference Pack 切换为保存后生成并下载 `.pptx`；保留已有选择版本检查、并发冲突处理、草稿保存及失败重试，不再以 DOCX 作为主交付。
- [x] 保留历史研究、旧 DOCX 文件和下载记录的兼容，不把旧导出记录错误标记为 PPTX。

**步骤 4：验收与用户说明**

- [x] 以 4 个 reference 完成主要流程和实际 PowerPoint 视觉验收：总计 7 页，Base 原第 1、2 页和结尾原第 3 页完整，参考页顺序、来源、三段正文与已保存选择一致，原始链接可点击，正文可编辑。
- [x] 用离线数据验证 1 个 / 20 个 reference 的边界，对应 4 页 / 23 页；拒绝空选择、重复 ID、跨研究 ID、超限选择和过期保存版本，不为边界测试额外发起真实搜索。
- [x] 验证模板缺失、必需文字框缺失、资源关系损坏、长文字、无图片和导出失败的反馈；补充回归无 Key、错误 Key、搜索工具不可用、超时、来源受限、结果不足等已有流程。
- [x] 在 PowerPoint 中检查字体、加粗、颜色、行距、图片比例、页码、备注和可点击链接；确认不会出现文本溢出、样例残留、错链或意外增页。
- [x] 更新简明使用说明和一个 4 条参考的 PPTX 示例，记录模板版本及已知限制。

验收：用户完成外部搜索、人工选择及排序后，可直接下载基于两份指定模板生成的可编辑 PPTX；N 个 reference 对应 N 张参考页及总计 N + 3 页，全部插入 Base 原第 2、3 页之间，来源与三段正文正确且可追溯。DOCX 不再是 Phase 4 验收交付物。

实施与验证记录：

- 新增 `pptx_export.py` 和轻量 Open XML 包合并模块，复用已有 lxml、Pillow；跨页面共享必要母版与布局，避免每条参考重复打包 9MB 的 Single 模板。模板及清单随安装包打包，已验证解包后的代码可独立生成 PPTX。
- 新增 PPTX 接口、格式/模板版本/模板哈希记录及正确的下载 MIME；历史记录缺少 `format` 时按 DOCX 读取。保留保存版本检查、并发冲突、失败重试、软删除后撤销下载，以及原有草稿。
- 按后续要求，图片直接复用对应 Reference Card 已验证且已缓存的缩略图，填入左侧 `IMAGE HERE` 区域并保持比例，取消重复的独立导出域名名单；原有网页预览许可与来源检查不变，图片来源和权利说明保留在备注中。无图或坏缓存采用占位；导出不调用 OpenAI 或抓取新图片。
- 完整回归 174 项通过，随后新增的缺失正文中性回退和原始链接定向验证通过。覆盖 1 / 20 条参考的页数、20 条选择、模板缺失/字段缺失/资源损坏、长文溢出、历史 DOCX、缓存格式、版本冲突与删除等；代码、格式、依赖检查通过。
- Chrome 使用 4 条离线参考验证保存、拖拽、PPTX 实际顺序、过滤、历史及桌面/窄屏；没有修改用户研究或消耗 OpenAI 额度。
- `scripts/verify_pptx.py` 生成明确标注为测试数据的 4 条参考示例（7 页），含两张许可测试图片、无图页面和长标题；PowerPoint 已只读打开并渲染，正文及标题未超出文字框，原始链接和结尾页正确保留。样例与报告位于 `data/pptx-review/20261117T152636Z/`。
- 另从已有真实研究复制 4 条结果到独立验收数据库，未改动原研究、已选顺序或注释，也未重新搜索；生成的 7 页 PPTX 已通过 PowerPoint 文字边界与视觉检查，位于 `data/pptx-review/20261117T154031Z-saved-research/`。
- README 已切换为 PPTX 用户步骤；模板、图片许可和重复验收方法见 `docs/PPTX_EXPORT.md`。现有后台需重启以加载新增接口，导出前的选择保存仍可保留用户草稿。
- 卡片缩略图复用调整通过 42 项回归测试；4 条离线参考样例通过 PowerPoint 渲染，已验证图片区域、排序对应、缺图及坏图回退。仅重载后端并保留前端会话后，当前已保存 Pack 的 3 条参考均成功嵌入各自缩略图，选择、顺序和注释未改动；未调用 OpenAI。

### Phase 5 — 后续加入内部案例（不阻塞当前 MVP）

启动条件：用户补充并决定启用内部 Case Study Deck。

- [ ] 接入一个或多个公司 Deck，提取项目文本、链接、页码和文件版本。
- [ ] 先建立小规模人工可校正的案例库，再评估全文或语义检索。
- [ ] 合并重复项目和多页信息，剔除占位文字，核对项目与链接的对应关系。
- [ ] 区分 Atomized 自制案例和团队收藏的外部 reference。
- [ ] 将内部结果接入同一筛选和模板化 PPTX 流程；内外部总和仍不超过 20 个。

已检查的示例 Deck 共 83 页，存在重复案例页、`REPLACE WITH IMAGE/VIDEO` 占位文字、管理页链接和部分疑似错配链接，例如非 Studio Ulster 案例中复用了 Studio Ulster 地址。文件名中的 2025 也不能自动作为每个项目的年份。后续入库必须保留原文件与页码，允许人工修正；当前不据此创建内部数据集，也不把演示文稿中的奖项和成效描述当成已外部核实的事实。

### MVP 后可选增强

- 平台专用 API、授权元数据与更稳定的缩略图支持。
- 根据明确的选择/拒绝反馈调整可检查的偏好设置。
- 参考 URL 反向检索、补充维度与相似作品发现。
- 企业部署、身份验证、独立任务队列和多人协作。
- moodboard、更多 PPTX 模板及其他独立扩展能力。

## 10. 建议目录结构

Phase 1 已建立主要目录。来源配置、独立核验与排序模块按后续 Phase 逐步补充，不预先创建空子系统。实际结构和启动方式见 `README.md`；依赖锁定在 `requirements.lock`，本地双服务启动入口为 `run.py`。
Phase 4 已将两份 PPTX 模板及版本清单复制到 `backend/templates/` 并随包分发；原始文件仍保留在项目目录上一级。

```text
.
├── PLAN.md
├── README.md
├── pyproject.toml
├── requirements.lock
├── run.py
├── .env.example
├── .gitignore
├── config/                   # 随包分发的新注册表、精选入口与旧来源兼容配置
├── frontend/
│   ├── app.py
│   ├── api_client.py
├── backend/
│   ├── main.py
│   ├── core/                 # 配置、日志、异常
│   ├── schemas/              # Brief、策略、证据、结果、选择
│   ├── services/             # 搜索编排、核验、去重、排序、导出
│   ├── providers/            # OpenAI 与可替换搜索接口
│   ├── repositories/         # SQLite 持久化
│   └── templates/            # Base / Single PPTX 与版本清单
├── prompts/                  # 版本化提示模板
├── tests/                    # API、Provider 和 Streamlit 集成测试
└── data/                     # SQLite、运行记录与导出；不提交仓库
```

## 11. 完成标准与待接入信息

当前规划阶段已完成：

- [x] 阅读项目大纲并区分背景建议与用户本轮要求。
- [x] 检查示例 Deck 文本与链接，记录后续入库注意事项。
- [x] 按最新要求把内部案例移出当前 MVP。
- [x] 确定框架优先、外部搜索优先、最多 20 个结果的 Phase 路线；按最新要求将 Phase 4 主交付由 DOCX 调整为指定模板的 PPTX。
- [x] 检查用户提供的 Base / Single 模板，确认尺寸、页数、填充区域及第 2、3 页之间的插入规则。

当前 MVP 完成标准：

- [x] Streamlit 与 FastAPI 框架可独立启动，应用仅使用真实 API 搜索。
- [x] 一个真实 Brief 能生成多方向、多来源的外部搜索结果。
- [x] 推荐数和人工最终选择均不超过 20，通过作品去重减少重复名额，保留合并后的来源和证据。
- [x] 来源可追溯，页面文字与搜索摘要区分，事实描述与创意解读分开；无法核验的候选标记待复核。
- [x] 用户可保存选择、顺序和注释，刷新后能恢复已保存内容。
- [x] 历史 DOCX 已通过程序化内容、顺序、修改与超链接检查，真实来源示例也已导出；保留验证记录，不作为 Phase 4 的交付验收。
- [x] Phase 4 的模板化 PPTX 导出、4 条参考 / 7 页样例、字段映射、顺序、链接、可编辑文本及 PowerPoint 视觉验收完成。
- [x] Phase 1–3 已支持操作的关键失败路径有明确反馈，密钥和敏感配置不出现在输出中；真实项目体验与视觉验收在 Phase 4 完成。
- [x] 内部 Deck 和内部索引不作为当前 MVP 的前置条件；Phase 4 使用的两份 PPTX 模板已提供。

本地 OpenAI API Key 已配置，模型与 Web Search 工具权限已验证；无需把密钥写入此计划或发送到聊天。Phase 4 已完成模板化 PPTX 导出；近期可按第 12 节实施外部来源架构迁移。内部案例仍属于独立的 Phase 5，待用户补充并启用内部 Case Study Deck 后再接入，不阻塞当前外部搜索与 PPTX 交付。

## 12. Source Architecture Migration — 8 Categories

状态：**Migration Phase 1–3 已完成，Phase 4 待集中验收。** 以下 Migration Phase 1–4 是来源迁移的四个步骤，与前文已完成的 MVP Phase 1–4 区分。前后端已接入 8 类，保留旧类别请求映射及历史读取，不改写历史研究或迁移数据库。

**最新范围约束（优先于原附件及已完成阶段记录）：只做 8 类 Sources 的迁移，Curated Seeds 暂时不实施。** 本次交付包含类别配置、后端校验/必要调度适配、前端选项和历史兼容；采用满足迁移的最小修改。seed 加载、筛选、抓取、合集拆解、专用预算及相关测试都不是迁移前置条件或验收项，不自动并入后续 Phase。已有 seed 文件和校验原型只是暂存资料；Phase 2 已解除 `load_source_registry()` 对 `curated_seeds.json` 的强制依赖，文件缺失或损坏均不影响 8 类元数据及搜索。是否继续做 Curated Seeds 等用户以后明确安排。

### 12.1 目标与现状核对

目标：用 8 个研究类别替换 New Research → Sources 中现有的 7 个来源。所有类别默认选中，用户可以取消任意类别，包括 Podcasts & Interviews；选中集合定义允许的发现范围，不要求每次搜索访问其中每个网站，也不按类别平均分配最终参考数量。

迁移前核对的实现及迁移影响（Phase 1 落地情况见 12.4）：

| 现有位置 | 当前行为 | 迁移需要处理的问题 |
| --- | --- | --- |
| `config/sources.json` | 7 个来源，记录 group、domains、instruction、note | 更新为 8 类注册表；保留开放检索；不依赖精选入口配置 |
| `backend/services/source_policy.py` | 只按 `original_work → video → awards → social` 轮转 | 新增的 editorial、brand_films、interactive、podcast 不能继续被固定列表遗漏；按 Brief 相关性调度 |
| `backend/schemas/models.py` | Source 为 7 项 Literal；Brief、Strategy、Coverage 等有 `max_length=7`；结果的 platform 也使用 Source | 更新新请求与结构化输出的类别校验，同时保留旧枚举读取；区分发现类别与实际发布平台 |
| `backend/providers/openai_provider.py`、`prompts/strategy.txt` | 已有一次 Brief 策略生成，主要输出 strands 和 queries | 在现有策略调用内增加受约束的类别优先级，不新增单独的分类模型调用 |
| `backend/providers/discovery.py`、`prompts/search.txt`、`prompts/discovery.txt` | 域名过滤、证据收集、页面核验后整理候选 | 接入新类别与域名策略，保留原作链接与证据；不接入 seeds |
| `backend/services/ranking.py` | 依据来源身份与证据评分，执行作品去重和多样性选择 | 合并来源后仍保留对转载、原作、官方奖项的区分；不通过新类别调整评分权重 |
| `frontend/app.py` | SOURCES 再次硬编码 7 项；结果筛选使用已有 platform 值 | 新表单改用 8 类；已保存研究的筛选、控件状态和草稿不能因枚举替换失效 |
| `backend/services/thumbnails.py`、`backend/services/pptx_export.py` | 官方视频封面解析按 URL 识别，部分预览分支及导出来源使用 platform | 保留真实 YouTube/Vimeo 等平台显示、社交预览限制和卡片缩略图导出，不能把“Video Platforms”误当实际平台 |

保留 Streamlit + FastAPI、现有模型配置、搜索与页面读取预算、进度展示和最多 20 条的结果/选择限制。Cards、Recent Research、筛选弹层、Your Pack 拖拽及 PPTX 模板与版式不重设计；不恢复 Mock，不新增无必要的依赖或 SQL 表结构迁移。现有证据、安全检查、归属与权利说明继续有效。

### 12.2 新 Source Registry

以下名称、group 和 domains 已在 Phase 1 注册表中落地；按表格顺序提供元数据，后续新表单沿用此显示顺序，搜索优先级另由 Brief 决定。

| Category | Group | Domains |
| --- | --- | --- |
| Video Platforms | `video` | `youtube.com`, `youtu.be`, `vimeo.com` |
| Award Archives | `awards` | `canneslions.com`, `lovethework.com`, `dandad.org`, `theoneclub.org`, `clios.com`, `thefwa.com`, `nativeadvertisinginstitute.com` |
| Creative Press | `editorial` | `famouscampaigns.com`, `thedrum.com`, `creativereview.co.uk`, `creativeboom.com` |
| Brand Film Showcases | `brand_films` | `nowadays.tv`, `herenow.film`, `superside.com` |
| Studios & Agencies | `original_work` | `[]`，开放发现工作室、导演、代理商及制作公司的具体项目页 |
| Web & Interactive | `interactive` | `awwwards.com`, `siteinspire.com`, `webflow.com` |
| Podcasts & Interviews | `podcast` | `gq.com`, `gq-magazine.co.uk` |
| Social Platforms | `social` | `instagram.com`, `pinterest.com`, `pinterest.co.uk` |

配置仍保留 `instruction` 与 `note`：描述适合的作品、原作优先和访问限制。Podcasts & Interviews 中 GQ 为优先发现来源，不是每次查询的必搜项；未选该类别时，不安排其独立搜索或精选入口任务。

原 7 类来源的兼容映射已移除，平台名称不再作为搜索类别提交。新研究使用上表中的 8 类 Sources，结果中的 `platform` 仍按真实发布网站识别。保留的是这 8 类来源改名前后的显示名称适配，不再支持迁移前的 7 类历史契约。

### 12.3 Curated Discovery Seeds — 暂缓，草稿代码已清理

Curated Seeds 未进入运行流程，其草稿配置和校验原型现已删除。当前 8 类 Sources 直接按各自 domains、instruction、note 检索；不依赖精选入口、seed 抓取或单独的 seed 请求预算。如以后需要，另行安排设计与实施。

### 12.4 Migration Phase 1 — Configuration

**状态：已完成。影响文件：** `config/sources.json`、`config/curated_seeds.json`、`config/legacy_sources.json`、`config/__init__.py`、`backend/schemas/source_registry.py`、`backend/services/source_registry.py`、`backend/services/source_policy.py`、`backend/main.py`、`pyproject.toml`、`tests/test_source_registry.py` 及简明 README 说明。此阶段不会立即切换新搜索，后续阶段通过兼容层接入后再统一启用。

实施步骤（按最新范围调整；以下阶段记录保留原实施事实）：

1. 建立 8 类注册表，按 12.2 保留名称、group、domains，补充英文 instruction/note；用现有 JSON、Pydantic 或标准库加载，不引入注册中心或数据库。
2. 解除主注册表加载对 seed 文件和 seed 校验的强制依赖；8 类 Sources 单独可用。暂存的 12 个 URL 不参与本次搜索或发布门槛。
3. 建立同一注册表的加载与校验入口，检查类别名称唯一、group 可被调度、域名格式合法；明确 `domains=[]` 表示开放搜索，不能解释为“禁用该类别”。
4. 后端提供有序的可选类别元数据；实际采用新增只读 `GET /source-categories`，返回 name、group、domains、instruction、note、open_discovery 和 default_selected。已存在的 `GET /sources` 保持旧响应，前端后续接入新接口，避免再次维护一份独立的 8 项常量。新类别类型与注册表有一致性校验，现有搜索结构化输出继续使用旧契约，直到 Phase 2 切换。
5. 核对源码运行和安装包对 JSON 资源的加载路径；若当前打包方式不能携带 `config/`，在实施时补齐资源打包和解析，不让本地正常、安装后找不到配置。不得把现有 PPTX 模板或配置移出包后遗漏。

验收标准：

- [x] 注册表恰好含 8 类，名称、group、域名与 12.2 一致；不存在遗留类别成为第 9 个新选项。
- [x] 按最新范围解除 seed 文件依赖；缺失、禁用或不提供 `curated_seeds.json` 均不妨碍 8 类 Sources 加载与使用（由 Phase 2 完成）。
- [x] 开放检索与受限域名检索可明确区分；新注册表接受全部 8 个 group，不依赖旧四组调度白名单（智能调度仍在 Phase 2）。
- [x] 配置错误有明确反馈；源码和安装包均能读取配置，来源元数据不包含 Key 或敏感设置。

原阶段实施与验证记录（216 项测试及 seed 原型属于范围调整前的事实，不表示本次要求实施 seeds）：

- 新注册表使用现有 Pydantic 校验类别、group、域名和入口元数据，拒绝重复 JSON 键、重复域名/seed、未知类别、非法 URL 与缺失资源，返回 `source_configuration_invalid`；配置加载不解析 DNS、不抓取网站、不调用 OpenAI，运行时网络核验仍沿用后续原有流程。
- 当前 SourcePolicy 改从 `legacy_sources.json` 读取原始 7 类配置；历史枚举、Brief、策略、coverage、筛选状态和前端 Sources 均不改。8 类元数据默认全部选中，仅供后续接入，并不把 GQ 变为必搜项。
- `config/` 作为独立资源包随 wheel 分发，使用 `importlib.resources` 读取；JSON 不在构建时复制出另一份独立维护的配置。实际构建的 wheel 已在项目目录之外解包验证，可读取 8 类、12 seeds 和 7 类兼容配置，两份 PPTX 模板继续随包保留。
- 完整回归 **216 项通过**，包括新注册表、配置错误、类别与域名、seed、只读元数据、旧搜索隔离及所有原有测试；代码/格式检查通过，无新增运行时依赖或数据库迁移。测试使用系统临时目录，不占用正式 `data`，未调用真实 API。
- 已仅重载当前后端，保留 Streamlit 会话；实际确认 `/source-categories` 返回 8 项、`/sources` 返回 7 项，前端健康状态正常。下一步是 Migration Phase 2 的搜索契约兼容与智能调度，Phase 3 再切换界面。

### 12.5 Migration Phase 2 — Backend

**状态：已完成。** 新搜索使用 8 类规范类别；旧类别输入只在新建/执行边界映射，不批量更新历史记录。Curated Seeds 不进入注册表加载、搜索或任何工具调用。

**影响文件：** `backend/schemas/models.py`、`backend/services/source_policy.py`、`backend/providers/openai_provider.py`、`backend/providers/discovery.py`、`backend/services/page_verification.py`、`backend/services/ranking.py`、`backend/services/thumbnails.py`、`backend/main.py`、`backend/services/workflow.py`；`prompts/strategy.txt`、`prompts/search.txt`、`prompts/discovery.txt`。导出服务仅在类别/平台兼容所必需的范围内适配，模板和排序权重不变。

实施步骤：

1. **新请求与历史读取分开：** 新搜索统一使用 8 类，输入兼容旧 7 类别名并去重；Brief、Strategy、coverage 的相关上限由 7 调整到 8。持久化对象继续识别旧值，不能只替换 Literal 导致历史研究返回校验错误。新请求拒绝未知类别、空选择或越过用户所选范围的策略。
2. **区分类别与平台：** 引入兼容旧 JSON 的可选 `source_category` 字段，新结果填入规范类别，旧记录缺省为 None 并保留原 platform，必要分类时才使用旧映射；`platform` 改为有长度限制、由可信来源 URL 推导的发布平台/域名。保留候选结构中 platform 字段的类别含义以兼容原输出，最终结果的平台值由后端核对，不能由模型自行编造；coverage 与选择范围使用 category，卡片和 PPTX 仍显示真实 YouTube/Vimeo 等。仅增添带默认值的 JSON 字段，不新建 SQL 迁移或重写历史 payload。
3. **沿用一次策略生成：** 现有策略调用增加 `source_priority`，只从所选 8 类中给出 Brief 相关的顺序；理由沿用已有 summary/strands，不另增一套解释结构。新增字段默认空列表兼容旧策略；后端检查重复与越界，并补齐所选类别的可调度集合。旧策略无优先信息时，按现有 Brief 文本和确定性顺序回退，不丢失新 group，不额外调用 OpenAI 做分类。
4. **按需安排任务：** 用相关性和现有请求/时间预算选择 category × strand 组合，替换固定四组轮转。短片 Brief 可先查 Video Platforms 与原作，互动项目可先查 Web & Interactive，访谈 Brief 可先查 Podcasts & Interviews。所有默认选中不等于八类每次都必须调用；选中 GQ 所在类别也不代表必查 GQ。开放 original_work 任务单独构造，不把其他限定域名任务意外放宽成全网搜索。
5. **独立于 seeds：** 只使用所选类别的正常域名搜索或 original_work 开放发现；不实现 seed 筛选、抓取、文章拆解、专用预算或证据扩展流程，不因为 seed 文件不可用而中断搜索。
6. **保留来源范围与原作追溯：** 未选类别不作为独立搜索任务。沿用现有作品与原作证据处理，仅做新分类兼容所必需的适配；保留发现类别、原作 URL 和证据，不能因实际发布平台不同而丢失已选类别的合格作品。此次不额外建设新的跨站发现系统。
7. **核验与去重保持证据标准：** 核对具体作品身份、署名和原始来源；播客以具体一期为单位，互动设计以具体网站/项目为单位。优先保留原作者或官方作品页，原作受限时保留原有核验限制。沿用 URL、视频 ID、嵌入信息、标题/制作方去重及 alternate_urls，不让同一作品占多个名额；聚合文章不直接当作作品。
8. **不把新分类当评分捷径：** 合并 Instagram/Pinterest 后，仍按具体来源识别转载与原作；新增 publications/awards 也不自动加分。必要适配来源身份判断，保留已有相关性、可信度、制作约束、多样性权重及最多 20 条的限制。页面可读不等于已观看视频、已听播客或体验互动网站。
9. **覆盖信息保真：** coverage 按所选新类别记录实际尝试、结果及限制；“预算内未搜索”“已搜索但无合格作品”“访问受限”明确区分。原作跨域去重后的计数规则保持确定性，保留其他发现证据，不能按新平台名称把覆盖归零。诊断仍留在后台与详情，不新增主结果区通用横幅。
10. **保留后续链路：** 缩略图仍按实际 URL 走官方 oEmbed 或许可网站路径；社交预览限制不能因类别合并而绕过。PPTX 继续按 `ordered_ids` 使用同一张卡片缓存图，保留 source URL、证据及权利备注，不重新生成注释或改变版式。旧 DOCX 与下载记录保持兼容。

验收标准：

- [x] 8 类均可单独发起有效搜索计划；新请求最多 8 类，规范化后的重复类别只执行一次，未知/空选择有清楚反馈。
- [x] 类别优先级响应 Brief 且严格落在用户选择内；取消 Podcasts & Interviews 后没有其独立任务，默认全选不要求访问每个域名。
- [x] studios 的 `domains=[]` 保持开放发现，其他域名过滤有效；正常搜索和来源注册表均不依赖 seed 文件。
- [x] 原有作品核验、转载合并、证据 ID 与原始链接保持可核对，聚合文章本身不作为最终作品；本次不新增 seed 流程。
- [x] 请求次数、时间、候选池、页面预算和 20 条上限继续强制执行；无新增必需模型调用、依赖或数据库结构迁移。
- [x] 历史 7 类研究、保存选择、人工注释、标题、URL 恢复、旧导出与删除语义均可正常读取和操作，不被静默改写。

实施与验证记录：

- 后端枚举兼容 8 个规范类别与旧 7 个标签，Brief 新建、策略执行及覆盖统计使用规范类别；旧记录读取保留原始标签。Brief、Strategy、coverage 接受最多 8 类，旧别名合并后的重复项去重。新增的 source_priority 为空时沿用旧提交指纹格式，避免旧幂等键重试发生错误冲突。
- SourcePolicy 从新注册表读取全部 8 个 group，按类别单独安排任务，开放域名任务不与受限域名混合。已有规划调用可提供类别优先级；旧策略使用本地匹配回退。用户未选类别不能由候选声明、搜索结果域名或提交的策略偷偷加入。
- 最终 reference 的 source_category 记录发现类别，platform 从证据来源 URL 推导；页面跳转后的实际发布平台不取代发现范围。coverage 按去重后代表作品的主类别计数，其他合并来源继续保留在证据和 alternate_urls 中。新出版网站使用保守的来源可信度回退，原有评分权重及 Instagram/Pinterest 的较低来源权重保持不变。
- 完整回归 **220 项通过**；随后补充策略越界与历史 Brief 重启检查，迁移专项 **21 项通过**。覆盖全部 8 类逐类搜索、官方域名/开放搜索、无 seed 或坏 seed 文件、类别优先级、旧记录与选择不改写、PPTX 导出及旧提交指纹重试；代码与格式检查通过。
- 只重载后端并保留 Streamlit 会话；运行中的 OpenAPI 已确认接受 8 类、提供 source_category 与可选 source_priority。重载前后只读比对正式库中的 10 条 Brief/Research payload 完全一致，现有研究可读取、前端健康状态正常。没有执行付费搜索或消耗 OpenAI 额度；临时测试文件独立放置并清理。
- Phase 2 完成时前端仍显示旧 7 类，后端接受并映射这些标签；此后的 8 类菜单及元数据接入在 Phase 3 完成。Curated Seeds 继续暂缓。

### 12.6 Migration Phase 3 — Frontend

**状态：已完成。实际影响文件：** `frontend/app.py`、`tests/test_frontend.py`、`scripts/verify_ui.py`、`README.md` 和本计划。复用原 APIClient，无需修改 `frontend/api_client.py` 或 `frontend/curation.py`；卡片 CSS、拖拽组件、页面布局与 PPTX 模板不变。

实施步骤：

1. New Research → Sources 从后端注册表读取 8 个类别，直接显示在主输入框下方，按 12.2 顺序排列，初次打开默认全部选中。后续重运行沿用用户选择，不能因读取配置重新勾回取消项。
2. Sources、Reference Count 直接显示，保留自动策略；约束统一写入主输入框。任何类别都可取消，不加入 GQ 必选、逐域名配置或手动 Search Strategy 编辑。
3. 新表单的全不选状态在提交前明确提示至少选择一类；后端仍独立校验。来源元数据请求失败时给出重试，不默默回退到另一套旧选项或发送空范围。
4. 当前研究不重新搜索、不改标题、prompt、ID 或结果。历史 Source 筛选继续从该研究的现有记录取值，保留原控件键；若新结果按 source_category 提供筛选，使用读取适配且保持同一控件和交互，旧状态只在本次运行的合法选项内处理。
5. 卡片仍展示真实平台、作品简介和只读分析，Your Pack 的唯一 ID、草稿版本、拖拽顺序与导出保存行为不变；Recent Research、URL 恢复和详情证据区不变。文案与未来代码继续使用英文。

验收标准：

- [x] Sources 恰好显示 8 个类别且无需展开，初次默认全选，取消并提交表单后重运行仍保持选择；Source 数量不与 reference 数量混淆。
- [x] Podcasts & Interviews 与其他类别拥有完全相同的可选行为，不产生每次必须访问 GQ 的限制。
- [x] 新研究提交规范的新类别；现有研究不会因切换来源菜单而重置结果、筛选或参考包。
- [x] 桌面双列 Cards、窄屏堆叠、Recent Research、只读详情、拖拽与 PPTX 下载均沿用当前体验，不新增配置面板。

实施与验证记录：

- 删除前端硬编码的 7 类列表，按 `/source-categories` 的名称、顺序和默认选中状态显示 8 类；成功的元数据按会话缓存，重运行与研究切换不重复加载，也不触发额外 OpenAI 调用。
- 使用独立于控件生命周期的 `research_source_choices` 保存已提交的偏好，并以固定控件 key 恢复来源选择。保留原表单批量提交行为；空来源在 Find References 时明确提示，保持空选择且不提交后端搜索。新建研究与返回历史不会改动已有参考包草稿。
- 元数据失败或响应无效时不回退到旧列表，不启用搜索；保留创意和约束输入，使用表单内 Retry Sources 一并提交待保存输入并重试元数据，避免外部按钮重运行丢失未提交内容。已保存研究的浏览不依赖新建表单的元数据加载。
- 历史和新结果的 Source 筛选继续使用各研究已有的实际 platform 值，未切换为 source_category 筛选；原控件 key、选择状态、过滤后的 Pack、只读详情及 PPTX 流程保持原样。
- 完整回归 **230 项通过**，覆盖 8 类默认值、取消项与导航持久化、空选择、不合法/失败元数据、重试保留输入、元数据缓存及全部既有后端/前端功能；代码与格式检查通过。
- Chrome 使用独立的 4 条离线参考，验证默认 8 类、原生移除 Podcasts、表单校验重运行后仍保留取消项，以及 1440px 桌面与 390px 窄屏表单无横向溢出；同时通过旧平台筛选、详情零额外请求、连续拖拽、历史管理和 PPTX 精确顺序检查，卡片布局另覆盖 1200/1101/1024/768/390px。
- 浏览器验收改用系统临时目录，退出时关闭测试进程树并自动删除专用 Chrome profile，防止缓存再次堆积到项目 `data`；本轮未调用 OpenAI、未修改用户研究，Curated Seeds 未接入。README 已更新 8 类用户说明和元数据重试步骤。

### 12.7 Migration Phase 4 — Testing

**影响文件：** `tests/test_discovery.py`、`tests/test_openai_provider.py`、`tests/test_workflow.py`、`tests/test_frontend.py`、`tests/test_presentation.py`、`tests/test_page_verification.py`、`tests/test_ranking.py`、`tests/test_pptx_export.py`、`tests/test_research_management.py`、`tests/fixtures.py`；必要时新增 `tests/test_source_registry.py`，更新 `scripts/verify_ui.py`、`scripts/verify_live.py`、`scripts/verify_pptx.py` 中的来源假设。

实施步骤与验收：

1. **配置与契约：** 校验 8 类/8 group、域名及旧 7 类映射；覆盖新字段默认值、旧 Literal/coverage 读取、源数据不改写、安装包资源加载、未知类别和全不选。验证没有 seed 文件也能加载注册表和运行搜索；12 个精选入口及其专用测试不属于本次验收。
2. **搜索计划：** 对短片、品牌、互动网站、播客等 Brief 做离线回放；检查全部 8 类可调度、单类选择、用户取消项、开放域名与受限域名及预算耗尽。验证默认全选不会强制逐域名抓取，也不增加固定的 GQ 请求或 seed 抓取。
3. **作品与归属：** 使用现有工具来源、页面和原作链接的离线样本回归去重；同一作品同时见于奖项、报道、Vimeo 时只返回一条，保留来源证据；不同作品不能因标题相似误合并。合集、首页、榜单与集合文章不得作为作品返回，具体一期访谈和独立互动项目可以返回；不新增 seed 样本作为必需条件。
4. **历史与 UI：** 使用含旧 7 类的独立历史样本验证列表、打开、重命名、软删除、URL 恢复、筛选/清空、参考包草稿、人工注释和版本冲突；验证新表单默认 8 项及取消项保留，后端重启后数据仍可读。不得用用户生产数据库做迁移测试。
5. **导出与安全：** 验证实际平台显示、官方视频封面、受限社交与网站图片许可、缓存图导出、PPTX 标题和正文格式、ID 对应及精确顺序；保留旧 DOCX 下载。回归 URL/DNS/重定向、robots、登录墙、证据归属、提示注入与未知引用 ID，奖项和成效仍必须有对应证据。
6. **分阶段回归：** Configuration 后跑注册表/配置测试；Backend 后跑搜索、契约、核验与去重测试；Frontend 后跑 Streamlit 及桌面/窄屏浏览验收；最后运行现有完整测试集和代码/格式检查。每阶段先通过再继续，不把尚未运行的测试标记完成。
7. **测试规模与产物：** 主要 UI、搜索与 PPTX 流程继续只用 4 条参考验证；1/20 条边界使用离线数据。默认零 OpenAI 调用，不因本计划自动运行付费真实搜索。验收产物写入独立临时目录，完成后清理测试浏览器 profile 和可再生文件，避免再次占满 `data`；保留必要报告，不接触正式数据库和 `exports/`。

完成条件：上述四阶段针对 8 类 Sources 的验收项通过后，才将本节状态改为已实施。8 类取代新研究菜单中的旧 7 类，但已有研究、证据、人工选择和 PPTX 交付继续可用。Curated Seeds 不作为完成条件，不自动继续实施；内部 Case Study Deck 仍等待用户后续提供，也不作为本次迁移依赖。

## 13. Add References 与 Regenerate

**状态：已完成。** 沿用 Streamlit + FastAPI、现有 LLM 策略/搜索/核验/评分和 PPTX 导出，不新增依赖。

### 13.1 后端与数据

1. 增加研究操作接口，接收 Add / Regenerate、完整 Creative Brief、Sources、Reference Count 和幂等键。复用现有搜索任务，异步执行真实阶段进度；相同请求重试返回同一任务，同一原研究不允许并行提交多个操作。
2. 每个操作保存独立 Brief、策略、来源、请求数量、时间、结果和原研究 ID；旧研究的新字段使用默认值，不重写历史记录。Add 批次不作为独立研究显示在 Recent Research；Regenerate 是可独立打开的新关联版本。
3. Add 对全部已有结果去重，包括已排除和已选作品，搜索时提供避免重复的上下文，统一评分后选取新作品；提交合并时再次去重。新结果以唯一 ID 追加，保留原卡片顺序、证据、选择版本、注释、排除、Pack 顺序、标题和导出记录。
4. 追加结果、证据与批次关联在单个数据库事务中提交，保留提交期间最新的选择；重复完成不会再次追加。失败、中断或没有新作品时不修改原研究，批次记录保留原因。
5. 单批 Reference Count 和参考包仍受现有 20 条上限约束；同一研究的累计卡片、证据和核验记录允许跨批次增长。Regenerate 不继承或覆盖原 Pack，完成后打开新版本，原研究及导出继续可访问。

### 13.2 前端

1. Reference Results 标题右侧保留 Filter，并加入 + Add References 与 Regenerate；两个操作打开同一个默认隐藏的内联面板，不跳转页面。
2. 面板预填当前 Creative Brief 和 Sources，可切换 Add / Regenerate，分别显示 Additional References / Reference Count，提供 Cancel 和明确的提交按钮；编辑草稿按研究保留，切换模式和失败重试不丢输入。
3. 操作进行时禁用重复提交，显示现有真实搜索阶段。已有 Cards、Your Pack 和筛选继续可用；完成后 Add 刷新原研究，Regenerate 激活新版本。
4. 追加刷新只补充新 ID 的注释，不覆盖尚未保存的 Pack 草稿、排序、排除、筛选和现有注释；保留已有安全保存、选择版本冲突与 PPTX 顺序检查。预览缓存按结果 ID 判断新增卡片，避免旧缓存隐藏新预览。
5. URL 恢复时读取已有操作，恢复进行中的搜索；所有界面文案和代码使用英文，沿用双列 Cards、右侧 Pack 与窄屏堆叠。

### 13.3 测试与验收

- 后端：幂等提交/冲突、并发提交、完整去重、跨 20 条追加、原子合并、并发选择保存、失败/重启、软删除、旧记录兼容、版本关联及原导出保留。
- 前端：面板默认隐藏与双入口、预填/切换/取消、空输入校验、重复点击、错误重试、Pack 未保存编辑保护、筛选状态、刷新恢复和完成后正确导航。
- 导出：新增卡片可以选择、保存及导出 PPTX，文档严格遵循显示的 Pack 顺序，原有研究和文件保持有效。
- 用离线测试验证主要 4 条参考场景及数量边界；不自动运行付费搜索，不修改正式研究数据库。完成后运行相关回归及代码检查，并记录实际结果。

### 13.4 实施与验证记录

- 新增 `POST /search-runs/{id}/actions` 与 `GET /search-runs/{id}/actions`。每个批次用独立 Brief 和 SearchRun 保存完整参数、真实阶段与结果，追加批次从 Recent Research 隐藏，重生成版本通过 parent_run_id 关联原研究；不迁移或批量改写历史数据。
- 复用同一策略、搜索、核验和评分流水线；追加时向搜索及筛选提供全部旧作品的去重上下文，在最终 Top N 之前排除已有作品，事务提交时再次去重。原研究的选择版本、顺序、排除、注释及导出不被覆盖，失败不修改原研究。
- 使用 SQLite 事务同时提交批次和累计结果，选择保存也采用事务读改写，避免追加与 Pack 保存互相覆盖。同一请求的幂等重试不重复规划、搜索或追加；同一研究的并行更新被明确拒绝。旧来源名称在覆盖统计合并时规范化，保留旧卡片及其来源字段。
- 新增 `frontend/research_actions.py` 共用内联编辑器和实际阶段轮询；标题操作按组响应式换行。支持切换模式、取消、验证、失败保留编辑、响应丢失后的安全重试、URL 恢复正在执行的更新和完成后的正确导航。新增卡片只补充草稿注释与预览缓存 ID，不重置筛选或未保存的 Pack。
- 应用完整回归 **397 项通过**（未重复运行本次未改动的进程生命周期测试）；随后新增失败标题、明确拒绝提交及旧来源覆盖三个边界用例。最终后端针对性回归 **60 项通过**，前端回归 **64 项通过**；当前应用测试共 400 项。Ruff 与 diff 格式检查通过。
- Chrome 无头验收通过 1440px / 390px 的 Add 与 Regenerate 面板，以及 1440 / 1200 / 1101 / 1024 / 768 / 390px 的结果布局、标题操作、筛选、Pack 保留、拖拽、历史导航、重命名、确认删除与精确 PPTX 顺序。验收使用独立数据库、4 条离线参考及测试端口，未调用 OpenAI；截图和报告写入系统临时目录，测试 Chrome profile 和进程均已清理。

## 14. 切换研究不中断搜索

**状态：已实施。**

- 新研究从 Brief 分析开始即在后端创建持久化任务，前端只提交参数、显示进度；继续复用同一规划与检索流水线，旧的 Brief / Strategy 两步接口和重试状态保持兼容。
- 切换历史研究或返回 New Research 仅切换展示，不取消后端的新搜索、Add 或 Regenerate。返回原研究时恢复实际进度；后台完成的任务不会强制跳走正在浏览的其他研究。
- 旧页面的轮询在读取结果前后核对当前 research ID，避免过期响应覆盖当前页面；任务仍依赖正在运行的后端进程，工作区停止或后端重启沿用中断记录处理。
- 以真实后台线程和可控的 Brief 分析/搜索阻塞点验证跨页面继续执行、完成结果持久化、返回恢复、Pack 保护及幂等提交，测试不调用 OpenAI。
- 新增 `ResearchSubmissionInput`，由原 `POST /search-runs` 同时兼容完整 Brief 提交与旧的已规划策略提交；Brief、幂等键与队列记录在调用模型前即持久化。界面不再负责串接新搜索的规划与提交请求，旧后端能力及旧会话待重试请求仍有兼容路径。
- 相关回归 **123 项通过**，包括真实后台线程分别阻塞 New Search / Add / Regenerate 的规划与检索阶段，在切换到其他研究和首页后继续完成；覆盖迟到轮询响应、失败记录、进度错误清除与重复提交只调用一次模型。检索流水线离线回放另 **24 项通过**；随后补充的失败详情展示用例通过，Ruff 与 diff 检查通过。

## 15. Global Favorites 与跨研究 Packs

**状态：已完成。** 沿用现有 Streamlit + FastAPI、SQLite、卡片、拖拽组件及 PPTX 模板，不改变搜索和评分，不新增依赖或模型调用。

1. **独立资料库：** 通过后端读取已核验的研究结果，保存参考、原 Brief、分析、证据、真实链接和已有预览的独立快照；沿用作品身份去重规则。相同作品跨研究共用资料库 ID，同时保留各研究上下文，不接收客户端伪造的参考或证据。
2. **Favorites：** 侧栏位于 Recent Research 上方；卡片书签可收藏或取消收藏，Favorites 页复用双列卡片及只读详情。收藏状态在 SQLite 持久化；取消收藏不影响 Pack，删除研究不删除收藏或其资料。
3. **Current Pack：** 将 Your Pack 改名为 Current Pack，右侧管理多个命名 Pack，支持创建、切换、重命名和确认删除；同一个 Pack 可以从不同研究或 Favorites 的 + 按钮添加作品。每次提交新 Research（包括 Regenerate）在同一事务中创建一个空 Pack 并设为当前，幂等重试不重复创建；Add References 不创建新 Pack。复用已有研究标题命名，不额外调用模型、不覆盖手动 Pack 名称；后台完成与浏览旧研究不强制切换当前 Pack。保持最多 20 条、无重复、拖拽排序及简单移除，立即持久化每次操作，切换页面和重启不丢内容。
4. **历史兼容：** 原研究选择、注释、排除、选择版本及旧 DOCX/PPTX 导出接口保持可读可用；已保存选择通过可重复启动、每研究仅一次的导入生成命名 Pack，不覆盖原数据，已删除 Pack 不会重启后复活。未保存的旧会话草稿独立保留，不擅自改写。
5. **版本和失败保护：** 命名 Pack 操作采用事务及乐观版本检查；创建、添加、移除和重排具备安全重试行为。冲突时保留当前显示内容和待提交操作，提供加载最新 Pack 的明确动作，不静默覆盖其他会话。
6. **PPTX：** 从独立 Pack 快照生成文档，严格遵循 Current Pack 的显示顺序，保留每条参考的原研究分析、来源、Brief、证据及权利信息；预览复用已缓存图片。删除原研究后仍可导出，历史导出保留，导出不重新搜索或调用模型。
7. **UI 集成：** 保留原页面布局、筛选、Add/Regenerate、后台搜索和导航恢复；书签与 + 各自控制收藏和 Current Pack，不互相替代。Favorites 页也显示右侧 Current Pack，可直接新建 Pack 并加入收藏。
8. **验证：** 先验证持久化、跨研究去重、各上下文快照、版本冲突、迁移和删除隔离，再验证 UI 收藏切换、Pack 管理、拖拽/导出、后台搜索互不影响；以 4 条离线参考为主，覆盖重启与桌面/窄屏，最后执行相关回归和代码检查，不使用正式数据库或付费 API。

实施与验收记录：

- 新增 `backend/schemas/library.py`、`backend/services/library.py`、`backend/services/library_export.py` 和 `backend/library_routes.py`。资料库、每研究快照、收藏状态、命名 Pack、当前选择、幂等创建及独立预览由 SQLite 保存；旧研究与导出记录不改写。
- 原有保存选择在启动时逐研究导入一次，保留人工注释和顺序；删除导入的 Pack 不会再次导入。跨研究依据已有作品身份、原链接及备用链接去重，保留各研究/追加批次的原 Brief、分析、证据和预览。PPTX 使用每条参考选定的上下文和图片，并在备注中保留原研究、批次和参考 ID。
- 新增 `frontend/library.py`；侧栏 Favorites、卡片书签与 Current Pack 的 + 独立。Favorites 复用原卡片与只读详情；右侧选择器和三点菜单管理 Pack，每次添加、移除和拖拽立即保存。失败保留待提交操作并支持安全重试，冲突提供明确的加载最新状态入口。
- 应用完整回归 **430 项通过**（未重复运行未改动的进程生命周期测试）；随后补充并发提交、数量边界、备用链接去重、追加批次上下文及各研究图片版本，相关专项回归 **75 项通过**，最终图片/批次上下文 **4 项通过**。Ruff 与 diff 检查通过。
- `scripts/verify_library_ui.py` 使用两组各 4 条离线参考、专用端口与临时数据库，通过实际 Chrome 书签切换、收藏列表、删除原研究后的刷新/详情、从 Favorites 创建 Pack、切换、实际拖拽与精确 PPTX 顺序，覆盖 1440 / 1024 / 768 / 390px 无横向溢出。原 `scripts/verify_ui.py` 命令转到当前 UI 的验收，原辅助组件和历史选择检查仍可导入。
- 本轮未调用 OpenAI，未修改正式数据库或停止正式工作区；最终浏览器测试的进程及 Chrome profile 已清理，截图/报告保留在系统临时目录，项目内测试目录仅清理本轮生成的路径。

## 16. PPTX 超长标题本地截断

**状态：已完成，按后续要求取消 API 改写。** 普通研究与跨研究 Pack 共用本地标题适配，保留现有模板、排序和导出保存流程。

- 依据真实模板标题框宽度检查全大写显示标题，保留默认 54pt、最低 18pt 和单行布局；无法容纳时按完整单词截断并加省略号，同时计入省略号宽度。
- 移除标题专用 OpenAI 调用、结构化返回处理和持久缓存代码；重复导出或重启后均直接本地计算，不使用旧标题缓存，不修改研究、收藏、Pack 快照或 UI 原题。
- 保留既有模板、正文、图片及链接处理，正文超框仍保留明确错误；导出不重新搜索、调用模型或下载图片。
- 验证：**42 项相关测试通过**，覆盖普通研究与跨研究 Pack 导出、超长标题、无空格长词、重启后重复导出、忽略旧缓存及原始数据不变；标题专项测试禁止任何 OpenAI 客户端调用。

## 17. Streamlit Community Cloud 单应用入口

- 新增 `cloud/streamlit_app.py`，同一实例内启动原 FastAPI 并加载现有前端；后端绑定自动分配的 loopback 端口，部署仅需填写一个 Main file path。本地 `run.py` 和 Windows 启动器不变。
- 云端使用 Secrets/环境变量，不依赖本地 `.env`；默认数据目录为 `data/cloud/`，不触碰本地工作区数据。根 `requirements.txt` 复用 `pyproject.toml` 安装运行依赖，云端子目录配置公开 Streamlit 监听地址。
- 通过全局资源缓存复用一个 API worker，启动先校验健康状态；缓存释放或主进程退出时仅清理自有进程。凭据经私有 stdin 传递，不放入命令行或日志；配置或后端代码变更要求 Reboot，不静默沿用旧代码。
- 本入口保留共享工作区语义；未增加用户隔离或外部持久化。Community Cloud 本地文件不保证长期保留，长期使用需另行接入持久存储。
- 验证：本地 **99 项测试通过**，覆盖真实 API 子进程与界面启动、并发会话复用、重启与退出清理、凭据脱敏、后台检索以及现有前端/Pack 功能；`requirements.txt` 构建预检通过，未执行真实 OpenAI 请求。尚未上传或实际部署到 Streamlit Community Cloud。

## 18. 共享工作区安全码

- 本地与云端入口在加载研究数据前显示不可关闭的安全码弹窗；云端在验证通过后才启动内部 API。每个浏览器会话独立解锁，同会话页面切换无需再次输入。
- 使用私有 Streamlit Secrets 或环境变量 `APP_ACCESS_CODE`，不把实际安全码提交到公开仓库；未配置时拒绝访问。云端部署需在应用 Secrets 中单独设置。
- 输入隐藏显示，服务端校验；连续输错五次后当前会话暂停尝试 30 秒。不更改检索、模型、收藏、Pack 或导出流程。
- 验证：安全码、现有前端与云端启动相关 **87 项测试通过**；真实浏览器验证首次解锁、刷新后再次解锁、收藏、Pack 管理、拖拽及 PPTX 导出顺序通过。未调用 OpenAI；本轮临时测试数据已清理。

## 19. 创意要点与多图预览

**实施范围：** 不改搜索与排名流程，兼容旧研究、收藏和 Pack；不新增依赖。

1. 保留 `what_to_borrow` 字符串合同，调整现有 AI 输出提示为 2–3 条简洁、可执行建议；详情与 PPTX 显示列表。历史段落仅在渲染时拆分，不重新生成或覆盖数据库。
2. 扩展原有预览载荷与持久缓存，每条参考最多 4 张来自原始页面的相关图片；优先已有封面，过滤装饰图、广告和重复图。已有视频封面与单图回退保留。
3. 卡片、详情和收藏复用轻量原生 Streamlit 组件，显示左右箭头与计数；单图隐藏导航。浏览器内切换图片，不触发后端请求，保持布局与选中状态。
4. 不新增图片授权门槛；沿用公开地址、文件格式、图片尺寸与抓取预算校验。已有来源及权利记录保持可读，PPTX 沿用首张预览图。
5. 覆盖历史内容、真实 PPTX 项目符号、采集过滤、去重、缓存、错误回退及浏览器桌面/窄屏验证，使用离线测试素材，不调用付费 API。

**状态：已完成。** 分组回归共 **278 项通过**，涵盖 AI 请求格式兼容、采集/缓存、旧研究和收藏、卡片与详情、PPTX、访问验证及云端入口。Chrome 验证卡片/详情轮播、三条建议、390px 详情布局、收藏持久化、Pack 拖拽及导出顺序通过；图片切换不触发服务器回调，单图保留原展示。PPTX 使用可编辑的原生项目符号与首张图片；旧收藏的永久预览不强制重新抓取。Ruff 与 diff 检查通过，未使用正式数据库或调用真实 OpenAI API。
