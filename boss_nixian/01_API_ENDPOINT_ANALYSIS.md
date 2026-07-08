# Boss 直聘职位列表 API 端点分析

## 1. 关键 API 识别结论

### 主要职位列表端点

| 项目 | 值 |
|------|-----|
| **URL** | `https://www.zhipin.com/wapi/zpgeek/pc/recommend/job/list.json` |
| **Method** | GET |
| **Content-Type** | application/json |
| **认证方式** | Cookie (登录态) |
| **用途** | 获取推荐职位列表（首页默认加载） |

### 其他相关端点

| 端点 | 用途 |
|------|------|
| `/wapi/zpgeek/job/detail.json` | 获取单个职位详情 |
| `/wapi/zpgeek/search/job/tdk.json` | 搜索页 TDK 数据 |
| `/wapi/zpgeek/search/job/seo/data.json` | SEO 数据 |
| `/wapi/zpgeek/search/job/sidebar.json` | 侧边栏筛选项 |

## 2. 请求参数表

### 职位列表 API 参数

| 参数名 | 位置 | 是否必需 | 示例值 | 来源/说明 |
|--------|------|----------|--------|-----------|
| `page` | query | 是 | `1` | 页码，从 1 开始 |
| `pageSize` | query | 是 | `15` | 每页数量，默认 15 |
| `city` | query | 是 | `101280100` | 城市编码（广州） |
| `encryptExpectId` | query | 否 | `""` | 加密的求职期望 ID |
| `mixExpectType` | query | 否 | `""` | 求职期望类型 |
| `expectInfo` | query | 否 | `""` | 求职期望信息 |
| `jobType` | query | 否 | `""` | 职位类型 |
| `salary` | query | 否 | `""` | 薪资范围 |
| `experience` | query | 否 | `""` | 工作经验 |
| `degree` | query | 否 | `""` | 学历要求 |
| `industry` | query | 否 | `""` | 行业 |
| `scale` | query | 否 | `""` | 公司规模 |
| `_` | query | 是 | `1783495671764` | 时间戳（毫秒） |

### 城市编码对照表

| 城市 | 编码 |
|------|------|
| 广州 | 101280100 |
| 深圳 | 101280600 |
| 北京 | 101010100 |
| 上海 | 101020100 |
| 杭州 | 101210100 |
| 成都 | 101270100 |

## 3. 分页机制

- **分页方式**: 传统的 page/pageSize 分页
- **起始页**: 1
- **默认每页**: 15 条
- **是否有更多**: `zpData.hasMore` 字段指示
- **无 cursor/offset**: 使用简单的页码递增

## 4. 认证与反爬评估

### 认证要求

| 检查项 | 结果 |
|--------|------|
| 是否需要登录 Cookie | **是** - 需要有效的登录态 |
| 是否有签名参数 | **否** - 未发现 sign/_token/enc 参数 |
| 是否需要 Referer | **推测否** - 直接访问 API 返回数据 |
| 是否有自定义 Header | **未发现** |
| 响应是否加密 | **否** - 明文 JSON |
| 是否有字体反爬 | **否** - 文本正常显示 |

### 安全评估

- **securityId**: 每个职位都有 `securityId`，用于职位详情页访问，**不建议自行生成**
- **encryptJobId**: 加密的职位 ID，用于构建详情页 URL
- **encryptBossId**: 加密的 Boss ID，用于构建 Boss 主页 URL
- **lid**: 追踪 ID，格式为 `{sessionId}.search.{position}`

### 使用建议

**推荐拦截复用**: 由于 API 需要登录态且存在 securityId 等动态字段，建议通过拦截页面原生请求来获取数据，而非自行构造请求。
