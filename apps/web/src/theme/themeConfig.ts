/**
 * Data Ocean → Ant Design 主题映射
 *
 * 按设计文档第 6.2 节，把语义令牌映射为 Ant Design `ThemeConfig`。
 * 必须对 Menu、Tabs、Table、Card、Button、Input、Select、Drawer、Modal、Tag、
 * Descriptions、Timeline、Alert、Progress、Tooltip 设置组件级 token，
 * 避免页面内继续覆盖默认白色和灰色。
 */
import type { ThemeConfig } from 'antd';
import { tokens } from './tokens';

/**
 * Data Ocean Ant Design 主题配置
 *
 * 设计基线为明亮的极地雾蓝：
 * - colorBgLayout 透明，由 OceanBackdrop 提供页面底色
 * - colorBgContainer 使用半透明 surface.default
 * - 组件级 token 覆盖默认白色面板，避免视觉割裂
 */
export const dataOceanTheme: ThemeConfig = {
  // 全局种子色
  token: {
    colorPrimary: tokens.ocean.action.primary,
    colorInfo: tokens.ocean.status.info,
    colorSuccess: tokens.ocean.status.success,
    colorWarning: tokens.ocean.status.warning,
    colorError: tokens.ocean.status.danger,
    colorTextBase: tokens.ocean.text.primary,
    colorText: tokens.ocean.text.primary,
    colorTextSecondary: tokens.ocean.text.secondary,
    colorTextTertiary: tokens.ocean.text.muted,
    colorTextQuaternary: tokens.ocean.text.muted,
    colorBgLayout: 'transparent',
    colorBgContainer: tokens.ocean.surface.default,
    colorBgElevated: tokens.ocean.surface.strong,
    colorBgSpotlight: tokens.ocean.surface.strong,
    colorBorder: 'rgba(24, 102, 133, 0.20)',
    colorBorderSecondary: tokens.ocean.border.subtle,
    colorSplit: 'rgba(24, 102, 133, 0.14)',
    borderRadius: 4,
    borderRadiusLG: 8,
    borderRadiusSM: 2,
    controlHeight: 34,
    controlHeightSM: 28,
    controlHeightLG: 40,
    fontSize: 14,
    fontSizeSM: 13,
    fontSizeLG: 16,
    fontFamily: tokens.typography.fontFamily,
    lineWidth: 1,
    wireframe: false,
    boxShadow:
      '0 12px 30px rgba(45, 104, 125, 0.10)',
    boxShadowSecondary:
      '0 24px 64px rgba(29, 78, 103, 0.18)',
  },

  // 组件级 token 覆盖，避免白色突兀面板
  components: {
    // 侧栏导航：浅雾蓝结构层
    Layout: {
      bodyBg: 'transparent',
      headerBg: 'transparent',
      siderBg: 'transparent',
      headerHeight: 56,
      headerPadding: '0 24px',
    },
    // 菜单：选中态由 ocean.css .ocean-sider-menu 的深潮渐变带接管，
    // 这里仅提供未覆盖场景的回退色
    Menu: {
      itemBg: 'transparent',
      subMenuItemBg: 'transparent',
      itemColor: tokens.ocean.text.primary,
      itemHoverColor: tokens.ocean.abyss.deep,
      itemHoverBg: 'rgba(14, 91, 132, 0.10)',
      itemSelectedColor: '#EAF6F9',
      itemSelectedBg: tokens.ocean.abyss.deep,
      itemHeight: 40,
      groupTitleColor: tokens.ocean.text.muted,
      itemBorderRadius: 2,
      activeBarBorderWidth: 0,
      activeBarHeight: 24,
    },
    // Tabs：底部细线选中
    Tabs: {
      itemColor: tokens.ocean.text.secondary,
      itemHoverColor: tokens.ocean.text.primary,
      itemSelectedColor: tokens.ocean.text.primary,
      inkBarColor: tokens.ocean.action.primary,
      itemActiveColor: tokens.ocean.text.primary,
      titleFontSize: 14,
      horizontalItemPadding: '8px 0',
      horizontalMargin: '0 0 12px 0',
    },
    // 表格：稳定 strong surface，表头浅雾蓝
    Table: {
      headerBg: 'rgba(142, 191, 208, 0.20)',
      headerColor: tokens.ocean.text.primary,
      headerSplitColor: 'rgba(24, 102, 133, 0.12)',
      rowHoverBg: 'rgba(111, 169, 190, 0.10)',
      rowSelectedBg: 'rgba(22, 134, 174, 0.10)',
      rowSelectedHoverBg: 'rgba(22, 134, 174, 0.16)',
      borderColor: 'rgba(24, 102, 133, 0.12)',
      footerBg: 'rgba(232, 246, 249, 0.60)',
      footerColor: tokens.ocean.text.secondary,
      cellPaddingBlock: 12,
      cellPaddingInline: 16,
      headerSortActiveBg: 'rgba(142, 191, 208, 0.28)',
      headerSortHoverBg: 'rgba(142, 191, 208, 0.24)',
    },
    // 卡片：透明面板材质
    Card: {
      colorBgContainer: tokens.ocean.surface.default,
      headerBg: 'transparent',
      headerFontSize: 16,
      headerHeight: 48,
      paddingLG: 20,
    },
    // 按钮
    Button: {
      primaryShadow: 'none',
      defaultShadow: 'none',
      dangerShadow: 'none',
      defaultBg: 'rgba(240, 250, 251, 0.72)',
      defaultBorderColor: 'rgba(24, 102, 133, 0.24)',
      defaultColor: tokens.ocean.text.primary,
      borderRadius: 4,
      controlHeight: 34,
    },
    // 输入框：半透明亮 surface + 2px 可见焦点
    Input: {
      activeBg: tokens.ocean.surface.strong,
      activeBorderColor: tokens.ocean.action.primary,
      hoverBorderColor: tokens.ocean.border.strong,
      activeShadow: '0 0 0 2px rgba(22, 134, 174, 0.16)',
      errorActiveShadow: '0 0 0 2px rgba(165, 61, 82, 0.16)',
      paddingBlock: 6,
      borderRadius: 4,
    },
    InputNumber: {
      activeBorderColor: tokens.ocean.action.primary,
      hoverBorderColor: tokens.ocean.border.strong,
      activeShadow: '0 0 0 2px rgba(22, 134, 174, 0.16)',
      borderRadius: 4,
    },
    Select: {
      optionSelectedBg: 'rgba(22, 134, 174, 0.12)',
      optionSelectedColor: tokens.ocean.text.primary,
      optionActiveBg: 'rgba(22, 134, 174, 0.08)',
      activeBorderColor: tokens.ocean.action.primary,
      hoverBorderColor: tokens.ocean.border.strong,
      borderRadius: 4,
    },
    // Drawer / Modal：浮层阴影 + strong surface
    Drawer: {
      colorBgElevated: tokens.ocean.surface.strong,
      paddingLG: 24,
    },
    Modal: {
      contentBg: tokens.ocean.surface.strong,
      headerBg: 'transparent',
      titleColor: tokens.ocean.text.primary,
    },
    // Tag：状态语义
    Tag: {
      defaultBg: 'rgba(142, 191, 208, 0.20)',
      defaultColor: tokens.ocean.text.secondary,
      borderRadiusSM: 2,
    },
    // Descriptions
    Descriptions: {
      titleColor: tokens.ocean.text.primary,
      labelColor: tokens.ocean.text.secondary,
      contentColor: tokens.ocean.text.primary,
      labelBg: 'rgba(142, 191, 208, 0.16)',
    },
    // Timeline
    Timeline: {
      tailColor: 'rgba(24, 102, 133, 0.18)',
      dotBg: tokens.ocean.action.primary,
    },
    // Alert
    Alert: {
      borderRadiusLG: 6,
    },
    // Progress
    Progress: {
      defaultColor: tokens.ocean.action.primary,
      remainingColor: 'rgba(24, 102, 133, 0.12)',
    },
    // Tooltip
    Tooltip: {
      colorBgSpotlight: tokens.ocean.surface.strong,
      colorTextLightSolid: tokens.ocean.text.primary,
      borderRadius: 4,
      borderRadiusXS: 2,
    },
    // 滚动条与分割
    Divider: {
      colorSplit: 'rgba(24, 102, 133, 0.14)',
    },
    // 表单
    Form: {
      labelColor: tokens.ocean.text.secondary,
      itemMarginBottom: 18,
    },
    // 分页
    Pagination: {
      itemBg: 'transparent',
      itemActiveBg: 'rgba(22, 134, 174, 0.12)',
      itemActiveColor: tokens.ocean.text.primary,
    },
    // 空状态
    Empty: {
      colorText: tokens.ocean.text.muted,
      colorTextDisabled: tokens.ocean.text.muted,
    },
    // 骨架屏
    Skeleton: {
      color: 'rgba(142, 191, 208, 0.24)',
      gradientFromColor: 'rgba(142, 191, 208, 0.10)',
      gradientToColor: 'rgba(142, 191, 208, 0.30)',
    },
  },
};

export default dataOceanTheme;
