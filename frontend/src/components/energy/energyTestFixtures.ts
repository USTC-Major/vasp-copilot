import type { EnergyCollection, EnergyCsvPreview, EnergySample } from '../../api/energy';

export function energyFixture(withResult = false): EnergyCollection {
  const sample = (id: string, name: string, composition: Record<string, number>, value: number, role: EnergySample['role']): EnergySample => ({
    id, name, revision: 1, source: { kind: 'manual', original_name: name, relative_path: '合成 中文/位点 A', sha256: `synthetic-${id}`, size_bytes: 0, imported_at: '2026-10-11T00:00:00Z', reference_note: '计量合成值，不是科研数据' }, energy_basis: 'sigma_to_zero_ev',
    parsed: { energy_fields: { sigma_to_zero_ev: value, without_entropy_ev: null, free_energy_toten_ev: null }, composition,
      status: { completion: 'unknown', electronic_converged: null, ionic_converged: null, ionic_applicability: 'unknown' }, metadata: { method: 'unknown', support: { automatic_comparison: true, reasons: [] } }, provenance: { kind: 'manual' }, errors: [], warnings: ['人工值：运行及收敛状态未经自动核查'], issues: [] },
    override: null, role_suggestion: { role: null, reasons: ['证据不足，请人工确认'], confidence: 'unknown' }, role, included: true, confirmed: withResult, accepted_warnings: withResult, confirmation_fingerprint: withResult ? 'synthetic-confirmed' : null,
  });
  const samples = [sample('es_clean', '合成表面', { Pt: 4 }, -100, 'clean_slab'), sample('es_reference', '合成 CO 参考', { C: 1, O: 1 }, -10, 'adsorbate'), sample('es_target', '合成构型 A', { Pt: 4, C: 1, O: 1 }, -112, 'adsorbed')];
  const groups: EnergyCollection['groups'] = [{ id: 'g_ads', name: '合成 CO 比较', kind: 'adsorption', energy_basis: 'sigma_to_zero_ev', basis_confirmed: withResult, reference_note: '每个 CO 分子，合成计量', reference_units: 1, clean_sample_id: 'es_clean', adsorbate_sample_id: 'es_reference', targets: [{ sample_id: 'es_target', adsorbate_count: 1 }] }];
  return { id: 'ec_fixture', schema_version: 'pp.energy.v1', title: '合成能量比较', revision: 4, created_at: '2026-10-11T00:00:00Z', updated_at: '2026-10-11T00:00:00Z', samples, groups,
    limits: { max_file_bytes: 64 * 1024 ** 2, max_collection_bytes: 128 * 1024 ** 2, max_samples: 100 },
    result: withResult ? { schema_version: 'pp.energy.result.v1', input_fingerprint: 'synthetic-input', calculated_at: '2026-10-11T00:01:00Z', warnings: ['合成值，运行未完成或结束状态未知'], groups: [{ id: 'g_ads', name: groups[0].name, kind: 'adsorption', energy_basis: 'sigma_to_zero_ev', warnings: [], rows: [{ sample_id: 'es_target', name: '合成构型 A', delta_ev: -2, normalized_ev: -2, normalization: 'per_adsorbate', unit: 'eV/adsorbate', formula: 'E_target - E_clean - n * E_reference / m', terms: [{ sample_id: 'es_target', coefficient: 1, energy_ev: -112, contribution_ev: -112 }, { sample_id: 'es_clean', coefficient: -1, energy_ev: -100, contribution_ev: 100 }, { sample_id: 'es_reference', coefficient: -1, energy_ev: -10, contribution_ev: 10 }], warnings: ['合成构型 A：运行及收敛状态未知'] }] }] } : null,
  };
}

export function energyCsvFixture(): EnergyCsvPreview {
  return { row_count: 3, valid_count: 3, can_import: true, issues: [], rows: energyFixture().samples.map((sample, index) => ({
    row_number: index + 2, name: sample.name, composition: sample.parsed.composition, energy_basis: 'sigma_to_zero_ev',
    energy_ev: sample.parsed.energy_fields.sigma_to_zero_ev, unit: 'eV', relative_path: '合成 中文/位点 A', reference_note: '测试合成值，不是科研数据', issues: [],
  })) };
}
