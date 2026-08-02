
# 修复：macOS 系统窗口级半透明磨砂未生效

## 问题

设置透明主题后，窗口仍然是实色的「#111417」。系统级磨砂（vibrancy under-window）没有生效，所有透明度仅停留在 CSS 层。

## 根因

在 `electron/main.js` 的 `createWindow()` 中：

```js
mainWindow = new BrowserWindow({
    backgroundColor: '#111417',  // ← 窗口创建时就完全不透明
    // 没有设置 transparent: true
    // 没有设置 vibrancy 构造参数
});
```

当前 `setTranslucent()` 函数调用了 `setVibrancy('under-window')`，但 Electron 43 要求窗口必须以 `transparent: true` 创建，`setVibrancy()` 才能生效。之前的 `setTransparent(true/false)` 方法在 Electron 43 中已被移除。

## 解决方案

### 方案：构造时统一设置 `transparent: true` + `vibrancy`，运行时切换

在 macOS 上窗口始终以透明方式创建。通过构造函数内置的 `vibrancy` 选项和 `backgroundColor` 的 alpha 通道来控制磨砂/不透明的切换。

### 具体改动：`electron/main.js`

#### 改动 1：`createWindow()` 构造参数

```js
// 当前（❌ 不行）
mainWindow = new BrowserWindow({
    backgroundColor: '#111417',
    // transparent: true 没设
    // vibrancy 没设
    ...(process.platform === 'darwin'
      ? { titleBarStyle: 'hidden', trafficLightPosition: { x: 14, y: 14 } }
      : {}),
});

// 改为（✅ 在 darwin 上开启透明 + vibrancy）
mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    show: false,
    icon: themedMonochromeAssetPath('cw-icon'),
    backgroundColor: '#111417',  // 非 darwin 保持不透明
    ...(process.platform === 'darwin'
      ? {
          titleBarStyle: 'hidden',
          trafficLightPosition: { x: 14, y: 14 },
          transparent: true,         // 窗口透明
          vibrancy: 'under-window',  // 窗口级磨砂
          visualEffectState: 'followWindow',
        }
      : {}),
    webPreferences: { /*...*/ },
});
```

#### 改动 2：修改 `setTranslucent()` 函数

```js
// 当前（❌ 用 setVibrancy 切换 opaque/transparent，窗口本身不透明所以无效）
function setTranslucent(enabled) {
  if (enabled) {
    mainWindow.setVibrancy('under-window');
    mainWindow.setBackgroundColor('#00000000');
  } else {
    mainWindow.setVibrancy(null);
    mainWindow.setBackgroundColor('#111417');
  }
}

// 改为（✅ 通过 backgroundColor alpha 切换磨砂效果）
// 在 darwin 上，窗口已带 transparent: true + vibrancy: under-window 构造
// 切换时只需改 backgroundColor 的 alpha：
// - '#00000000' → 完全透明，底层桌面模糊透出
// - '#111417'  → 不透明深色（vibrancy 会被忽略，显示纯色背景）
function setTranslucent(enabled) {
  if (process.platform !== 'darwin' || !mainWindow || mainWindow.isDestroyed()) return;
  if (enabled) {
    mainWindow.setBackgroundColor('#00000000');
  } else {
    mainWindow.setBackgroundColor('#111417');
  }
}
```

原理：在 Electron 43 macOS 中，
- `transparent: true` 窗口 + `vibrancy: 'under-window'` = 窗口内容完全透明，背景有磨砂
- `backgroundColor` 为 `#00000000`（透明）时，磨砂效果最强
- `backgroundColor` 为 `#111417`（不透明）时，vibrancy 被忽略，窗口变回深色

这不需要调用 `setVibrancy(null)` 来关闭磨砂——它自然生效，因为 `#111417` 没有 alpha 通道，直接覆盖。

#### 改动 3：清理未使用的辅助函数

移除 `transparentOff()` 和 `refreshTranslucent()`，逻辑简化为只修改 `backgroundColor`。

## 涉及文件

| 文件 | 改动类型 | 行数 |
|------|----------|------|
| `electron/main.js` | `createWindow()` 构造参数变更 + `setTranslucent()` 重写 | ~10行 |

## 验证

1. 启动 App（非透明主题） → 窗口应为正常「#111417」深色不透明
2. 打开设置 → 开启透明主题 → 窗口变磨砂，能看到模糊的桌面背景
3. 关闭透明主题 → 窗口恢复「#111417」不透明
4. 在非 macOS 平台 → 行为不变（无 `transparent`/`vibrancy` 选项）

## 注意事项

- `transparent: true` 在 macOS 上要求 `titleBarStyle: 'hidden'`，项目已有此配置 ✅
- `vibrancy` 是 `BrowserWindow` 构造选项，Electron 43 仍然支持 ✅
- `backgroundColor` 的 8 位 hex (`#RRGGBBAA`) 需要 `transparent: true` 才支持 ✅
- 非 darwin 平台不变 ✅
