// ============================================================
// RecipeCompositionPreview — 按类别展示 Recipe，保留原始组合与覆盖详情
// ============================================================

import React from 'react';
import { Alert, Card, Collapse, Tag, Typography } from 'antd';
import { BranchesOutlined } from '@ant-design/icons';
import RecipeBadge from './RecipeBadge';
import type { RecipeComposition, SelectedRecipe } from '../../types/generated-api';

const { Text, Title } = Typography;

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

const INITIAL_RECOMMENDATION_CODE = 'INITIAL_RECOMMENDATION_ONLY';
const INITIAL_RECOMMENDATION_MESSAGE = '这是初始推荐，不是唯一正确设置。';

const CATEGORIES = [
  { key: 'base_task', label: '基础与任务', color: 'default' },
  { key: 'precision', label: '精度', color: 'blue' },
  { key: 'electronic_type', label: '电子类型', color: 'green' },
  { key: 'dftu', label: 'DFT+U', color: 'purple' },
  { key: 'modifier', label: '其他修饰', color: 'orange' },
  { key: 'user_patch', label: '用户覆盖', color: 'cyan' },
  { key: 'unknown', label: '其他/未识别类别', color: 'default' },
] as const;

const referenceIdentity = (reference: string) => {
  const separator = reference.lastIndexOf('@');
  return separator > 0 && separator < reference.length - 1 ? reference.slice(0, separator) : reference;
};

const recipeIdentity = (selected: SelectedRecipe) => (
  selected.recipe_id?.trim() ? selected.recipe_id : referenceIdentity(selected.recipe_ref ?? '')
);

const categoryFor = (selected: SelectedRecipe): typeof CATEGORIES[number]['key'] => {
  if (selected.layer === 'base' || selected.layer === 'task') return 'base_task';
  if (selected.layer === 'precision' || selected.layer === 'electronic_type' || selected.layer === 'user_patch') return selected.layer;
  if (selected.layer === 'modifier') return recipeIdentity(selected) === 'modifier.dftu' ? 'dftu' : 'modifier';
  return 'unknown';
};

const hasIdentityConflict = (selected: SelectedRecipe) => (
  !!selected.recipe_id?.trim() && !!selected.recipe_ref?.trim()
  && selected.recipe_id !== referenceIdentity(selected.recipe_ref)
);

const jsonValue = (value: unknown) => value === undefined ? '未提供' : JSON.stringify(value, null, 2);
const confirmationText = (value: boolean | undefined) => value === true ? '已确认' : value === false ? '未确认' : '未提供确认状态';
const hasWarningCode = (value: unknown, code: string) => (
  typeof value === 'object' && value !== null && 'code' in value && value.code === code
);

const NoticeList: React.FC<{ values: unknown[] }> = ({ values }) => (
  <ul style={{ margin: 0, paddingInlineStart: 20 }}>
    {values.map((value, index) => (
      <li key={index}>
        {typeof value === 'object' && value !== null && 'message' in value && typeof value.message === 'string'
          ? value.message : typeof value === 'string' ? value : JSON.stringify(value)}
      </li>
    ))}
  </ul>
);

