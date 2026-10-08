import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ConfigProvider } from 'antd';
import RecipeCompositionPreview from './RecipeCompositionPreview';
import type { ParameterPatch, ParameterProvenance, RecipeComposition, SelectedRecipe } from '../../types/generated-api';

const recipe = (layer: SelectedRecipe['layer'], recipeId: string, order: number): SelectedRecipe => ({
  layer, recipe_id: recipeId, version: '1.0.0', order,
  selection_reason: `选取 ${recipeId}`, matched_context: { task: 'static' },
});

const patch: ParameterPatch = {
  patch_id: 'patch-encut', composition_id: 'composition-static', expected_revision: 3,
  parameter: 'ENCUT', operation: 'replace', value: 600, source: 'user',
  reason: '用户提供的精度要求', confirmed_by_user: false,
  validation: { allowed: true, rule_ids: ['encut.range'], warnings: [] },
};

const provenance: ParameterProvenance = {
  parameter: 'ENCUT', value: 600, source_type: 'user_patch', source_id: 'patch-encut', source_revision: '3',
  overrode: { source_type: 'recipe', source_id: 'precision.standard', value: 520 },
  derived_by: null, requires_confirmation: true, confirmed: false,
};

const composition = (changes: Partial<RecipeComposition> = {}): RecipeComposition => ({
  composition_id: 'composition-static', revision: 3, step_id: 'static',
  recipe_pack: { pack_id: 'vasp-mvp-core', version: '1.0.0', sha256: 'pack-hash' },
  selected: [
    recipe('modifier', 'modifier.magnetic', 40),
    recipe('electronic_type', 'electronic.unknown', 30),
    recipe('base', 'base.vasp', 10),
    recipe('precision', 'precision.standard', 25),
    recipe('task', 'task.static.standard', 20),
    recipe('modifier', 'modifier.dftu', 41),
    recipe('user_patch', 'user.parameters', 50),
  ],
  resolved_parameters: { ENCUT: 600, LDAU: true, LDAUU: [4, 0], nullable_value: null },
  provenance: [provenance], patches: [patch],
  confirmations: [{ key: 'dftu.Fe.u_ev', prompt: '确认 Fe 的 U 值', confirmation_status: 'pending' }],
  composition_sha256: 'composition-hash',
  ...changes,
});

const steps = [{ step_id: 'static', label: '静态计算' }];
const initialRecommendationWarning = {
  code: 'INITIAL_RECOMMENDATION_ONLY',
  message: '这是初始推荐，不是唯一正确设置。',
};
const renderPreview = (compositions: RecipeComposition[]) => render(
  <ConfigProvider theme={{ token: { motion: false } }}>
    <RecipeCompositionPreview compositions={compositions} workflowSteps={steps} />
  </ConfigProvider>,
);

function deepFreeze<T>(value: T): T {
  if (value && typeof value === 'object') {
    Object.values(value).forEach(deepFreeze);
    Object.freeze(value);
  }
  return value;
}

