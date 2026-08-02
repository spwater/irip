/**
 * "发布为私有"勾选框 + 提示文案
 *
 * Ant Design Checkbox + 展开提示文案。
 * 勾选后数据仅创建者可见，后续可一键"公开"转为部门可见，但此操作不可逆。
 */
import { useState } from 'react';
import { Checkbox, Alert, Typography } from 'antd';

const { Text } = Typography;

/** PublishPrivateToggle 组件 Props */
export type PublishPrivateToggleProps = {
  /** 是否勾选 */
  checked: boolean;
  /** 选中变化回调 */
  onChange: (checked: boolean) => void;
  /** 是否禁用 */
  disabled?: boolean;
};

/**
 * "发布为私有"勾选框组件
 *
 * 包含一个 Checkbox 和展开的提示文案。
 * 勾选后，此数据仅您本人可见，包括管理员在内的其他任何人都不可见。
 * 后续可一键"公开"转为部门可见，但此操作不可逆。
 */
export function PublishPrivateToggle({
  checked,
  onChange,
  disabled = false,
}: PublishPrivateToggleProps): JSX.Element {
  const [showHint, setShowHint] = useState(false);

  return (
    <div>
      <Checkbox
        checked={checked}
        disabled={disabled}
        onChange={(e) => {
          onChange(e.target.checked);
          setShowHint(e.target.checked);
        }}
        onMouseEnter={() => {
          if (checked) setShowHint(true);
        }}
      >
        <Text strong style={{ color: checked ? '#cf1322' : undefined }}>
          🔒 发布为私有数据
        </Text>
      </Checkbox>

      {showHint && checked && (
        <Alert
          type="warning"
          showIcon
          style={{ marginTop: 8, marginBottom: 0 }}
          message="私有数据可见性说明"
          description="勾选后，此数据仅您本人可见，包括管理员在内的其他任何人都不可见。后续可一键"公开"转为部门可见，但此操作不可逆。"
        />
      )}
    </div>
  );
}
