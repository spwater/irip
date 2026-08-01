/**
 * OceanBackdrop — 全局背景「潮线 Tideline」
 *
 * 固定定位背景层，承载数据之海的全部空间签名：
 *   冰蓝渐变画布 → 斜向网格 → 双层潮汐波浪（缓慢漂移）
 *   → 对角数据流光带 → 稀疏数据粒子 → 半调网点 → 大范围光域
 *
 * - z-index: 0，不承载业务数据，不响应鼠标
 * - 运动层 ≤ 3（两条波 + 光带），粒子低频低幅
 * - prefers-reduced-motion 下全部位移动画关闭（见 ocean.css）
 */
import type { CSSProperties, ReactNode } from 'react';

interface OceanBackdropProps {
  /** 可选装饰内容（如背景大字索引），不承载业务数据 */
  children?: ReactNode;
  /** 透传样式 */
  style?: CSSProperties;
}

/**
 * 波浪 path：宽 2400（200% 平铺单位）、高 200，两段相同波形拼接，
 * 位移 -50% 时无缝循环。viewBox 不缩放宽度（preserveAspectRatio="none"）。
 */
/**
 * 波浪 path：宽 2400、高 200，周期 1200（后半 = 前半 +1200），
 * 位移 -50% 时无缝循环；曲线平缓对称，无尖锐折角。
 */
const TIDE_PATH_BACK =
  'M0 100 C 300 70, 600 130, 900 100 C 1050 85, 1150 92, 1200 100 ' +
  'C 1500 70, 1800 130, 2100 100 C 2250 85, 2350 92, 2400 100 L2400 200 L0 200 Z';

const TIDE_PATH_FRONT =
  'M0 122 C 300 94, 600 146, 900 120 C 1050 106, 1150 114, 1200 122 ' +
  'C 1500 94, 1800 146, 2100 120 C 2250 106, 2350 114, 2400 122 L2400 200 L0 200 Z';

/** 前层波峰亮线：仅开放波峰路径，避免闭合轮廓出现竖直边 */
const TIDE_CREST_FRONT =
  'M0 122 C 300 94, 600 146, 900 120 C 1050 106, 1150 114, 1200 122 ' +
  'C 1500 94, 1800 146, 2100 120 C 2250 106, 2350 114, 2400 122';

export function OceanBackdrop({ children, style }: OceanBackdropProps): JSX.Element {
  return (
    <div className="ocean-backdrop" style={style} aria-hidden="true">
      {/* 斜向细网格 */}
      <div className="ocean-backdrop__grid" />

      {/* 大范围柔光 */}
      <div className="ocean-backdrop__glow ocean-backdrop__glow--primary" />
      <div className="ocean-backdrop__glow ocean-backdrop__glow--secondary" />

      {/* 潮汐波浪：后层（浅、慢） */}
      <div className="ocean-backdrop__tide ocean-backdrop__tide--back">
        <svg viewBox="0 0 2400 200" preserveAspectRatio="none">
          <path d={TIDE_PATH_BACK} fill="rgba(14, 91, 132, 0.10)" />
        </svg>
      </div>
      {/* 潮汐波浪：前层（深、快）+ 波峰亮线（仅开放路径） */}
      <div className="ocean-backdrop__tide ocean-backdrop__tide--front">
        <svg viewBox="0 0 2400 200" preserveAspectRatio="none">
          <path d={TIDE_PATH_FRONT} fill="rgba(11, 74, 111, 0.13)" />
          <path d={TIDE_CREST_FRONT} fill="none" stroke="rgba(23, 184, 206, 0.35)" strokeWidth="1.5" />
        </svg>
      </div>

      {/* 对角数据流光带 */}
      <div className="ocean-backdrop__current" />

      {/* 稀疏数据粒子 */}
      <div className="ocean-backdrop__particle ocean-backdrop__particle--1" />
      <div className="ocean-backdrop__particle ocean-backdrop__particle--2" />
      <div className="ocean-backdrop__particle ocean-backdrop__particle--3" />
      <div className="ocean-backdrop__particle ocean-backdrop__particle--4" />
      <div className="ocean-backdrop__particle ocean-backdrop__particle--5" />

      {/* 半调网点 */}
      <div className="ocean-backdrop__halftone ocean-backdrop__halftone--tr" />
      <div className="ocean-backdrop__halftone ocean-backdrop__halftone--bl" />

      {/* 装饰层（背景大字等） */}
      {children ? (
        <div className="ocean-backdrop__decoration">{children}</div>
      ) : null}
    </div>
  );
}

export default OceanBackdrop;
