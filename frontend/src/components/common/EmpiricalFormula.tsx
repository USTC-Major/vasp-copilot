// ============================================================
// EmpiricalFormula — 最简整数比化学式展示（元素 + 下标原子数）
// ============================================================

function gcd(a: number, b: number): number {
  let x = a;
  let y = b;
  while (y) {
    const t = x % y;
    x = y;
    y = t;
  }
  return x;
}

// 求各计数除以最大公约数后的最简整数比（无共同因子时保持原值）
export function reduceCounts(counts: number[]): number[] {
  if (counts.length === 0) return [];
  let g = counts[0] || 1;
  for (const n of counts.slice(1)) {
    g = gcd(g, n || 1);
  }
  return counts.map((n) => n / (g || 1));
}

// 单个元素：符号 + 右下角下标原子数（1 时不显示下标）
export function ElementSymbol({ symbol, count }: { symbol: string; count?: number }) {
  return (
    <span style={{ whiteSpace: 'nowrap' }}>
      {symbol}
      {count && count > 1 ? (
        <span style={{ fontSize: '0.75em', verticalAlign: 'sub' }}>{count}</span>
      ) : null}
    </span>
  );
}

interface EmpiricalFormulaProps {
  elements: string[];
  counts: number[];
}

// 化学式：按最简整数比拼接「元素 + 下标原子数」
export function EmpiricalFormula({ elements, counts }: EmpiricalFormulaProps) {
  return (
    <span>
      {elements.map((el, i) => (
        <span key={el}>
          <ElementSymbol symbol={el} count={counts[i]} />
        </span>
      ))}
    </span>
  );
}