/**
 * 千摊智脑 · admin-web 设计令牌
 * 与小程序 miniprogram/app.wxss 色彩体系对齐（市井烟火 × 生鲜元气）。
 *
 * 本文件是 admin-web 端唯一的品牌色 / 图表色板来源。
 * 请勿在各页面 / 图表里硬编码色值——统一从这里引入。
 */

// 品牌主色（对齐 app.wxss 令牌，括号内为对应 CSS 变量名）
export const brand = {
  primary: '#1E7A57', // --green-700
  primaryHover: '#23916A', // --green-600
  success: '#23916A', // --green-600
  warning: '#D98F1F', // --corn-600
  error: '#E2503E', // --tomato
  info: '#3B82F6', // --info
  grape: '#8B6FC4', // --grape
}

// 分类图表色板（6 色，品牌感知、色相区分清晰，可循环复用）
// 顺序：绿 → 蓝 → 金 → 紫 → 青 → 番茄红
export const chartPalette = [
  '#1E7A57', // green
  '#3B82F6', // info
  '#F2A93B', // corn
  '#8B6FC4', // grape
  '#0891B2', // teal
  '#E2503E', // tomato
]

// 暗色背景下仍可读的图表色板（保留以备深色图表扩展）
export const chartPaletteDark = ['#2FB67D', '#5B9BF0', '#F5BD5C', '#A48ED4', '#3FB4CC', '#EF6E5D']

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

// 注入到 :root 的 CSS 变量（供 index.css 及内联样式消费，与 brand 保持同步）
export const cssVars = {
  '--qg-primary': brand.primary,
  '--qg-primary-hover': brand.primaryHover,
  '--qg-success': brand.success,
  '--qg-warning': brand.warning,
  '--qg-error': brand.error,
  '--qg-info': brand.info,
  '--qg-grape': brand.grape,
  '--qg-corn': '#F2A93B',
  '--qg-teal': '#0891B2',
}
