/**
 * record-card 记账记录卡片组件
 *
 * 展示一条解析后的记账事件(采购/销售/损耗/支出),支持只读和可操作两种模式。
 * voice 页用于"确认入库"前的预览;index 页用于最近记录展示。
 *
 * 支出(expense)事件(v3.3):契约约定 quantity/unit_cost/unit_price 为空、
 * 只有 total_amount,卡片因此只展示「支/日常支出」徽章 + 金额,不放数量/单价行。
 *
 * 用法:
 *   <record-card record="{{parsed}}" show-actions="{{true}}"
 *                bind:confirm="onConfirm" bind:correct="onCorrect" />
 *
 * record 结构(与 /voice/parse-text 响应一致):
 *   { event_type, product, quantity, unit, unit_cost, unit_price,
 *     total_amount, confidence, missing_fields, voice_log_id }
 */
Component({
  properties: {
    record: {
      type: Object,
      value: null,
    },
    // 是否显示操作按钮(修正/确认)
    showActions: {
      type: Boolean,
      value: false,
    },
    // 是否显示撤销/修改按钮(已确认记录用)
    showVoidAction: {
      type: Boolean,
      value: false,
    },
    // 紧凑模式(首页最近记录用)
    compact: {
      type: Boolean,
      value: false,
    },
  },

  methods: {
    onConfirm: function () {
      this.triggerEvent('confirm', { record: this.data.record });
    },

    onCorrect: function () {
      this.triggerEvent('correct', { record: this.data.record });
    },

    onVoid: function () {
      this.triggerEvent('void', { record: this.data.record });
    },

    onEdit: function () {
      this.triggerEvent('edit', { record: this.data.record });
    },
  },
});
