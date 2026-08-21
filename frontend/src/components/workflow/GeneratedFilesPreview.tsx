// ============================================================
// GeneratedFilesPreview — 文件树 + 按需预览
// ============================================================

import React, { useState } from 'react';
import { Card, Tree, Modal, Typography, Tag } from 'antd';
import { FileOutlined, FolderOutlined, FileTextOutlined } from '@ant-design/icons';
import type { DataNode } from 'antd/es/tree';
import CodePreview from '../common/CodePreview';
import { formatFileSize } from '../../utils/formatters';
import type { FileTreeNode } from '../../types/generated-api';

const { Text } = Typography;

interface GeneratedFilesPreviewProps {
  fileTree: FileTreeNode | null;
}

function treeNodeToDataNode(node: FileTreeNode): DataNode & { data: FileTreeNode } {
  const isFile = node.type === 'file';
  return {
    key: node.relative_path,
    data: node,
    title: (
      <span>
        {isFile ? <FileOutlined style={{ marginRight: 4 }} /> : <FolderOutlined style={{ marginRight: 4, color: '#faad14' }} />}
        <span>{node.name}</span>
        {isFile && node.size_bytes != null && (
          <Text type="secondary" style={{ fontSize: 11, marginLeft: 8 }}>
            {formatFileSize(node.size_bytes)}
          </Text>
        )}
        {isFile && node.preview_available && (
          <Tag color="blue" style={{ marginLeft: 4, fontSize: 10 }}>可预览</Tag>
        )}
      </span>
    ),
    icon: null as unknown as React.ReactNode,
    children: node.children?.map(treeNodeToDataNode),
    isLeaf: isFile,
  };
}

const GeneratedFilesPreview: React.FC<GeneratedFilesPreviewProps> = ({ fileTree }) => {
  const [previewFileId, setPreviewFileId] = useState<string | null>(null);
  const [previewFileName, setPreviewFileName] = useState<string>('');
  const [previewOpen, setPreviewOpen] = useState(false);

  if (!fileTree) {
    return (
      <Card title="生成文件预览">
        <Text type="secondary">暂无生成文件</Text>
      </Card>
    );
  }

  const treeData = [treeNodeToDataNode(fileTree)];

  const handleSelect = (_keys: React.Key[], info: { node: DataNode }) => {
    const node = info.node as DataNode & { data: FileTreeNode };
    if (node.data?.file_id && node.data?.preview_available) {
      setPreviewFileId(node.data.file_id);
      setPreviewFileName(node.data.name);
      setPreviewOpen(true);
    }
  };

  return (
    <Card
      title={<span><FileTextOutlined /> 生成文件</span>}
      bordered={false}
    >
      <Tree
        showIcon={false}
        defaultExpandAll
        treeData={treeData}
        onSelect={handleSelect as never}
        style={{ marginBottom: 12 }}
      />

      <Modal
        title={`预览: ${previewFileName}`}
        open={previewOpen}
        onCancel={() => setPreviewOpen(false)}
        footer={null}
        width={800}
      >
        <CodePreview fileId={previewFileId} fileName={previewFileName} />
      </Modal>
    </Card>
  );
};

export default GeneratedFilesPreview;