/**
 * Boss 直聘 DOM 选择器配置
 *
 * 职责：
 * - 集中管理 Boss 直聘列表页 + 详情面板的所有 CSS 选择器
 * - 提供 queryText / queryElement / queryTextRendered 等辅助函数
 * - 当 Boss 页面结构变化时，只需修改本文件即可适配
 *
 * 设计动机：
 * - spec §2.1 要求选择器外置到 selector.ts，便于维护
 * - design doc §3 已基于真实页面 HTML 验证过选择器
 * - 字体反爬处理：薪资字段需用 innerText 而非 textContent（见 design doc §5.1）
 *
 * 使用方式：
 *   import { BOSS_SELECTORS, queryText } from './selector'
 *   const card = document.querySelector(BOSS_SELECTORS.list.jobCard)
 *   const title = queryText(card, BOSS_SELECTORS.list.jobName)
 */

/**
 * Boss 直聘选择器配置接口
 *
 * 列表页与详情面板分离：
 * - list：从 .job-card-box 提取岗位卡片基础信息（岗位名、薪资、标签等）
 * - detail：从 .job-detail-box 提取完整 JD、技能标签、招聘者信息
 *
 * 选择器相对路径约定：
 * - list 内的选择器相对于 .job-card-box 元素查找
 * - detail 内的选择器相对于 .job-detail-box 元素查找
 */
export interface BossSelectorConfig {
  /** 列表页岗位卡片选择器 */
  list: {
    /** 岗位列表容器（用于 MutationObserver 监听子节点变化，design doc §5.2） */
    listContainer: string
    /** 单个岗位卡片容器 */
    jobCard: string
    /** 岗位名（同时是 <a> 标签，href 为详情页 URL） */
    jobName: string
    /** 薪资显示（字体反爬字段，必须用 innerText 读取） */
    jobSalary: string
    /** 标签列表容器（如 "5天/周"、"6个月"、"本科"） */
    tagList: string
    /** 单个标签项 */
    tagItem: string
    /** 招聘者/公司名称（猎头/代招可能显示为"某大型 ICT 公司"） */
    bossName: string
    /** 工作地点（如 "深圳·南山区·西丽"） */
    companyLocation: string
    /** 岗位详情链接（与 jobName 同元素，读取 href 属性） */
    detailLink: string
    /** 已读标记选择器（在 jobCard 内查找，存在即表示已读） */
    seenClass: string
    /** 特殊标签图标（<img> 的 alt 属性，如 "猎头"、"代招"） */
    specialTag: string
  }
  /** 详情面板选择器 */
  detail: {
    /** 详情面板容器 */
    container: string
    /** 岗位名 */
    jobName: string
    /** 薪资显示（字体反爬字段） */
    jobSalary: string
    /** 标签列表容器（含城市、工作周期、学历等） */
    tagList: string
    /** 单个标签项 */
    tagItem: string
    /** JD 正文（完整职位描述） */
    jd: string
    /** 技能标签列表容器（如 "Pandas"、"MySQL"） */
    skillList: string
    /** 单个技能标签项 */
    skillItem: string
    /** 招聘者姓名（如 "罗女士"） */
    bossName: string
    /** 招聘者职位（如 "科脉技术 · HR"） */
    bossTitle: string
    /** 工作地址（如 "深圳南山区南山智园 A4 栋 8 楼"） */
    address: string
    /** 立即沟通按钮（后续自动打招呼阶段使用） */
    chatButton: string
  }
}

/**
 * Boss 直聘选择器常量
 *
 * 选择器来源：design doc §3 基于真实页面 HTML 验证
 * 维护策略：Boss 页面结构变化时只需修改本常量，无需改动 parser/adapter
 */
export const BOSS_SELECTORS: BossSelectorConfig = {
  list: {
    // .rec-job-list 是岗位列表的滚动容器，MutationObserver 监听其子节点变化以支持滚动加载
    listContainer: '.rec-job-list',
    // .job-card-box 是单个岗位卡片的最外层容器
    jobCard: '.job-card-box',
    // .job-title .job-name 通常是 <a> 标签，textContent 为岗位名，href 为详情页链接
    jobName: '.job-title .job-name',
    // .job-title .job-salary 显示薪资，因字体反爬必须用 innerText 读取
    jobSalary: '.job-title .job-salary',
    // .tag-list 包含实习周期、学历要求等标签
    tagList: '.tag-list',
    tagItem: '.tag-list li',
    // .job-card-footer .boss-name 显示招聘者或公司名称
    bossName: '.job-card-footer .boss-name',
    // .job-card-footer .company-location 显示工作地点
    companyLocation: '.job-card-footer .company-location',
    // 详情链接与 jobName 是同一元素，adapter 中读取 href 属性
    detailLink: '.job-title .job-name',
    // .is-seen 类标记已查看过的岗位，加在 .card-area 上
    seenClass: '.is-seen',
    // .job-tag-icon 是 <img>，alt 属性为 "猎头"/"代招" 等特殊标签
    specialTag: '.job-tag-icon',
  },
  detail: {
    // .job-detail-box 是右侧详情面板的根容器
    container: '.job-detail-box',
    jobName: '.job-detail-header .job-name',
    jobSalary: '.job-detail-header .job-salary',
    tagList: '.job-detail-header .tag-list',
    tagItem: '.job-detail-header .tag-list li',
    // .job-detail-body .desc 包含完整职位描述（含任职要求）
    jd: '.job-detail-body .desc',
    // .job-label-list 是技能标签列表（Pandas/MySQL/Python 等）
    skillList: '.job-detail-body .job-label-list',
    skillItem: '.job-detail-body .job-label-list li',
    // .job-boss-info .name 是招聘者姓名（如 "罗女士"）
    bossName: '.job-boss-info .name',
    // .job-boss-info .boss-info-attr 是招聘者职位（如 "科脉技术 · HR"）
    bossTitle: '.job-boss-info .boss-info-attr',
    // .job-address .job-address-desc 是详细工作地址
    address: '.job-address .job-address-desc',
    // .op-btn-chat 是"立即沟通"按钮，后续自动打招呼阶段使用
    chatButton: '.job-detail-header .op-btn-chat',
  },
}

