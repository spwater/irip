/**
 * 摘要预览 Modal 组件。
 *
 * 用 react-markdown 渲染后端生成的 Markdown 分析摘要，
 * 支持复制到剪贴板和下载 .md 文件。
 */
import { Modal, Button, Space, Typography, message } from 'antd';
import { CopyOutlined, DownloadOutlined } from '@ant-design/icons';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

const { Text } = Typography;

export function SummaryModal({
  open,
  markdown,
  title,
  onClose,
}: {
  /** 是否显示 */
  open: boolean;
  /** Markdown 摘要内容 */
  markdown: string;
  /** 对话标题（用于下载文件名） */
  title: string;
  /** 关闭回调 */
  onClose: () => void;
}): JSX.Element {
  /** 复制到剪贴板 */
  const handleCopy = (): void => {
    navigator.clipboard
      .writeText(markdown)
      .then(() => {
        message.success('已复制到剪贴板');
      })
      .catch(() => {
        message.error('复制失败');
      });
  };

  /** 下载 .md 文件 */
  const handleDownload = (): void => {
    const blob = new Blob([markdown], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${title || '分析摘要'}-摘要.md`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    message.success('已下载摘要文件');
  };

  return (
    <Modal
      title="分析摘要预览"
      open={open}
      onCancel={onClose}
      width={720}
      footer={
        <Space>
          <Button onClick={onClose}>关闭</Button>
          <Button icon={<CopyOutlined />} onClick={handleCopy}>
            复制
          </Button>
          <Button
            type="primary"
            icon={<DownloadOutlined />}
            onClick={handleDownload}
          >
            下载 .md
          </Button>
        </Space>
      }
    >
      <div
        style={{
          maxHeight: 'calc(100vh - 320px)',
          overflowY: 'auto',
          padding: '8px 4px',
        }}
        className="ai-markdown-body"
      >
        {markdown ? (
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{markdown}</ReactMarkdown>
        ) : (
          <Text type="secondary">暂无摘要内容</Text>
        )}
      </div>
    </Modal>
  );
}

export default SummaryModal;
