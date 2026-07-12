/**
 * 千摊智脑 · 主题化线描图标组件
 * 通过 CSS mask 渲染 SVG，颜色跟随父级/传入 color（默认跟随 --ink-2）。
 * 用法：<icon name="leaf" size="md" color="var(--green-700)"/>
 */
Component({
  properties: {
    name: { type: String, value: '' },
    size: { type: String, value: 'md' },   // sm | md | lg
    color: { type: String, value: '' },
  },
});
