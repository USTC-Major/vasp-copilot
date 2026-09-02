// ============================================================
// AiContextBar — 上下文占有率显示（聊天/项目列表顶部）
// 用于提示「继续本会话」还是「新开会话」。
// ============================================================

import React from 'react';
import { Progress, Tooltip, Typography } from 'antd';
import type { AiContextSummary } from '../../types/ai';

const { Text } = Typography;

const AiContextBar: React.FC<{ context: AiContextSummary | undefined }> = ({ context }) => {
  const ratio = context?.ratio ?? 0;
  const used = context?.used ?? 0;
  const capacity = context?.capacity ?? 0;
  const pct = Math.round(ratio * 100);
  const overloaded = pct >= 80;

  return (
    <Tooltip title={`已用约 ${used.toLocaleString()} / ${capacity.toLocaleString()} tokens；达到 80% 建议新开会话`}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 180 }}>
        <Text style={{ fontSize: 12, color: overloaded ? '#ff3b30' : '#6e6e73', whiteSpace: 'nowrap' }}>
          上下文 {pct}%
        </Text>
        <Progress
          percent={pct}
          size="small"
          strokeColor={overloaded ? '#ff3b30' : '#0071e3'}
          trailColor="rgba(0,0,0,0.08)"
          style={{ flex: 1, margin: 0 }}
        />
        {overloaded && <Text style={{ fontSize: 12, color: '#ff3b30', whiteSpace: 'nowrap' }}>建议新开会话</Text>}
      </div>
    </Tooltip>
  );
};

export default AiContextBar;
