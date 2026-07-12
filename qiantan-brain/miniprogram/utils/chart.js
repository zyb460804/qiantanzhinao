/**
 * 统一 Canvas 2D 图表工具 — chart.js
 *
 * 消除 sandbox / dashboard / report 三处重复的 Canvas 图表绘制代码。
 * 提供柱状图和折线图两种图表类型，通过 options 配置样式。
 *
 * 使用方式：
 *   var Chart = require('../../utils/chart');
 *   Chart.drawLineChart(ctx, width, height, data, { series: [...] });
 *   Chart.drawBarChart(ctx, width, height, data, { valueKey: 'net_profit', ... });
 */

// ── Canvas 初始化辅助 ──────────────────────────────

/**
 * 初始化 Canvas 2D 上下文 (处理 DPR 缩放)。
 * @param {Object} page    - Page 实例 (用于 createSelectorQuery)
 * @param {string} selector - Canvas 选择器 (如 '#myCanvas')
 * @returns {Promise<{ctx, width, height}>}
 */
function initCanvas(page, selector) {
  return new Promise(function (resolve) {
    var query = wx.createSelectorQuery().in(page);
    query.select(selector)
      .fields({ node: true, size: true })
      .exec(function (res) {
        if (!res[0] || !res[0].node) { resolve(null); return; }
        var canvas = res[0].node;
        var ctx = canvas.getContext('2d');
        var dpr = wx.getWindowInfo().pixelRatio;
        var w = res[0].width;
        var h = res[0].height;
        canvas.width = w * dpr;
        canvas.height = h * dpr;
        ctx.scale(dpr, dpr);
        resolve({ ctx: ctx, width: w, height: h });
      });
  });
}

// ── 通用工具 ───────────────────────────────────────

function formatMoney(value) {
  if (value >= 10000) return '¥' + (value / 10000).toFixed(1) + '万';
  if (value >= 1000) return '¥' + (value / 1000).toFixed(1) + 'k';
  return '¥' + Math.round(value);
}

function formatAov(value) {
  return '¥' + (Math.round(value * 10) / 10);
}

// ── 默认样式 ───────────────────────────────────────

var DEFAULT_PAD = { top: 22, right: 54, bottom: 34, left: 48 };

/**
 * 绘制折线图 (双轴: 左轴金额 / 右轴客单价)。
 *
 * @param {CanvasContext} ctx
 * @param {number} w  - Canvas 逻辑宽度
 * @param {number} h  - Canvas 逻辑高度
 * @param {Array}  data - 数据点数组, 每项含 { date, revenue, profit, customer_price }
 * @param {Object} opts
 * @param {Array}  opts.series       - 序列定义 [{ key, color, axis: 'left'|'right' }]
 * @param {Object} opts.fillArea     - 区域填充 { key, gradientFrom, gradientTo }
 * @param {Object} opts.pad          - 内边距 (可选, 默认 DEFAULT_PAD)
 * @param {number} opts.maxPoints    - 显示数据点上限 (超过则采样, 默认 14)
 * @param {number} opts.maxRight     - 右轴强制最大值 (可选)
 * @param {number} opts.maxLeft      - 左轴强制最大值 (可选)
 */
