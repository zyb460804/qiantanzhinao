/**
 * sim-input 沙盘参数输入组件
 *
 * 决策沙盘的参数配置卡片:商品选择 + 进货量/进价/售价 + 运行按钮。
 * advisor 页内联沙盘和 sandbox 独立页共用。
 *
 * 用法:
 *   <sim-input
 *     products="{{simProducts}}"
 *     product-index="{{simProductIndex}}"
 *     purchase-qty="{{simPurchaseQty}}"
 *     unit-cost="{{simUnitCost}}"
 *     unit-price="{{simUnitPrice}}"
 *     loading="{{simLoading}}"
 *     bind:change="onSimField"
 *     bind:product-change="onSimProduct"
 *     bind:run="onRun"
 *   />
 *
 * 事件:
 *   change        — {field: 'purchaseQty', value: 50}
 *   product-change — {value: 0}
 *   run           — {}
 */
Component({
  properties: {
    // 商品名列表(用于 picker)
    products: {
      type: Array,
      value: [],
    },
    // 当前选中的商品索引
    productIndex: {
      type: Number,
      value: 0,
    },
    // 进货量
    purchaseQty: {
      type: Number,
      value: 50,
    },
    // 进货单价
    unitCost: {
      type: Number,
      value: 0.5,
    },
    // 售价
    unitPrice: {
      type: Number,
      value: 2.0,
    },
    // 运行中(按钮 loading)
    loading: {
      type: Boolean,
      value: false,
    },
    // 运行按钮文案
    runText: {
      type: String,
      value: '运行模拟',
    },
  },

  methods: {
    onFieldInput: function (e) {
      var field = e.currentTarget.dataset.field;
      var val = parseFloat(e.detail.value) || 0;
      this.triggerEvent('change', { field: field, value: val });
    },

    onProductChange: function (e) {
      this.triggerEvent('product-change', { value: parseInt(e.detail.value) });
    },

    onRun: function () {
      if (this.data.loading) return;
      this.triggerEvent('run');
    },
  },
});
