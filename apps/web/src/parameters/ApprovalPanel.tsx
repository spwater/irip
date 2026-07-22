import {
  Button,
  Descriptions,
  Divider,
  Space,
  Tag,
  Typography,
  message,
} from 'antd';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import {
  apiApproveCandidate,
  apiRejectCandidate,
  extractApiError,
  type CurrentUser,
  type ParameterCandidate,
} from '@/api/client';

const { Text } = Typography;

/** 状态 → 颜色 */
const STATUS_COLOR: Record<string, string> = {
  draft: 'blue',
  in_review: 'orange',
  published: 'green',
  deprecated: 'default',
  rejected: 'red',
};

/** 状态 → 中文标签 */
const STATUS_LABEL: Record<string, string> = {
  draft: '草稿',
  in_review: '审核中',
  published: '已发布',
  deprecated: '已弃用',
  rejected: '已驳回',
};

/** 质量等级 → 颜色 */
const QUALITY_COLOR: Record<string, string> = {
  Q0: 'default',
  Q1: 'blue',
  Q2: 'gold',
  Q3: 'green',
};

/**
 * 候选参数审批面板
 *
 * 功能：
 * - 展示候选参数详情（值、条件、置信区间、证据数、质量等级、状态、版本）
 * - 显示「查看完整来源」链接，跳转到溯源图谱
 * - 提交者不能审批自己提交的候选（self_approval_forbidden）
 *   当 currentUser.id === candidate.submitted_by 时，隐藏「批准发布」和「驳回」按钮
 */
export function ApprovalPanel({
  candidate,
  currentUser,
  parameterId,
}: {
  candidate: ParameterCandidate;
  currentUser: CurrentUser;
  parameterId: string;
}): JSX.Element {
  const queryClient = useQueryClient();

  /** 当前用户是否为提交者 */
  const isSubmitter = currentUser.id === candidate.submitted_by;

  // ---- 批准 Mutation ----
  const approveMutation = useMutation({
    mutationFn: () => apiApproveCandidate(candidate.id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['candidates', parameterId] });
      void queryClient.invalidateQueries({ queryKey: ['parameters'] });
      message.success('批准成功');
    },
    onError: (err: unknown) => {
      message.error(extractApiError(err));
    },
  });

  // ---- 驳回 Mutation ----
  const rejectMutation = useMutation({
    mutationFn: () => apiRejectCandidate(candidate.id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['candidates', parameterId] });
      message.success('已驳回');
    },
    onError: (err: unknown) => {
      message.error(extractApiError(err));
    },
  });

  return (
    <div>
      <Descriptions bordered column={2} size="small">
        <Descriptions.Item label="版本标签">
          {candidate.version_label}
        </Descriptions.Item>
        <Descriptions.Item label="值">
          {candidate.value} {candidate.unit}
        </Descriptions.Item>
        <Descriptions.Item label="条件" span={2}>
          {candidate.conditions ? JSON.stringify(candidate.conditions) : '-'}
        </Descriptions.Item>
        <Descriptions.Item label="置信区间">
          {candidate.confidence_interval
            ? `[${candidate.confidence_interval.lower}, ${candidate.confidence_interval.upper}]`
            : '-'}
        </Descriptions.Item>
        <Descriptions.Item label="证据数">
          {candidate.evidence_count}
        </Descriptions.Item>
        <Descriptions.Item label="质量等级">
          <Tag color={QUALITY_COLOR[candidate.quality_level] ?? 'default'}>
            {candidate.quality_level}
          </Tag>
        </Descriptions.Item>
        <Descriptions.Item label="状态">
          <Tag color={STATUS_COLOR[candidate.status] ?? 'default'}>
            {STATUS_LABEL[candidate.status] ?? candidate.status}
          </Tag>
        </Descriptions.Item>
        <Descriptions.Item label="提交者">
          {candidate.submitted_by}
        </Descriptions.Item>
        <Descriptions.Item label="版本">
          {candidate.version_label}
        </Descriptions.Item>
      </Descriptions>

      <Divider style={{ margin: '12px 0' }} />

      <Space direction="vertical" style={{ width: '100%' }}>
        <Button
          type="link"
          href={
            candidate.derivation_run_id
              ? `/provenance?run_id=${candidate.derivation_run_id}`
              : '/provenance'
          }
          style={{ padding: 0 }}
        >
          查看完整来源
        </Button>

        {!isSubmitter && (
          <Space>
            <Button
              type="primary"
              loading={approveMutation.isPending}
              onClick={() => approveMutation.mutate()}
            >
              批准发布
            </Button>
            <Button
              danger
              loading={rejectMutation.isPending}
              onClick={() => rejectMutation.mutate()}
            >
              驳回
            </Button>
          </Space>
        )}

        {isSubmitter && (
          <Text type="secondary" style={{ fontSize: 12 }}>
            提交者不可审批自己提交的候选参数
          </Text>
        )}
      </Space>
    </div>
  );
}
