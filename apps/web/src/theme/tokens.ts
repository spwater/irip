/**
 * Data Ocean「数据之海」设计令牌
 *
 * 语义令牌按第 6 节定义，全部值为字符串常量。
 * 这些令牌同时作为：
 *   1. TypeScript 端的类型化引用（组件、主题配置、图表主题）
 *   2. 与 global.css 中 `--ocean-*` CSS 变量的值保持一一对应
 *
 * 约束（来自设计文档）：
 * - 页面大面积区域不得使用接近 #030812 的近黑蓝。
 * - 暖色只用于状态与风险，不能成为页面装饰主色。
 * - 高饱和青色在单屏可见面积中占比应低于 8%。
 * - 背景渐变不得降低正文对比度；正文区域必须有稳定 surface 承载。
 */

/** 极地雾蓝原始语义令牌 */
export const tokens = {
  /** 画布渐变与空间背景 */
  ocean: {
    canvas: {
      /** 页面空间渐变起点 */
      start: '#A9D2DF',
      /** 主内容背景 */
      middle: '#CFE5EA',
      /** 亮部与留白区 */
      end: '#E8F3F5',
    },
    /** 内容面板材质 */
    surface: {
      /** 常规内容面板 */
      default: 'rgba(240, 250, 251, 0.72)',
      /** 表单、表格、详情主面板 */
      strong: 'rgba(232, 246, 249, 0.90)',
      /** 导航、分区和结构层 */
      structural: 'rgba(142, 191, 208, 0.46)',
      /** 局部中深蓝锚点 */
      focus: '#6FA9BE',
    },
    /** 文字层级 */
    text: {
      /** 标题、正文、核心数据 */
      primary: '#102F44',
      /** 说明、标签和次级信息 */
      secondary: '#486B7E',
      /** 弱提示和辅助索引 */
      muted: '#6F8D9C',
    },
    /** 边框与分隔 */
    border: {
      /** 默认边框与分隔线 */
      subtle: 'rgba(24, 102, 133, 0.16)',
      /** 选中、聚焦和强调边缘 */
      strong: 'rgba(14, 118, 156, 0.34)',
    },
    /** 主操作 */
    action: {
      /** 主要按钮、链接、焦点 */
      primary: '#1686AE',
      /** 主操作悬停 */
      hover: '#0E769C',
      /** 按下、选中、键盘焦点 */
      active: '#075C7D',
    },
    /** 强调 */
    accent: {
      /** 数据轨迹、关键数字、流动强调 */
      current: '#39B9C2',
    },
    /** 状态语义 */
    status: {
      /** 成功、健康、已完成 */
      success: '#14765E',
      /** 待复核、警告、部分成功 */
      warning: '#9A6818',
      /** 失败、错误、危险操作 */
      danger: '#A53D52',
      /** 运行中、处理中、信息状态 */
      info: '#245F9A',
      /** 需要区别的模型或 AI 专属状态 */
      violet: '#6655A4',
    },
  },

  /** 排版 */
  typography: {
    /** 系统字体栈（不下载或嵌入新字体） */
    fontFamily:
      '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif',
    /** UUID、版本、时间、日志与代码等宽字体栈 */
    fontFamilyMono:
      'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace',
    /** 页面主标题字号 */
    pageTitle: '36px',
    /** 二级模块标题字号 */
    sectionTitle: '24px',
    /** 正文 */
    body: '14px',
    /** 高密度表格 */
    dense: '13px',
    /** 最小字号 */
    min: '12px',
    /** 核心数据 */
    hero: '72px',
    /** 英文索引 */
    index: '11px',
    /** 行高 */
    lineHeightBody: '1.7',
    lineHeightTitle: '1.2',
  },

  /** 间距序列（基础单位 4px） */
  spacing: {
    xs: '4px',
    sm: '8px',
    md: '12px',
    base: '16px',
    lg: '24px',
    xl: '32px',
    xxl: '48px',
    xxxl: '64px',
  },

  /** 圆角 */
  radius: {
    /** 小控件与状态 */
    xs: '2px',
    /** 默认控件 */
    sm: '4px',
    /** 面板 */
    md: '6px',
    /** Drawer、Modal 和大型浮层 */
    lg: '8px',
  },

  /** 阴影 */
  shadow: {
    /** 常规面板阴影 */
    panel: '0 12px 30px rgba(45, 104, 125, 0.10)',
    /** 浮层阴影 */
    float: '0 24px 64px rgba(29, 78, 103, 0.18)',
  },

  /** 层级 z-index */
  zIndex: {
    /** 背景光域与网格 */
    background: '0',
    /** 页面装饰层 */
    decoration: '1',
    /** 页面内容 */
    content: '10',
    /** 固定顶栏和局部悬浮工具 */
    header: '100',
  },

  /** 动效令牌 */
  motion: {
    /** 即时反馈（hover、press、focus） */
    instant: '120ms',
    /** 控件状态（选中、展开、标签变化） */
    control: '180ms',
    /** 内容进入（opacity + 最大 6px 上浮） */
    enter: '240ms',
    /** 面板聚焦（边框、背景和最大 8px 位移） */
    focus: '320ms',
    /** 页面层次（仅用于路由主要内容显现） */
    page: '360ms',
    /** 背景呼吸下限 */
    breatheMin: '24s',
    /** 背景呼吸上限 */
    breatheMax: '40s',
    /** 统一缓动 */
    easing: 'cubic-bezier(0.22, 1, 0.36, 1)',
  },
} as const;

/** 状态语义类型 */
export type StatusSemantic =
  | 'neutral'
  | 'info'
  | 'success'
  | 'warning'
  | 'danger'
  | 'special';

/** 状态语义到颜色的映射字典 */
export const STATUS_SEMANTIC_COLOR: Record<StatusSemantic, string> = {
  neutral: tokens.ocean.text.muted,
  info: tokens.ocean.status.info,
  success: tokens.ocean.status.success,
  warning: tokens.ocean.status.warning,
  danger: tokens.ocean.status.danger,
  special: tokens.ocean.status.violet,
};

export default tokens;