const JsonValue: React.FC<{ value: unknown }> = ({ value }) => (
  <pre style={{ margin: '4px 0 12px', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', fontSize: 12 }}>{jsonValue(value)}</pre>
);

const DetailField: React.FC<{ label: string; children: React.ReactNode }> = ({ label, children }) => (
  <div style={{ marginBottom: 6 }}><Text strong>{label}：</Text> {children}</div>
);

const CompositionDetails: React.FC<{ composition: RecipeComposition }> = ({ composition: comp }) => (
  <div style={{ minWidth: 0, overflowWrap: 'anywhere' }}>
    <DetailField label="Composition ID">{comp.composition_id}</DetailField>
    <DetailField label="Revision">{comp.revision}</DetailField>
    <DetailField label="Pack">{comp.recipe_pack.pack_id}</DetailField>
    <DetailField label="Pack 版本">{comp.recipe_pack.version}</DetailField>
    <Title level={5}>原始组合顺序</Title>
    <ol style={{ paddingInlineStart: 24 }}>
      {comp.selected.map((selected, index) => (
        <li key={index} style={{ marginBottom: 16 }}>
          <RecipeBadge recipeId={(selected.recipe_id?.trim() ? selected.recipe_id : selected.recipe_ref) || '未提供配方身份'} version={selected.version} selectionReason={selected.selection_reason} />
          <DetailField label="Layer">{selected.layer}</DetailField>
          <DetailField label="Order">{selected.order}</DetailField>
          <DetailField label="recipe_id">{selected.recipe_id ?? '未提供'}</DetailField>
          <DetailField label="recipe_ref">{selected.recipe_ref ?? '未提供'}</DetailField>
          <DetailField label="版本">{selected.version}</DetailField>
          <DetailField label="选择原因">{selected.selection_reason || '未提供'}</DetailField>
          <Text strong>匹配上下文</Text>
          <JsonValue value={selected.matched_context} />
        </li>
      ))}
    </ol>
    {!comp.selected.length && <Text type="secondary">未提供已选 Recipe 条目</Text>}
    <Title level={5}>最终参数（resolved_parameters）</Title>
    <JsonValue value={comp.resolved_parameters} />
    <Title level={5}>参数来源与覆盖</Title>
    {comp.provenance?.length ? comp.provenance.map((source, index) => (
      <section key={index} aria-label={`参数来源 ${index + 1}：${source.parameter}`} style={{ marginBottom: 16 }}>
        <DetailField label="参数">{source.parameter}</DetailField>
        <DetailField label="值">{jsonValue(source.value)}</DetailField>
        <DetailField label="来源类型">{source.source_type}</DetailField>
        <DetailField label="来源 ID">{source.source_id}</DetailField>
        <DetailField label="来源修订">{source.source_revision ?? '未提供'}</DetailField>
        <DetailField label="派生函数">{source.derived_by ?? '未提供'}</DetailField>
        <DetailField label="需要确认">{source.requires_confirmation === true ? '是' : source.requires_confirmation === false ? '否' : '未提供'}</DetailField>
        <DetailField label="确认状态">{confirmationText(source.confirmed)}</DetailField>
        <Text strong>覆盖记录（overrode）</Text>
        <JsonValue value={source.overrode} />
      </section>
    )) : <Text type="secondary">未提供来源明细</Text>}
    <Title level={5}>用户补丁（patches）</Title>
    {comp.patches?.length ? comp.patches.map((patch, index) => (
      <section key={index} aria-label={`用户补丁 ${index + 1}：${patch.parameter}`} style={{ marginBottom: 16 }}>
        <DetailField label="参数">{patch.parameter}</DetailField>
        <DetailField label="操作">{patch.operation}</DetailField>
        <DetailField label="值">{jsonValue(patch.value)}</DetailField>
        <DetailField label="原因">{patch.reason || '未提供'}</DetailField>
        <DetailField label="用户确认">{confirmationText(patch.confirmed_by_user)}</DetailField>
        <Text strong>补丁原始数据</Text>
        <JsonValue value={patch} />
      </section>
    )) : <Text type="secondary">未提供补丁明细</Text>}
    <Title level={5}>确认记录（confirmations）</Title>
    <JsonValue value={comp.confirmations} />
    {!!comp.conflicts?.length && <><Title level={5}>冲突原始数据</Title><JsonValue value={comp.conflicts} /></>}
    {!!comp.warnings?.length && <><Title level={5}>警告原始数据</Title><JsonValue value={comp.warnings} /></>}
    <details>
      <summary>原始组合数据（含补充字段）</summary>
      <JsonValue value={comp} />
    </details>
  </div>
);

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

  const hasInitialRecommendationWarning = compositions.some((comp) => (
    comp.warnings?.some((warning) => hasWarningCode(warning, INITIAL_RECOMMENDATION_CODE))
  ));

  return (
    <Card title={<span><BranchesOutlined /> Recipe 组合与来源</span>} bordered={false}>
      {hasInitialRecommendationWarning && (
        <Alert
          type="info"
          showIcon
          title="Recipe 推荐说明"
          description={INITIAL_RECOMMENDATION_MESSAGE}
          style={{ marginBottom: 12 }}
        />
      )}
      {compositions.map((comp) => {
        const step = workflowSteps.find((s) => s.step_id === comp.step_id);
        const identityConflicts = comp.selected.filter(hasIdentityConflict);
        const visibleWarnings = (comp.warnings ?? []).filter((warning) => (
          !hasWarningCode(warning, INITIAL_RECOMMENDATION_CODE)
        ));
        const patchWarnings = comp.patches?.flatMap((patch) => patch.validation?.warnings ?? []) ?? [];
        return (
          <Card
            key={comp.composition_id}
            size="small"
            title={<span style={{ whiteSpace: 'normal' }}>{step?.label || comp.step_id} — Recipe 组合</span>}
            type="inner"
            style={{ marginBottom: 12, minWidth: 0, overflowWrap: 'anywhere' }}
          >
            {!!identityConflicts.length && (
              <Alert type="warning" showIcon title="配方身份不一致，请核对 recipe_id 与 recipe_ref" description={<NoticeList values={identityConflicts.map(({ recipe_id, recipe_ref }) => ({ recipe_id, recipe_ref }))} />} style={{ marginBottom: 12 }} />
            )}
            {!!comp.conflicts?.length && <Alert type="error" showIcon title={`配方冲突（${comp.conflicts.length}）`} description={<NoticeList values={comp.conflicts} />} style={{ marginBottom: 12 }} />}
            {!!visibleWarnings.length && <Alert type="warning" showIcon title={`配方警告（${visibleWarnings.length}）`} description={<NoticeList values={visibleWarnings} />} style={{ marginBottom: 12 }} />}
            {!!patchWarnings.length && <Alert type="warning" showIcon title={`补丁验证警告（${patchWarnings.length}）`} description={<NoticeList values={patchWarnings} />} style={{ marginBottom: 12 }} />}
            {comp.composition_status && <DetailField label="组合状态">{comp.composition_status}</DetailField>}
            {CATEGORIES.map((category) => {
              const selected = comp.selected.filter((recipe) => categoryFor(recipe) === category.key);
              const patches = category.key === 'user_patch' ? comp.patches ?? [] : [];
              if (!selected.length && !patches.length) return null;
              return (
                <section key={category.key} aria-label={category.label} style={{ marginBottom: 12 }}>
                  <Title level={5} style={{ margin: '0 0 6px' }}>{category.label}</Title>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                    {selected.map((recipe, index) => (
                      <Tag key={`recipe-${index}`} color={category.color} style={{ margin: 0, maxWidth: '100%', whiteSpace: 'normal', overflowWrap: 'anywhere' }}>
                        {LAYER_NAMES[recipe.layer] || recipe.layer} · {recipeIdentity(recipe) || '未提供配方身份'} · v{recipe.version}
                      </Tag>
                    ))}
                    {patches.map((patch, index) => (
                      <Tag key={`patch-${index}`} color={category.color} style={{ margin: 0, maxWidth: '100%', whiteSpace: 'normal', overflowWrap: 'anywhere' }}>
                        补丁 · {patch.parameter} · {patch.operation}
                      </Tag>
                    ))}
                  </div>
                </section>
              );
            })}
            {!comp.selected.length && !comp.patches?.length && <Text type="secondary">未提供已选 Recipe 或用户补丁</Text>}
            <Collapse style={{ marginTop: 12, minWidth: 0 }} items={[{
              key: 'details',
              label: '组合与覆盖详情',
              children: <CompositionDetails composition={comp} />,
            }]} />
          </Card>
        );
      })}
    </Card>
  );
};

export default RecipeCompositionPreview;
