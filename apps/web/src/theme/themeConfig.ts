/**
 * Data Ocean — Ant Design ThemeConfig
 *
 * 将语义 token 映射到 Ant Design 的 ConfigProvider theme。
 * main.tsx 通过 <ConfigProvider theme={dataOceanTheme}> 应用。
 */
import type { ThemeConfig } from 'antd';
import { oceanTokens } from './tokens';

export const dataOceanTheme: ThemeConfig = {
  token: {
    colorPrimary: oceanTokens.action.primary,
    colorInfo: oceanTokens.status.info,
    colorSuccess: oceanTokens.status.success,
    colorWarning: oceanTokens.status.warning,
    colorError: oceanTokens.status.danger,
    colorText: oceanTokens.text.primary,
    colorTextSecondary: oceanTokens.text.secondary,
    colorBgLayout: 'transparent',
    colorBgContainer: oceanTokens.surface.default,
    colorBorder: 'rgba(24, 102, 133, 0.20)',
    colorSplit: 'rgba(24, 102, 133, 0.14)',
    borderRadius: oceanTokens.radius.control,
    borderRadiusLG: oceanTokens.radius.overlay,
    controlHeight: 34,
    fontSize: 14,
  },
  components: {
    Layout: { bodyBg: 'transparent', headerBg: 'transparent', siderBg: 'transparent' },
    Card: { colorBgContainer: oceanTokens.surface.default },
    Table: {
      headerBg: 'rgba(169, 210, 223, 0.36)',
      rowHoverBg: 'rgba(57, 185, 194, 0.08)',
      borderColor: oceanTokens.border.subtle,
    },
    Menu: {
      itemBg: 'transparent',
      itemSelectedBg: 'rgba(22, 134, 174, 0.12)',
      itemSelectedColor: oceanTokens.action.active,
    },
    Tabs: {
      itemSelectedColor: oceanTokens.action.active,
      inkBarColor: oceanTokens.accent.current,
    },
    Drawer: { colorBgElevated: 'rgba(232, 246, 249, 0.98)' },
    Modal: { contentBg: 'rgba(232, 246, 249, 0.98)', headerBg: 'transparent' },
  },
};
