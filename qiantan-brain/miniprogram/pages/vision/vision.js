/**
 * 拍照识货页面 — 商品视觉识别
 * 拍照/相册选图 → 压缩 → YOLO识别 → 填写入库信息 → 确认入库
 */
var app = getApp();

// 语音解析器支持的基础单位白名单（与 backend/app/services/voice_parser.py
// _extract_quantity 保持一致）。两/份/克 等不在其中，会被默认回退为「斤」。
var PARSED_UNITS = ['斤', '公斤', '千克', '个', '把', '箱', '袋', '件'];

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

    // 单位兼容性预检：语音解析器仅识别 PARSED_UNITS 中的单位，其余会被默认回退为「斤」，
    // 造成账实不符（如「2 两」会被记成「2 斤」）。提示用户先换算。
    if (PARSED_UNITS.indexOf(unit) < 0) {
      var supportedTip = '语音解析仅支持「' + PARSED_UNITS.join('/') + '」；当前单位「' + unit + '」会被当作「斤」入库。';
      wx.showModal({
        title: '单位不被识别',
        content: supportedTip + '是否仍要提交？',
        confirmText: '仍要提交',
        cancelText: '去修改',
        success: function (m) {
          if (m.confirm) self._beginParseAndConfirm(product, qty, unit, unitPrice);
        }
      });
      return;
    }
    this._beginParseAndConfirm(product, qty, unit, unitPrice);
  },

  /** 走 voice/parse-text → voice/confirm 两步链路入库。 */
  _beginParseAndConfirm: function (product, qty, unit, unitPrice) {
    var self = this;
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
      // 商品名识别检查：parser 未识别时 product 为 null/undefined，
      // 后续 /voice/confirm 会 400「商品 XXX 未在品类表中找到」。
      // 这里前置拦截，给出明确指引而非让 confirm 直接报错。
      if (!parsed.product) {
        self.setData({ submitting: false });
        wx.showModal({
          title: '商品名未被解析器识别',
          content: '「' + product.name + '」不在系统品类表中。请改用「手动选择」从目录里挑一个匹配商品，再确认入库。',
          confirmText: '去手选',
          cancelText: '取消',
          showCancel: true,
          success: function (m) {
            if (m.confirm) self.manualSelectProduct();
          }
        });
        return;
      }
      // 数量/单位一致性核对：若 parser 解析出的值与用户输入不一致，
      // 提示用户存在账实不符风险，避免 UI 静默覆盖后端真实数据。
      var parsedQty = parsed.quantity;
      var parsedUnit = parsed.unit;
      if ((parsedQty !== undefined && parsedQty !== null && Math.abs(Number(parsedQty) - qty) > 0.0001) ||
          (parsedUnit && parsedUnit !== unit)) {
        self.setData({ submitting: false });
        wx.showModal({
          title: '解析结果与输入不一致',
          content: '系统解析为 ' + (parsedQty !== undefined ? parsedQty : '?') +
                   (parsedUnit || '') + '，与你输入的 ' + qty + unit + ' 不同。\n' +
                   '入库会以解析结果为准。建议重新输入或改用克/两换算到斤。',
          confirmText: '仍要继续',
          cancelText: '去修改',
          success: function (m) {
            if (m.confirm) self._doConfirm(self.data.event_type, res);
          }
        });
        return;
      }
      self._doConfirm(self.data.event_type, res);
    }).catch(function () {
      self.setData({ submitting: false });
      wx.showToast({ title: '解析失败，请重试', icon: 'none' });
    });
  },

  /** 调 /voice/confirm，并展示后端实际写入的值（避免本地覆盖造成账实不符）。 */
  _doConfirm: function (eventType, parseRes) {
    var self = this;
    var parsed = (parseRes && parseRes.parsed) || {};
    var voiceLogId = parsed.voice_log_id || (parseRes && parseRes.voice_log_id);
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
      // 直接用后端返回的真实写入值，不再用本地 qty/unit/product 覆盖，
      // 保证 UI 与 InventoryRecord 一致，便于摊主发现解析偏差。
      var result = confirmRes || {};
      result.event_type = eventType;
      self.setData({
        submitting: false,
        submitted: true,
        submitResult: result,
      });
      wx.showToast({ title: eventType === 'purchase' ? '入库成功' : '出库成功', icon: 'success' });
    }).catch(function (err) {
      self.setData({ submitting: false });
      wx.showToast({ title: (err && err.body && err.body.detail) || (eventType === 'purchase' ? '入库失败，请重试' : '出库失败，请重试'), icon: 'none' });
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
