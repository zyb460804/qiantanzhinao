/**
 * 千摊智脑 · 品牌吉祥物「小智」
 * 生鲜绿芽身 + 嫩芽 + 微笑 + 番茄红脸蛋，用于引导 / AI 头像 / 空状态。
 * 用法：<mascot size="md" mood="idle"/>
 * mood: idle(默认) | think(思考摇晃) | cheer(庆祝跳跃)
 */
Component({
  properties: {
    size: { type: String, value: 'md' },  // sm | md | lg
    mood: { type: String, value: 'idle' },
  },
});