function drawLineChart(ctx, w, h, data, opts) {
  if (!data || !data.length || !ctx) return;

  var pad = opts.pad || DEFAULT_PAD;
  var series = opts.series || [];
  var fillArea = opts.fillArea || null;
  var maxPoints = opts.maxPoints || 14;

  var chartW = w - pad.left - pad.right;
  var chartH = h - pad.top - pad.bottom;

  ctx.clearRect(0, 0, w, h);
  if (!data.length) return;

  // 采样: 超过 maxPoints 个点时抽取
  var drawData = data;
  var count = data.length;
  if (count > maxPoints) {
    var step = Math.ceil(count / maxPoints);
    drawData = [];
    for (var i = 0; i < count; i += step) { drawData.push(data[i]); }
    if (drawData[drawData.length - 1] !== data[count - 1]) {
      drawData.push(data[count - 1]);
    }
    count = drawData.length;
  }

  // 计算双轴范围
  var leftVals = [], rightVals = [];
  drawData.forEach(function (d) {
    series.forEach(function (s) {
      if (s.axis === 'right') rightVals.push(Number(d[s.key]) || 0);
      else leftVals.push(Number(d[s.key]) || 0);
    });
  });

  var maxLeft = opts.maxLeft;
  if (!maxLeft) {
    var ml = Math.max.apply(null, leftVals.concat([1]));
    var mag = Math.pow(10, Math.max(0, String(Math.floor(ml)).length - 2));
    maxLeft = Math.ceil(ml / mag) * mag;
  }

  var maxRight = opts.maxRight;
  if (!maxRight) {
    var mr = Math.max.apply(null, rightVals.concat([1]));
    if (mr <= 10) maxRight = 10;
    else maxRight = Math.ceil(mr / (mr >= 100 ? 10 : 5)) * (mr >= 100 ? 10 : 5);
  }

  // 坐标映射函数
  var pointX = function (index) {
    return count === 1 ? pad.left + chartW / 2 : pad.left + index / (count - 1) * chartW;
  };
  var pointYL = function (value) {
    return pad.top + chartH - (Number(value) || 0) / maxLeft * chartH;
  };
  var pointYR = function (value) {
    return pad.top + chartH - (Number(value) || 0) / maxRight * chartH;
  };

  // 网格线 + 双轴刻度
  ctx.font = '10px sans-serif';
  ctx.textBaseline = 'middle';
  for (var g = 0; g <= 4; g++) {
    var gy = pad.top + g / 4 * chartH;
    ctx.beginPath();
    if (ctx.setLineDash) ctx.setLineDash([3, 4]);
    ctx.moveTo(pad.left, gy);
    ctx.lineTo(w - pad.right, gy);
    ctx.strokeStyle = g === 4 ? '#C9D5CD' : '#E4EAE5';
    ctx.lineWidth = 1;
    ctx.stroke();
    if (ctx.setLineDash) ctx.setLineDash([]);
    ctx.fillStyle = '#8A938D';
    ctx.textAlign = 'right';
    ctx.fillText(formatMoney(maxLeft * (1 - g / 4)), pad.left - 7, gy);
    ctx.fillStyle = '#2E7DD1';
    ctx.textAlign = 'left';
    ctx.fillText(formatAov(maxRight * (1 - g / 4)), w - pad.right + 6, gy);
  }

  // 区域填充 (仅第一条左轴序列)
  if (count > 1 && fillArea) {
    var gradient = ctx.createLinearGradient(0, pad.top, 0, pad.top + chartH);
    gradient.addColorStop(0, fillArea.gradientFrom || 'rgba(23,92,69,.20)');
    gradient.addColorStop(1, fillArea.gradientTo || 'rgba(23,92,69,0)');
    ctx.beginPath();
    drawData.forEach(function (d, i) {
      var x = pointX(i);
      var y = pointYL(d[fillArea.key]);
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.lineTo(pointX(count - 1), pad.top + chartH);
    ctx.lineTo(pointX(0), pad.top + chartH);
    ctx.closePath();
    ctx.fillStyle = gradient;
    ctx.fill();
  }

  // 绘制折线 + 数据点
  series.forEach(function (s) {
    var py = s.axis === 'right' ? pointYR : pointYL;
    ctx.beginPath();
    drawData.forEach(function (d, i) {
      var x = pointX(i);
      var y = py(d[s.key]);
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.strokeStyle = s.color;
    ctx.lineWidth = 2.5;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    if (count > 1) ctx.stroke();
    drawData.forEach(function (d, i) {
      var x = pointX(i);
      var y = py(d[s.key]);
      ctx.beginPath();
      ctx.arc(x, y, 4, 0, Math.PI * 2);
      ctx.fillStyle = '#FFFEFA';
      ctx.fill();
      ctx.strokeStyle = s.color;
      ctx.lineWidth = 2.2;
      ctx.stroke();
    });
  });

  // X 轴日期标签
  ctx.fillStyle = '#7B8780';
  ctx.font = '10px sans-serif';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'alphabetic';
  var labelStep = 1;
  if (count > 7) labelStep = Math.ceil(count / 6);
  drawData.forEach(function (d, i) {
    if (i % labelStep !== 0 && i !== count - 1) return;
    var label = (d.date || '').slice(5).replace('-', '/');
    ctx.fillText(label, pointX(i), h - 8);
  });
}

// ── 柱状图 (正负双向, 带推荐标记) ─────────────────

var BAR_PAD = { top: 34, right: 18, bottom: 58, left: 40 };

/**
 * 绘制双向柱状图 (正负值, 推荐方案标记)。
 *
 * @param {CanvasContext} ctx
 * @param {number} w
 * @param {number} h
 * @param {Array}  data    - 方案数组
 * @param {Object} opts
 * @param {string} opts.valueKey        - 值字段名 (默认 'net_profit')
 * @param {string} opts.labelKey        - 标签字段名 (默认 'name')
 * @param {string} opts.subKey          - 副标签字段名 (如 'purchase_qty')
 * @param {string} opts.subSuffix       - 副标签后缀 (如 '斤')
 * @param {Array}  opts.colors          - 颜色数组 (默认 ['#78A890','#175C45','#F3A83B'])
 * @param {number} opts.bestIndex       - 推荐方案索引 (自动计算最大值)
 * @param {string} opts.unitPrefix      - 值前缀 (默认 '¥')
 * @param {string} opts.recommendLabel  - 推荐标签文字 (默认 '推荐')
 * @param {Object} opts.pad             - 内边距 (可选)
 */
function drawBarChart(ctx, w, h, data, opts) {
  if (!data || !data.length || !ctx) return;

  var pad = opts.pad || BAR_PAD;
  var valueKey = opts.valueKey || 'net_profit';
  var labelKey = opts.labelKey || 'name';
  var subKey = opts.subKey || '';
  var subSuffix = opts.subSuffix || '';
  var colors = opts.colors || ['#78A890', '#175C45', '#F3A83B'];
  var unitPrefix = opts.unitPrefix !== undefined ? opts.unitPrefix : '¥';
  var recommendLabel = opts.recommendLabel || '推荐';

  var chartW = w - pad.left - pad.right;
  var chartH = h - pad.top - pad.bottom;
  var count = data.length;

  ctx.clearRect(0, 0, w, h);

  var values = data.map(function (item) { return Number(item[valueKey]) || 0; });
  var maxAbs = Math.max.apply(null, values.map(Math.abs).concat([1]));
  maxAbs = Math.ceil(maxAbs / 10) * 10;
  var zeroY = pad.top + chartH / 2;
  var groupW = chartW / count;
  var barW = Math.max(26, Math.min(52, groupW * 0.48));

  // 推荐方案: 自动找最大值, 或使用 opts.bestIndex
  var bestIndex = opts.bestIndex;
  if (bestIndex === undefined) {
    bestIndex = 0;
    values.forEach(function (v, i) { if (v > values[bestIndex]) bestIndex = i; });
  }

  function roundedBar(x, y, width, height, radius) {
    var r = Math.min(radius, width / 2, height / 2);
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + width, y, x + width, y + height, r);
    ctx.arcTo(x + width, y + height, x, y + height, r);
    ctx.arcTo(x, y + height, x, y, r);
    ctx.arcTo(x, y, x + width, y, r);
    ctx.closePath();
  }

  // 水平参考线
  [-1, 0, 1].forEach(function (step) {
    var y = zeroY - step * chartH / 2;
    ctx.beginPath();
    if (ctx.setLineDash && step !== 0) ctx.setLineDash([3, 4]);
    ctx.moveTo(pad.left, y);
    ctx.lineTo(w - pad.right, y);
    ctx.strokeStyle = step === 0 ? '#B9C8BF' : '#E5EAE6';
    ctx.lineWidth = step === 0 ? 1.2 : 1;
    ctx.stroke();
    if (ctx.setLineDash) ctx.setLineDash([]);
  });

  // Y 轴刻度
  ctx.fillStyle = '#8A938D';
  ctx.font = '9px sans-serif';
  ctx.textAlign = 'right';
  ctx.textBaseline = 'middle';
  ctx.fillText('+' + Math.round(maxAbs), pad.left - 5, pad.top);
  ctx.fillText('0', pad.left - 5, zeroY);
  ctx.fillText('-' + Math.round(maxAbs), pad.left - 5, pad.top + chartH);

  // 绘制柱体
  data.forEach(function (item, i) {
    var centerX = pad.left + groupW * (i + 0.5);
    var x = centerX - barW / 2;
    var value = values[i];
    var actualH = Math.abs(value) / maxAbs * chartH / 2;
    var visualH = Math.max(actualH, 3);
    var y = value >= 0 ? zeroY - visualH : zeroY;
    var color = value < 0 ? '#D9524A' : colors[i % colors.length];

    roundedBar(x, y, barW, visualH, 8);
    ctx.fillStyle = color;
    ctx.fill();

    // 数值标签
    ctx.fillStyle = color;
    ctx.font = '700 12px sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'alphabetic';
    var valueY = value >= 0 ? Math.max(13, y - 7) : Math.min(h - pad.bottom + 14, y + visualH + 16);
    var sign = value >= 0 ? '+' : '-';
    ctx.fillText(sign + unitPrefix + Math.abs(value).toFixed(0), centerX, valueY);

    // 推荐标记
    if (i === bestIndex) {
      var pillY = Math.max(2, valueY - 25);
      roundedBar(centerX - 18, pillY, 36, 17, 8.5);
      ctx.fillStyle = '#E7F1EB';
      ctx.fill();
      ctx.fillStyle = '#175C45';
      ctx.font = '700 9px sans-serif';
      ctx.textBaseline = 'middle';
      ctx.fillText(recommendLabel, centerX, pillY + 8.5);
    }

    // 方案名称
    ctx.fillStyle = '#263B31';
    ctx.font = '600 11px sans-serif';
    ctx.textBaseline = 'alphabetic';
    ctx.fillText(item[labelKey] || ['保守', '标准', '激进'][i] || '方案', centerX, h - 25);

    // 副标签
    if (subKey) {
      ctx.fillStyle = '#8A938D';
      ctx.font = '9px sans-serif';
      ctx.fillText((item[subKey] || 0) + subSuffix, centerX, h - 9);
    }
  });
}

// ── 导出 ──────────────────────────────────────────

module.exports = {
  initCanvas: initCanvas,
  drawLineChart: drawLineChart,
  drawBarChart: drawBarChart,
};
