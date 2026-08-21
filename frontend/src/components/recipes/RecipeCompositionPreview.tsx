// ============================================================
// RecipeCompositionPreview — 展示每个 step 的 Recipe 选择与推理链路
// ============================================================

import React from 'react';
import { Card, Tag, Timeline, Typography } from 'antd';
import { BranchesOutlined } from '@ant-design/icons';
import RecipeBadge from './RecipeBadge';
import type { RecipeComposition } from '../../types/generated-api';

const { Text, Paragraph } = Typography;

interface RecipeCompositionPreviewProps {
  compositions: RecipeComposition[];
  workflowSteps: { step_id: string; label: string }[];
}

const LAYER_NAMES: Record<string, string> = {
  base: '基础',
  task: '任务',
  electronic_type: '电子类型',
  modifier: '修饰',
  precision: '精度',
  user_patch: '用户补丁',
};

const RecipeCompositionPreview: React.FC<RecipeCompositionPreviewProps> = ({
  compositions,
  workflowSteps,
}) => {
  if (!compositions.length) {
    return (
      <Card title="Recipe 组合" bordered={false}>
        <Text type="secondary">暂无 Recipe 组合信息</Text>
      </Card>
    );
  }

  return (
    <Card title={<span><BranchesOutlined /> Recipe 组合与来源</span>} bordered={false}>
      {compositions.map((comp) => {
        const step = workflowSteps.find((s) => s.step_id === comp.step_id);
        return (
          <Card
            key={comp.composition_id}
            size="small"
            title={`${step?.label || comp.step_id} — Recipe 组合`}
            type="inner"
            style={{ marginBottom: 12 }}
          >
            <Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 12 }}>
              Composition ID: {comp.composition_id} (rev {comp.revision}) | 
              Pack: {comp.recipe_pack.pack_id} v{comp.recipe_pack.version}
            </Paragraph>

            <Timeline
              items={comp.selected.map((sel, idx) => ({
                color: ['blue', 'green', 'cyan', 'orange', 'purple', 'red'][idx % 6],
                children: (
                  <div>
                    <RecipeBadge
                      recipeId={sel.recipe_ref || sel.recipe_id || ''}
                      version={sel.version}
                      selectionReason={sel.selection_reason}
                    />
                    <Tag color={['blue', 'green', 'cyan', 'orange', 'purple', 'red'][idx % 6]}>
                      {LAYER_NAMES[sel.layer] || sel.layer}
                    </Tag>
                    {sel.matched_context && (
                      <Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>
                        匹配: {JSON.stringify(sel.matched_context)}
                      </Text>
                    )}
                  </div>
                ),
              }))}
            />

            {comp.resolved_parameters && (
              <div style={{ marginTop: 8 }}>
                <Text strong style={{ fontSize: 12 }}>解析参数: </Text>
                {Object.entries(comp.resolved_parameters).map(([k, v]) => (
                  <Tag key={k} style={{ marginTop: 4 }}>
                    {k} = {JSON.stringify(v)}
                  </Tag>
                ))}
              </div>
            )}
          </Card>
        );
      })}
    </Card>
  );
};

export default RecipeCompositionPreview;