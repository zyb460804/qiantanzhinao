/**
 * 千摊智脑 · admin-web 设计令牌
 * 与小程序 miniprogram/app.wxss 色彩体系对齐(清爽现代绿 v3.0)。
 *
 * 本文件是 admin-web 端唯一的品牌色 / 图表色板来源。
 * 请勿在各页面 / 图表里硬编码色值——统一从这里引入。
 */

// 品牌主色(对齐 app.wxss 令牌,括号内为对应 CSS 变量名)
export const brand = {
  primary: '#00A06A', // --green-700
  primaryHover: '#00B578', // --green-600
  success: '#00B578', // --green-600
  warning: '#FFA800', // --corn
  error: '#FA5151', // --tomato
  info: '#3478F6', // --info
  grape: '#8B5CF6', // --grape
}

// 分类图表色板(6 色,品牌感知、色相区分清晰,可循环复用)
// 顺序:绿 → 蓝 → 橙 → 紫 → 青 → 珊瑚红
export const chartPalette = [
  '#00A06A', // green
  '#3478F6', // info
  '#FFA800', // corn
  '#8B5CF6', // grape
  '#00B9A8', // teal
  '#FA5151', // tomato
]

// 暗色背景下仍可读的图表色板(保留以备深色图表扩展)
export const chartPaletteDark = ['#16C48A', '#6AA5F8', '#FFBE45', '#A88BF8', '#3ACCBE', '#FC7A7A']

// Antd ConfigProvider 全局令牌
export const antdTokens = {
  colorPrimary: brand.primary,
  colorSuccess: brand.success,
  colorWarning: brand.warning,
  colorError: brand.error,
  colorInfo: brand.info,
  borderRadius: 10,
  fontFamily:
    "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, 'Noto Sans', 'PingFang SC', 'Microsoft YaHei', sans-serif",
}

// 浅色模式追加令牌(暗色模式下不注入,避免破坏 darkAlgorithm 的推导)
export const antdLightTokens = {
  colorBgLayout: '#F4F6F5',
}

// 注入到 :root 的 CSS 变量(供 index.css 及内联样式消费,与 brand 保持同步)
export const cssVars = {
  '--qg-primary': brand.primary,
  '--qg-primary-hover': brand.primaryHover,
  '--qg-success': brand.success,
  '--qg-warning': brand.warning,
  '--qg-error': brand.error,
  '--qg-info': brand.info,
  '--qg-grape': brand.grape,
  '--qg-corn': '#FFA800',
  '--qg-teal': '#00B9A8',
}