describe('RecipeCompositionPreview', () => {
  it('按固定顺序显示分类，基础和任务身份仍可辨认，移除 Timeline', () => {
    const { container } = renderPreview([composition()]);
    expect(screen.getAllByRole('heading', { level: 5 }).map((heading) => heading.textContent)).toEqual([
      '基础与任务', '精度', '电子类型', 'DFT+U', '其他修饰', '用户覆盖',
    ]);
    const baseGroup = screen.getByRole('region', { name: '基础与任务' });
    expect(baseGroup).toHaveTextContent('基础 · base.vasp');
    expect(baseGroup).toHaveTextContent('任务 · task.static.standard');
    expect(screen.getByRole('region', { name: 'DFT+U' })).toHaveTextContent('modifier.dftu');
    expect(screen.getByRole('region', { name: '其他修饰' })).toHaveTextContent('modifier.magnetic');
    const userGroup = screen.getByRole('region', { name: '用户覆盖' });
    expect(userGroup).toHaveTextContent('user.parameters');
    expect(userGroup).toHaveTextContent('补丁 · ENCUT · replace');
    expect(container.querySelector('.ant-timeline')).toBeNull();
    expect(screen.queryByText('原始组合顺序')).not.toBeInTheDocument();
  });

  it('条目重排或新增后同类别颜色不变，并保留组内原数组次序', () => {
    const original = composition();
    const { rerender } = renderPreview([original]);
    const colors = [
      ['基础与任务', 'default'], ['精度', 'blue'], ['电子类型', 'green'],
      ['DFT+U', 'purple'], ['其他修饰', 'orange'], ['用户覆盖', 'cyan'],
    ];
    const assertColors = () => {
      for (const [category, color] of colors) {
        const tags = screen.getByRole('region', { name: category }).querySelectorAll('.ant-tag');
        expect(tags.length).toBeGreaterThan(0);
        for (const tag of tags) expect(tag).toHaveClass(`ant-tag-${color}`);
      }
    };
    assertColors();
    const reordered = composition({ selected: [recipe('modifier', 'modifier.extra', 5), ...[...original.selected].reverse()] });
    rerender(<ConfigProvider theme={{ token: { motion: false } }}><RecipeCompositionPreview compositions={[reordered]} workflowSteps={steps} /></ConfigProvider>);
    assertColors();
    expect(Array.from(screen.getByRole('region', { name: '其他修饰' }).querySelectorAll('.ant-tag')).map((tag) => tag.textContent)).toEqual([
      '修饰 · modifier.extra · v1.0.0', '修饰 · modifier.magnetic · v1.0.0',
    ]);
  });

  it('展开后保留原始数组顺序和 order，不改输入、最终参数或覆盖来源', async () => {
    const comp = deepFreeze(composition());
    const before = JSON.stringify(comp);
    renderPreview([comp]);
    await userEvent.setup().click(screen.getByRole('button', { name: /组合与覆盖详情/ }));
    const orderedList = screen.getByRole('list');
    const entries = within(orderedList).getAllByRole('listitem');
    expect(entries).toHaveLength(comp.selected.length);
    comp.selected.forEach((selected, index) => {
      expect(entries[index]).toHaveTextContent(selected.recipe_id!);
      expect(entries[index]).toHaveTextContent(`Order： ${selected.order}`);
      expect(entries[index]).toHaveTextContent(selected.selection_reason);
      expect(entries[index]).toHaveTextContent('"task": "static"');
    });
    expect(screen.getByText('composition-static')).toBeVisible();
    expect(screen.getByText('vasp-mvp-core')).toBeVisible();
    const finalParameters = screen.getByRole('heading', { name: '最终参数（resolved_parameters）' }).nextElementSibling;
    expect(finalParameters).toHaveTextContent('"LDAUU": [ 4, 0 ]');
    expect(finalParameters).toHaveTextContent('"nullable_value": null');
    const source = screen.getByRole('region', { name: '参数来源 1：ENCUT' });
    expect(source).toHaveTextContent('来源类型： user_patch');
    expect(source).toHaveTextContent('来源 ID： patch-encut');
    expect(source).toHaveTextContent('来源修订： 3');
    expect(source).toHaveTextContent('确认状态： 未确认');
    expect(source).toHaveTextContent('precision.standard');
    expect(source).toHaveTextContent('520');
    const patchDetails = screen.getByRole('region', { name: '用户补丁 1：ENCUT' });
    expect(patchDetails).toHaveTextContent('操作： replace');
    expect(patchDetails).toHaveTextContent('值： 600');
    expect(patchDetails).toHaveTextContent('原因： 用户提供的精度要求');
    expect(patchDetails).toHaveTextContent('用户确认： 未确认');
    const confirmations = screen.getByRole('heading', { name: '确认记录（confirmations）' }).nextElementSibling;
    expect(confirmations).toHaveTextContent('"confirmation_status": "pending"');
    expect(JSON.stringify(comp)).toBe(before);
  });

  it('按精确身份识别 DFT+U，空 ID 回退 ref，仅去掉最后的版本段', async () => {
    const comp = composition({
      selected: [
        { ...recipe('modifier', '', 1), recipe_ref: 'modifier.dftu@1.0.0' },
        { ...recipe('modifier', ' ', 2), recipe_ref: 'modifier.dftu@2.0.0' },
        { ...recipe('modifier', 'modifier.unrelated', 3), selection_reason: 'DFT+U 功能说明' },
        { ...recipe('modifier', '', 4), recipe_ref: 'modifier.dftu@variant@1.0.0' },
      ], patches: [],
    });
    renderPreview([comp]);
    expect(screen.getByRole('region', { name: 'DFT+U' }).querySelectorAll('.ant-tag')).toHaveLength(2);
    const other = screen.getByRole('region', { name: '其他修饰' });
    expect(other).toHaveTextContent('modifier.unrelated');
    expect(other).toHaveTextContent('modifier.dftu@variant');
    await userEvent.setup().click(screen.getByRole('button', { name: /组合与覆盖详情/ }));
    expect(screen.getAllByText('modifier.dftu@1.0.0').length).toBeGreaterThan(0);
    expect(screen.getAllByText('modifier.dftu@variant@1.0.0').length).toBeGreaterThan(0);
  });

  it('ID 和 ref 冲突在折叠状态下可见，采用非空 ID 并保留双方数据', () => {
    renderPreview([composition({ selected: [{ ...recipe('modifier', 'modifier.magnetic', 1), recipe_ref: 'modifier.dftu@1.0.0' }], patches: [] })]);
    expect(screen.getByRole('alert')).toHaveTextContent('配方身份不一致');
    expect(screen.getByRole('alert')).toHaveTextContent('modifier.magnetic');
    expect(screen.getByRole('alert')).toHaveTextContent('modifier.dftu@1.0.0');
    expect(screen.queryByRole('region', { name: 'DFT+U' })).not.toBeInTheDocument();
    expect(screen.getByRole('region', { name: '其他修饰' })).toHaveTextContent('modifier.magnetic');
  });

  it('未知 layer 保留原始类别和身份，缺少可选字段不编造来源', async () => {
    const comp = composition({
      selected: [recipe('future_layer' as SelectedRecipe['layer'], 'future.recipe', 9)],
      resolved_parameters: undefined, provenance: undefined, patches: undefined, confirmations: undefined,
    });
    renderPreview([comp]);
    const unknown = screen.getByRole('region', { name: '其他/未识别类别' });
    expect(unknown).toHaveTextContent('future_layer · future.recipe');
    expect(unknown.querySelector('.ant-tag')).toHaveClass('ant-tag-default');
    expect(screen.queryByRole('region', { name: '用户覆盖' })).not.toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole('button', { name: /组合与覆盖详情/ }));
    expect(screen.getByText('未提供来源明细')).toBeVisible();
    expect(screen.getByText('未提供补丁明细')).toBeVisible();
    expect(screen.queryByText('无覆盖')).not.toBeInTheDocument();
  });

  it('仅有实际 patch 时仍显示用户覆盖，不制造 Recipe 条目或两次执行结论', async () => {
    renderPreview([composition({ selected: [], patches: [patch] })]);
    const group = screen.getByRole('region', { name: '用户覆盖' });
    expect(group.querySelectorAll('.ant-tag')).toHaveLength(1);
    expect(group).toHaveTextContent('补丁 · ENCUT · replace');
    expect(group).not.toHaveTextContent('patch-encut');
    expect(screen.queryByRole('region', { name: '基础与任务' })).not.toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole('button', { name: /组合与覆盖详情/ }));
    expect(screen.getByText('未提供已选 Recipe 条目')).toBeVisible();
    expect(screen.getByRole('region', { name: '用户补丁 1：ENCUT' })).toHaveTextContent('patch-encut');
  });

  it('冲突、警告和补丁验证警告在折叠时可见，详情保留原始记录', async () => {
    renderPreview([composition({
      conflicts: [{ parameter: 'ENCUT', values: { 'precision.high': 600, 'precision.standard': 520 } }],
      warnings: [{ code: 'DFTU_USER_VALUE_REQUIRED', message: 'U/J/L 来自用户输入' }],
      patches: [{ ...patch, validation: { ...patch.validation, warnings: ['补丁警告样例'] } }],
    })]);
    const alerts = screen.getAllByRole('alert');
    expect(alerts).toHaveLength(3);
    expect(alerts[0]).toHaveTextContent('配方冲突（1）');
    expect(alerts[1]).toHaveTextContent('U/J/L 来自用户输入');
    expect(alerts[2]).toHaveTextContent('补丁警告样例');
    await userEvent.setup().click(screen.getByRole('button', { name: /组合与覆盖详情/ }));
    expect(screen.getByText('冲突原始数据')).toBeVisible();
    expect(screen.getByText('警告原始数据')).toBeVisible();
    expect(screen.getByRole('heading', { name: '警告原始数据' }).nextElementSibling).toHaveTextContent('DFTU_USER_VALUE_REQUIRED');
    expect(screen.getAllByRole('alert')).toHaveLength(3);
  });

  it('多个步骤的初始推荐说明只显示一次，计数仅包含其他警告且不丢冲突或补丁警告', async () => {
    renderPreview([
      composition({
        warnings: [initialRecommendationWarning, { code: 'DFTU_USER_VALUE_REQUIRED', message: 'U/J/L 来自用户输入' }],
        conflicts: [{ parameter: 'ENCUT', values: { 'precision.high': 600, 'precision.standard': 520 } }],
        patches: [{ ...patch, validation: { ...patch.validation, warnings: ['补丁警告样例'] } }],
      }),
      composition({
        composition_id: 'composition-dos', step_id: 'dos',
        warnings: [initialRecommendationWarning],
      }),
    ]);

    expect(screen.getAllByText('这是初始推荐，不是唯一正确设置。')).toHaveLength(1);
    expect(screen.getByText('配方警告（1）')).toBeInTheDocument();
    expect(screen.getByText('U/J/L 来自用户输入')).toBeInTheDocument();
    expect(screen.getByText('配方冲突（1）')).toBeInTheDocument();
    expect(screen.getByText('补丁验证警告（1）')).toBeInTheDocument();
    expect(screen.getAllByRole('alert')).toHaveLength(4);

    await userEvent.setup().click(screen.getAllByRole('button', { name: /组合与覆盖详情/ })[0]);
    expect(screen.getByRole('heading', { name: '警告原始数据' }).nextElementSibling).toHaveTextContent('INITIAL_RECOMMENDATION_ONLY');
  });

  it('只剩初始推荐说明时不显示空的步骤警告框，且没有该警告码时不显示说明', () => {
    const { rerender } = renderPreview([composition({ warnings: [initialRecommendationWarning] })]);
    expect(screen.getAllByRole('alert')).toHaveLength(1);
    expect(screen.queryByText(/配方警告/)).not.toBeInTheDocument();

    rerender(<ConfigProvider theme={{ token: { motion: false } }}><RecipeCompositionPreview compositions={[composition({ warnings: [] })]} workflowSteps={steps} /></ConfigProvider>);
    expect(screen.queryByText('这是初始推荐，不是唯一正确设置。')).not.toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('来源修订为 null、确认和数组值如实展示，补充字段保留在原始数据中', async () => {
    const nullableSource: unknown = { ...provenance, source_revision: null, derived_by: 'generate_ldau_arrays', value: [4, 0], confirmed: true, extra_note: '额外来源证据' };
    renderPreview([composition({ provenance: [nullableSource as ParameterProvenance], patches: [{ ...patch, confirmed_by_user: true }] })]);
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /组合与覆盖详情/ }));
    const source = screen.getByRole('region', { name: '参数来源 1：ENCUT' });
    expect(source).toHaveTextContent('来源修订： 未提供');
    expect(source).toHaveTextContent('派生函数： generate_ldau_arrays');
    expect(source).toHaveTextContent('确认状态： 已确认');
    expect(source).toHaveTextContent('4');
    expect(screen.getByRole('region', { name: '用户补丁 1：ENCUT' })).toHaveTextContent('用户确认： 已确认');
    await user.click(screen.getByText('原始组合数据（含补充字段）'));
    expect(screen.getByText(/"extra_note": "额外来源证据"/)).toBeVisible();
  });

  it('保留空状态，每个 step 独立折叠，未提供步骤名称时回退 step_id', async () => {
    const { rerender } = renderPreview([]);
    expect(screen.getByText('暂无 Recipe 组合信息')).toBeInTheDocument();
    const empty = composition({ selected: [], patches: [], provenance: [] });
    const second = composition({ step_id: 'dos', composition_id: 'composition-dos' });
    rerender(<ConfigProvider theme={{ token: { motion: false } }}><RecipeCompositionPreview compositions={[empty, second]} workflowSteps={steps} /></ConfigProvider>);
    expect(screen.getByText('静态计算 — Recipe 组合')).toBeInTheDocument();
    expect(screen.getByText('dos — Recipe 组合')).toBeInTheDocument();
    expect(screen.getByText('未提供已选 Recipe 或用户补丁')).toBeInTheDocument();
    const controls = screen.getAllByRole('button', { name: /组合与覆盖详情/ });
    expect(controls).toHaveLength(2);
    await userEvent.setup().click(controls[0]);
    expect(screen.getByText('composition-static')).toBeVisible();
    expect(screen.queryByText('composition-dos')).not.toBeInTheDocument();
    expect(controls[1]).toHaveAttribute('aria-expanded', 'false');
  });
});
