/**
 * 拍照识货页面 — 商品视觉识别
 * 拍照/相册选图 → 压缩 → YOLO识别 → 填写入库信息 → 确认入库
 */
var app = getApp();

Page({
  data: {
    imagePath: '',
    recognizing: false,
    recognized: false,
    recognizeFailed: false,
    detections: [],
    suggestedProduct: null,
    selectedIndex: 0,
    event_type: 'purchase',
    quantity: '',
    unit: '斤',
    unitCost: '',
    demoMode: false,
    submitting: false,
    submitted: false,
    submitResult: null,
    categories: [],
    filteredCategories: [],
    categoriesLoading: false,
    showProductPicker: false,
    productPickerFilter: '',
    processingTime: 0,
    source: '',
    recognizedName: '',
    recognizedConfidence: 0,
    recognizedConfidencePercent: 0,
    confidenceTag: 'green',
    manualProduct: null,
    skinClass: '',
  },

  onLoad: function () {
    this.loadCategories();
  },

  onShow: function () {
    this.setData({ skinClass: 'skin-' + app.resolveSkin() });
    if (this.data.categories.length === 0) {
      this.loadCategories();
    }
  },

  // ── 商品分类 ──────────────────────────────────────────

  loadCategories: function () {
    var self = this;
    this.setData({ categoriesLoading: true });
    app.request({ url: '/vision/categories' }).then(function (data) {
      var list = data || [];
      self.setData({
        categories: list,
        filteredCategories: list,
        categoriesLoading: false,
      });
    }).catch(function () {
      self.setData({ categoriesLoading: false, categories: [], filteredCategories: [] });
      wx.showToast({ title: '商品目录加载失败', icon: 'none' });
    });
  },

  // ── 演示模式 ──────────────────────────────────────────

  toggleDemoMode: function (e) {
    this.setData({ demoMode: e.detail.value });
  },

  // ── 拍照 / 相册 ───────────────────────────────────────

  takePhoto: function () {
    var self = this;
    wx.chooseMedia({
      count: 1,
      mediaType: ['image'],
      sourceType: ['camera'],
      camera: 'back',
      success: function (res) {
        var path = res.tempFiles[0].tempFilePath;
        self.onImageSelect(path);
      },
      fail: function (err) { self.handleMediaError(err); },
    });
  },

  chooseFromAlbum: function () {
    var self = this;
    wx.chooseMedia({
      count: 1,
      mediaType: ['image'],
      sourceType: ['album'],
      success: function (res) {
        var path = res.tempFiles[0].tempFilePath;
        self.onImageSelect(path);
      },
      fail: function (err) { self.handleMediaError(err); },
    });
  },

  handleMediaError: function (err) {
    var msg = (err && err.errMsg) || '';
    if (msg.indexOf('cancel') >= 0) return;
    wx.showToast({ title: '无法读取图片，请检查相机或相册权限', icon: 'none' });
  },

  retakePhoto: function () {
    // 只重置状态,不自动开相机(用户可主动点拍照/相册按钮重新选择)
    this.setData({
      imagePath: '', recognizing: false, recognized: false, recognizeFailed: false,
      detections: [], suggestedProduct: null, submitted: false, submitResult: null,
      quantity: '', unitCost: '',
    });
  },

  onImageSelect: function (filePath) {
    var self = this;
    this.setData({
      imagePath: filePath,
      recognizing: true,
      recognized: false,
      recognizeFailed: false,
      submitted: false,
      detections: [],
      manualProduct: null,
    });
    this.compressImage(filePath, function (compressed) {
      self.setData({ imagePath: compressed });
      self.recognizeImage();
    });
  },

  // ── 图片压缩 (压缩到 1280px 以下) ─────────────────────

  compressImage: function (filePath, callback) {
    wx.getImageInfo({
      src: filePath,
      success: function (info) {
        var maxSize = 1280;
        var compressWidth = info.width;
        if (info.width > maxSize || info.height > maxSize) {
          if (info.width >= info.height) {
            compressWidth = maxSize;
          } else {
            compressWidth = Math.round(info.width * maxSize / info.height);
          }
        }
        wx.compressImage({
          src: filePath,
          quality: 80,
          compressedWidth: compressWidth,
          success: function (res) {
            callback(res.tempFilePath);
          },
          fail: function () {
            callback(filePath);
          },
        });
      },
      fail: function () {
        callback(filePath);
      },
    });
  },

  // ── 识别 ───────────────────────────────────────────────

  recognizeImage: function () {
    var self = this;
    if (!this.data.imagePath) {
      wx.showToast({ title: '请先选择图片', icon: 'none' });
      return;
    }

    this.setData({
      recognizing: true,
      recognized: false,
      recognizeFailed: false,
      detections: [],
    });

    app.uploadFile({
      url: '/vision/recognize',
      filePath: this.data.imagePath,
      formData: {
        demo_mode: this.data.demoMode ? 'true' : 'false',
      },
    }).then(function (data) {
      var detections = data.detections || [];
      if (detections.length === 0) {
        self.setData({
          recognizing: false,
          recognizeFailed: true,
        });
        return;
      }
      var top = detections.slice(0, 3).map(function (d) {
        var conf = d.confidence || 0;
        return {
          product_id: d.product_id,
          name: d.name,
          confidence: conf,
          confidencePercent: Math.round(conf * 100),
          confidenceTag: self.getConfidenceTag(conf),
        };
      });
      var suggested = data.suggested_product || top[0];
      var first = top[0];
      self.setData({
        recognizing: false,
        recognized: true,
        detections: top,
        suggestedProduct: suggested,
        selectedIndex: 0,
        processingTime: data.processing_time_ms || 0,
        source: data.source || '',
        recognizedName: first.name,
        recognizedConfidence: first.confidence,
        recognizedConfidencePercent: first.confidencePercent,
        confidenceTag: first.confidenceTag,
        quantity: '',
        unitCost: '',
        submitted: false,
      });
    }).catch(function () {
      self.setData({
        recognizing: false,
        recognizeFailed: true,
      });
    });
  },

  getConfidenceTag: function (conf) {
    if (conf > 0.85) return 'green';
    if (conf >= 0.7) return 'amber';
    return 'red';
  },

  // ── 候选切换 ──────────────────────────────────────────

  selectCandidate: function (e) {
    var index = e.currentTarget.dataset.index;
    var item = this.data.detections[index];
    if (!item) return;
    this.setData({
      selectedIndex: index,
      recognizedName: item.name,
      recognizedConfidence: item.confidence,
      recognizedConfidencePercent: item.confidencePercent,
      confidenceTag: item.confidenceTag,
      manualProduct: null,
      submitted: false,
    });
  },

  // ── 手动选择商品 ───────────────────────────────────────

  manualSelectProduct: function () {
    if (this.data.categories.length === 0) {
      this.loadCategories();
    }
    this.setData({
      showProductPicker: true,
      productPickerFilter: '',
      filteredCategories: this.data.categories,
    });
  },

  onProductFilterInput: function (e) {
    var keyword = (e.detail.value || '').trim().toLowerCase();
    var filtered;
    if (!keyword) {
      filtered = this.data.categories;
    } else {
      filtered = this.data.categories.filter(function (c) {
        return (c.name || '').toLowerCase().indexOf(keyword) >= 0;
      });
    }
    this.setData({
      productPickerFilter: e.detail.value,
      filteredCategories: filtered,
    });
  },

  selectProductFromList: function (e) {
    var ds = e.currentTarget.dataset;
    var product = {
      product_id: ds.id,
      name: ds.name,
      confidence: 1,
      confidencePercent: 100,
      confidenceTag: 'green',
    };
    this.setData({
      showProductPicker: false,
      manualProduct: product,
      recognized: true,
      recognizeFailed: false,
      recognizing: false,
      recognizedName: product.name,
      recognizedConfidence: 1,
      recognizedConfidencePercent: 100,
      confidenceTag: 'green',
      detections: [product],
      selectedIndex: 0,
      source: '手动选择',
      unit: ds.unit || this.data.unit,
      submitted: false,
    });
  },

  closeProductPicker: function () {
    this.setData({ showProductPicker: false });
  },

  noop: function () {},

  // ── 入库表单 ──────────────────────────────────────────

  onEventTypeChange: function (e) {
    this.setData({ event_type: e.currentTarget.dataset.type });
  },

  onQuantityInput: function (e) {
    this.setData({ quantity: e.detail.value });
  },

  onUnitInput: function (e) {
    this.setData({ unit: e.detail.value });
  },

  onUnitCostInput: function (e) {
    this.setData({ unitCost: e.detail.value });
  },

  // ── 确认入库 ──────────────────────────────────────────

  confirmStockIn: function () {
    var self = this;
    if (this.data.submitting) return;

    var product = this.getCurrentProduct();
    if (!product) {
      wx.showToast({ title: '请选择商品', icon: 'none' });
      return;
    }
    var qtyText = String(this.data.quantity || '').trim();
    var qty = Number(qtyText);
    if (!isFinite(qty) || qty <= 0) {
      wx.showToast({ title: '请输入大于0的有效数量', icon: 'none' });
      return;
    }
    var unit = String(this.data.unit || '').trim();
    if (!unit) {
      wx.showToast({ title: '请输入计量单位', icon: 'none' });
      return;
    }
    var unitPrice = Number(this.data.unitCost);
    if (!isFinite(unitPrice) || unitPrice <= 0) {
      wx.showToast({ title: this.data.event_type === 'purchase' ? '请输入有效成本' : '请输入有效售价', icon: 'none' });
      return;
    }

    this.setData({ submitting: true });

    var verb = this.data.event_type === 'purchase' ? '进了' : '卖了';
    var text = verb + product.name + qty + unit;
    // 采购和销售都把单价写入文本，让语音解析器分别落 unit_cost / unit_price。
    text += '每' + unit + unitPrice + '元';

    app.request({
      url: '/voice/parse-text',
      method: 'POST',
      data: { text: text },
    }).then(function (res) {
      var parsed = res.parsed || {};
      var voiceLogId = parsed.voice_log_id || res.voice_log_id;
      if (!voiceLogId) {
        self.setData({ submitting: false });
        wx.showToast({ title: '解析失败', icon: 'none' });
        return;
      }
      app.request({
        url: '/voice/confirm',
        method: 'POST',
        data: { voice_log_id: voiceLogId },
      }).then(function (confirmRes) {
        var result = confirmRes || {};
        result.product = product.name;
        result.quantity = qty;
        result.unit = unit;
        result.event_type = self.data.event_type;
        self.setData({
          submitting: false,
          submitted: true,
          submitResult: result,
        });
        wx.showToast({ title: self.data.event_type === 'purchase' ? '入库成功' : '出库成功', icon: 'success' });
      }).catch(function (err) {
        self.setData({ submitting: false });
        wx.showToast({ title: (err && err.body && err.body.detail) || (self.data.event_type === 'purchase' ? '入库失败，请重试' : '出库失败，请重试'), icon: 'none' });
      });
    }).catch(function () {
      self.setData({ submitting: false });
      wx.showToast({ title: '解析失败，请重试', icon: 'none' });
    });
  },

  getCurrentProduct: function () {
    if (this.data.manualProduct) {
      return this.data.manualProduct;
    }
    if (this.data.selectedIndex < this.data.detections.length) {
      return this.data.detections[this.data.selectedIndex];
    }
    return null;
  },

  // ── 重置 ───────────────────────────────────────────────

  reset: function () {
    this.setData({
      imagePath: '',
      recognizing: false,
      recognized: false,
      recognizeFailed: false,
      detections: [],
      suggestedProduct: null,
      selectedIndex: 0,
      quantity: '',
      unitCost: '',
      submitting: false,
      submitted: false,
      submitResult: null,
      manualProduct: null,
      processingTime: 0,
      source: '',
      recognizedName: '',
      recognizedConfidence: 0,
      recognizedConfidencePercent: 0,
      confidenceTag: 'green',
    });
  },
});
