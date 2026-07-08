# Boss 直聘职位列表 API 逆向分析报告

## 分析概述

本报告详细分析了 Boss 直聘（zhipin.com）职位搜索列表页的内部 API，为 AI Career Copilot 浏览器扩展提供数据拦截与解析方案。

## 分析时间

2026-07-08

## 目标页面

https://www.zhipin.com/web/geek/jobs?city=101280100

## 文件结构

```
boss_nixian/
├── README.md                          # 本文件
├── 01_API_ENDPOINT_ANALYSIS.md        # API 端点分析
├── 02_FIELD_MAPPING.md                # 字段映射表
├── 03_INTERCEPTOR_MAIN_WORLD.ts       # 主世界拦截器代码
├── 04_CONTENT_SCRIPT_RECEIVER.ts      # Content Script 接收器
├── 05_RISK_ASSESSMENT.md              # 风险评估与建议
└── 06_IMPLEMENTATION_EXAMPLE.ts       # 完整实现示例
```

## 核心发现

### 1. 关键 API 端点

| 项目 | 值 |
|------|-----|
| **URL** | `/wapi/zpgeek/pc/recommend/job/list.json` |
| **Method** | GET |
| **认证** | Cookie（登录态） |
| **分页** | page/pageSize 参数 |

### 2. 响应结构

```json
{
  "code": 0,
  "message": "Success",
  "zpData": {
    "hasMore": true,
    "jobList": [
      {
        "jobName": "职位名称",
        "brandName": "公司名称",
        "salaryDesc": "8-12K",
        "cityName": "广州",
        "areaDistrict": "番禺区",
        "businessDistrict": "南村",
        "jobLabels": ["1-3年", "本科"],
        "skills": ["Python", "Django"],
        "encryptJobId": "xxx",
        "securityId": "xxx",
        ...
      }
    ]
  }
}
```

### 3. 推荐方案

**拦截复用** - 通过拦截页面原生 fetch/XHR 请求获取数据

优势：
- 无需处理认证
- 无需处理签名
- 实时性好
- 维护成本低

## 使用指南

### 快速开始

1. 阅读 `01_API_ENDPOINT_ANALYSIS.md` 了解 API 结构
2. 查看 `02_FIELD_MAPPING.md` 了解字段映射
3. 参考 `06_IMPLEMENTATION_EXAMPLE.ts` 集成到扩展

### 集成步骤

1. 将 `03_INTERCEPTOR_MAIN_WORLD.ts` 作为主世界脚本
2. 将 `04_CONTENT_SCRIPT_RECEIVER.ts` 作为 Content Script
3. 配置 manifest.json 注入权限
4. 在 Service Worker 中处理接收到的数据

### 注意事项

- `securityId` 字段**不建议自行生成**，使用拦截到的值
- 建议限制请求频率，避免触发风控
- 仅用于个人浏览器扩展，不得批量爬取

## 风险评估

| 评估项 | 结论 |
|--------|------|
| 置信度 | **高** |
| 稳定性 | **中-高**（短期稳定） |
| 使用建议 | **推荐拦截复用** |

## 后续优化

1. 支持搜索接口拦截
2. 添加职位详情获取
3. 实现增量更新
4. 添加数据缓存机制

## 技术支持

如有问题，请参考各文件中的详细说明。