/**
 * 在指定根节点内查找单个元素
 *
 * @param root 查找的根节点（document、Element 或 DocumentFragment）
 * @param selector CSS 选择器
 * @returns 找到的元素，未找到返回 null
 */
export function queryElement(
  root: ParentNode | null,
  selector: string,
): Element | null {
  // 防御性编程：root 为 null 时直接返回 null，避免抛出异常
  if (!root) return null
  return root.querySelector(selector)
}

/**
 * 在指定根节点内查找所有匹配元素
 *
 * @param root 查找的根节点
 * @param selector CSS 选择器
 * @returns 匹配的元素列表（NodeList）
 */
export function queryAll(
  root: ParentNode | null,
  selector: string,
): NodeListOf<Element> {
  if (!root) return document.querySelectorAll('.__nonexistent__')
  return root.querySelectorAll(selector)
}

/**
 * 提取元素的文本内容（通用版本，textContent 优先）
 *
 * textContent 性能优于 innerText，且不受 CSS 显示状态影响
 * 适用于岗位名、公司名、标签等不会触发字体反爬的字段
 *
 * @param root 查找的根节点
 * @param selector CSS 选择器
 * @returns 去除首尾空白后的文本，未找到返回空字符串
 */
export function queryText(
  root: ParentNode | null,
  selector: string,
): string {
  const el = queryElement(root, selector)
  if (!el) return ''
  // textContent 不触发 reflow，性能最优
  const text = el.textContent?.trim() ?? ''
  // 回退到 innerText：处理 textContent 为空但元素实际有渲染文本的情况
  if (text) return text
  return (el as HTMLElement).innerText?.trim() ?? ''
}

/**
 * 提取元素的渲染文本（用于薪资等字体反爬字段）
 *
 * innerText 读取的是浏览器渲染后的文本，能正确处理自定义字体映射
 * textContent 读取的是 HTML 源码字符，对薪资字段可能为乱码
 *
 * design doc §5.1：Boss 直聘使用自定义字体对数字进行映射
 * - textContent 读取到乱码（如 "-元/天"）
 * - innerText 读取到正常数字（如 "300-360元/天"）
 *
 * 性能说明：innerText 会触发 reflow，比 textContent 慢
 * 因此仅用于薪资等必须使用渲染文本的字段，其他字段用 queryText
 *
 * @param root 查找的根节点
 * @param selector CSS 选择器
 * @returns 去除首尾空白后的文本，未找到返回空字符串
 */
export function queryTextRendered(
  root: ParentNode | null,
  selector: string,
): string {
  const el = queryElement(root, selector)
  if (!el) return ''
  // 薪资字段必须用 innerText，否则会读到字体映射前的乱码
  const text = (el as HTMLElement).innerText?.trim() ?? ''
  if (text) return text
  // 回退到 textContent：处理 innerText 为空的情况（如元素被隐藏）
  return el.textContent?.trim() ?? ''
}

/**
 * 提取元素列表的文本内容（用于标签列表、技能列表等）
 *
 * @param root 查找的根节点
 * @param selector CSS 选择器（匹配多个元素，如 ".tag-list li"）
 * @returns 文本数组（已去除空白项，避免空 li 混入）
 */
export function queryTextList(
  root: ParentNode | null,
  selector: string,
): string[] {
  if (!root) return []
  const elements = root.querySelectorAll(selector)
  const texts: string[] = []
  elements.forEach((el) => {
    const text = el.textContent?.trim() ?? ''
    // 过滤空白项：Boss 列表页 .tag-list 可能包含空 li 用于间距
    if (text) texts.push(text)
  })
  return texts
}

/**
 * 提取元素的属性值
 *
 * 用于读取 <a> 的 href、<img> 的 alt 等属性
 *
 * @param root 查找的根节点
 * @param selector CSS 选择器
 * @param attribute 属性名
 * @returns 属性值，未找到元素或属性返回空字符串
 */
export function queryAttribute(
  root: ParentNode | null,
  selector: string,
  attribute: string,
): string {
  const el = queryElement(root, selector)
  if (!el) return ''
  return el.getAttribute(attribute) ?? ''
}
