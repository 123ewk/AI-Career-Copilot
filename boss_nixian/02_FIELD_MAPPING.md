# Boss 直聘 API 字段映射表

## 响应结构概览

```
{
  "code": 0,                    // 状态码，0=成功
  "message": "Success",         // 状态消息
  "zpData": {
    "hasMore": true,            // 是否有更多页
    "jobList": [...],           // 职位数组
    "type": 2,                  // 列表类型
    "lid": "xxx"                // 会话追踪 ID
  }
}
```

## 职位字段映射表

| Boss JSON 字段路径 | 类型 | 扩展字段名 | 说明/示例 |
|-------------------|------|-----------|-----------|
| `zpData.jobList[i].jobName` | string | `title` | "AI自动化工程师（跨境电商TEMU）" |
| `zpData.jobList[i].brandName` | string | `company` | "广州如鱼得水文化..." |
| `zpData.jobList[i].salaryDesc` | string | `salary` | "8-12K"、"11-16K·13薪" |
| `zpData.jobList[i].cityName` | string | `city` | "广州" |
| `zpData.jobList[i].areaDistrict` | string | `district` | "番禺区" |
| `zpData.jobList[i].businessDistrict` | string | `area` | "南村" |
| `zpData.jobList[i].jobLabels` | array | `tags` | ["1-3年", "大专"] |
| `zpData.jobList[i].skills` | array | `skills` | ["Python", "Django", "MySQL"] |
| `zpData.jobList[i].jobExperience` | string | `experience` | "1-3年" |
| `zpData.jobList[i].jobDegree` | string | `degree` | "大专"、"本科" |
| `zpData.jobList[i].encryptJobId` | string | `jobId` | "245b790b526c79170nZ_392_FVNX" |
| `zpData.jobList[i].encryptBrandId` | string | `companyId` | "8e44730a3525241c03dy3d2_E1U~" |
| `zpData.jobList[i].brandLogo` | string | `companyLogo` | 公司 logo URL |
| `zpData.jobList[i].brandStageName` | string | `fundingStage` | "未融资"、"已上市"、"C轮" |
| `zpData.jobList[i].brandIndustry` | string | `industry` | "电子商务"、"互联网" |
| `zpData.jobList[i].brandScaleName` | string | `companySize` | "20-99人"、"1000-9999人" |
| `zpData.jobList[i].bossName` | string | `recruiterName` | "苏女士" |
| `zpData.jobList[i].bossTitle` | string | `recruiterTitle` | "招聘者"、"HR"、"CEO" |
| `zpData.jobList[i].bossOnline` | boolean | `recruiterOnline` | true/false |
| `zpData.jobList[i].bossAvatar` | string | `recruiterAvatar` | 头像 URL |
| `zpData.jobList[i].securityId` | string | `securityId` | 用于详情页访问（**不要自行生成**） |
| `zpData.jobList[i].jobType` | number | `jobType` | 0=全职, 5=实习 |
| `zpData.jobList[i].gps.longitude` | number | `longitude` | 经度 |
| `zpData.jobList[i].gps.latitude` | number | `latitude` | 纬度 |
| `zpData.jobList[i].welfareList` | array | `benefits` | 福利列表（当前为空） |
| `zpData.jobList[i].itemId` | number | `itemIndex` | 列表中的序号 |
| `zpData.jobList[i].jobValidStatus` | number | `status` | 1=有效 |

## 详情页 URL 构建

```typescript
// 职位详情页
const jobDetailUrl = `https://www.zhipin.com/job_detail/${encryptJobId}.html`;

// 公司主页
const companyUrl = `https://www.zhipin.com/gongsi/${encryptBrandId}.html`;
```

## 薪资解析

薪资格式多样，需要解析：

| 格式 | 示例 | 解析结果 |
|------|------|----------|
| 简单范围 | "8-12K" | min=8000, max=12000 |
| 带薪数 | "11-16K·13薪" | min=11000, max=16000, months=13 |
| 面议 | "面议" | min=null, max=null |

```typescript
function parseSalary(salaryDesc: string): { min: number | null; max: number | null; months: number } {
  if (!salaryDesc || salaryDesc === '面议') {
    return { min: null, max: null, months: 12 };
  }

  const match = salaryDesc.match(/(\d+)-(\d+)K(?:·(\d+)薪)?/);
  if (match) {
    return {
      min: parseInt(match[1]) * 1000,
      max: parseInt(match[2]) * 1000,
      months: match[3] ? parseInt(match[3]) : 12
    };
  }

  return { min: null, max: null, months: 12 };
}
```

## 完整位置拼接

```typescript
function buildLocation(job: any): string {
  const parts = [job.cityName, job.areaDistrict, job.businessDistrict].filter(Boolean);
  return parts.join('·');
  // 示例: "广州·番禺区·南村"
}
```
