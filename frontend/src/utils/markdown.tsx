// ============================================================
// 工具函数 — 轻量安全的 Markdown → ReactNode（AI 助手气泡用）
// 不引入第三方依赖：按代码块/标题/列表/段落分块；行内解析
// `code`、**粗体**、*斜体*、[文字](链接)。全程以 React 文本
// 节点输出（自动转义），不使用 dangerouslySetInnerHTML，防 XSS。
// ============================================================

import React from 'react';

const CODE_STYLE: React.CSSProperties = {
  display: 'block',
  background: '#f6f8fa',
  border: '1px solid #e6e6e6',
  borderRadius: 6,
  padding: '8px 10px',
  margin: '6px 0',
  fontSize: 12,
  lineHeight: 1.5,
  whiteSpace: 'pre',
  overflowX: 'auto',
  fontFamily: "'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace",
};

const INLINE_CODE_STYLE: React.CSSProperties = {
  background: '#f6f8fa',
  border: '1px solid #e6e6e6',
  borderRadius: 4,
  padding: '0 4px',
  fontSize: '0.92em',
  fontFamily: "'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace",
};

const HEADING_STYLE: Record<number, React.CSSProperties> = {
  1: { fontSize: 17, fontWeight: 700, margin: '8px 0 6px' },
  2: { fontSize: 15, fontWeight: 700, margin: '8px 0 6px' },
  3: { fontSize: 14, fontWeight: 600, margin: '6px 0 4px' },
  4: { fontSize: 13, fontWeight: 600, margin: '6px 0 4px' },
};

const LIST_STYLE: React.CSSProperties = {
  margin: '4px 0 8px',
  paddingLeft: 20,
};

const TABLE_WRAP_STYLE: React.CSSProperties = {
  overflowX: 'auto',
  margin: '4px 0 10px',
};

const TABLE_STYLE: React.CSSProperties = {
  width: '100%',
  borderCollapse: 'collapse',
  fontSize: 13,
};

const TABLE_CELL_STYLE: React.CSSProperties = {
  border: '1px solid #d9d9d9',
  padding: '6px 8px',
  verticalAlign: 'top',
  overflowWrap: 'anywhere',
};

// ---- 行内解析：`code`、**粗体**、*斜体*、[文字](链接) ----
function renderInline(text: string, keyPrefix: string): React.ReactNode[] {
  const tokenRe =
    /(`[^`\n]+`)|(\*\*[^*\n]+\*\*)|(\*[^*\n]+\*)|(\[([^\]\n]+)\]\(([^)\s\n]+)\))/g;
  const nodes: React.ReactNode[] = [];
  let lastIndex = 0;
  let counter = 0;
  let match: RegExpExecArray | null;

  while ((match = tokenRe.exec(text)) !== null) {
    if (match.index > lastIndex) {
      nodes.push(
        <span key={`${keyPrefix}-t${counter++}`}>
          {text.slice(lastIndex, match.index)}
        </span>
      );
    }
    if (match[1] != null) {
      nodes.push(
        <code key={`${keyPrefix}-c${counter++}`} style={INLINE_CODE_STYLE}>
          {match[1].slice(1, -1)}
        </code>
      );
    } else if (match[2] != null) {
      nodes.push(
        <strong key={`${keyPrefix}-b${counter++}`}>
          {renderInline(match[2].slice(2, -2), `${keyPrefix}-b${counter}`)}
        </strong>
      );
    } else if (match[3] != null) {
      nodes.push(
        <em key={`${keyPrefix}-i${counter++}`}>
          {renderInline(match[3].slice(1, -1), `${keyPrefix}-i${counter}`)}
        </em>
      );
    } else if (match[4] != null) {
      const href = match[6];
      if (/^(https?:)?\/\//.test(href) || href.startsWith('#')) {
        nodes.push(
          <a key={`${keyPrefix}-a${counter++}`} href={href} target="_blank" rel="noreferrer">
            {renderInline(match[5], `${keyPrefix}-a${counter}`)}
          </a>
        );
      } else {
        nodes.push(
          <span key={`${keyPrefix}-a${counter++}`}>{`[${match[5]}](${href})`}</span>
        );
      }
    }
    lastIndex = tokenRe.lastIndex;
  }

  if (lastIndex < text.length) {
    nodes.push(
      <span key={`${keyPrefix}-t${counter++}`}>{text.slice(lastIndex)}</span>
    );
  }
  return nodes;
}

function isListLine(line: string): boolean {
  return /^[-*]\s+/.test(line) || /^\d+\.\s+/.test(line);
}

function splitTableRow(line: string): string[] {
  const source = line.trim().replace(/^\|/, '').replace(/\|$/, '');
  const cells: string[] = [];
  let cell = '';
  let escaped = false;
  let inCode = false;
  for (const char of source) {
    if (escaped) {
      cell += char;
      escaped = false;
    } else if (char === '\\') {
      escaped = true;
    } else if (char === '`') {
      inCode = !inCode;
      cell += char;
    } else if (char === '|' && !inCode) {
      cells.push(cell.trim());
      cell = '';
    } else {
      cell += char;
    }
  }
  if (escaped) cell += '\\';
  cells.push(cell.trim());
  return cells;
}

function tableAlignments(line: string): Array<React.CSSProperties['textAlign']> | null {
  const cells = splitTableRow(line);
  if (!cells.length || cells.some((cell) => !/^:?-{3,}:?$/.test(cell.replace(/\s/g, '')))) return null;
  return cells.map((cell) => {
    const compact = cell.replace(/\s/g, '');
    if (compact.startsWith(':') && compact.endsWith(':')) return 'center';
    if (compact.endsWith(':')) return 'right';
    return 'left';
  });
}

function isTableStart(lines: string[], index: number): boolean {
  if (index + 1 >= lines.length || !lines[index].includes('|')) return false;
  const headers = splitTableRow(lines[index]);
  const alignments = tableAlignments(lines[index + 1]);
  return headers.length > 0 && alignments !== null && headers.length === alignments.length;
}

// ---- 主入口：块级解析（代码块/标题/表格/列表/段落） ----
export function renderMarkdown(text: string): React.ReactNode {
  const blocks: React.ReactNode[] = [];
  const lines = text.replace(/\r\n/g, '\n').replace(/\r/g, '\n').split('\n');
  let i = 0;
  let blockKey = 0;

  while (i < lines.length) {
    const line = lines[i];

    // 围栏代码块 ```lang
    const fence = line.match(/^```(\S*)/);
    if (fence) {
      const codeLines: string[] = [];
      i += 1;
      while (i < lines.length && !/^```/.test(lines[i])) {
        codeLines.push(lines[i]);
        i += 1;
      }
      if (i < lines.length) i += 1; // 跳过收尾 ```
      blocks.push(
        <pre key={`b${blockKey++}`} style={CODE_STYLE}>
          <code>{codeLines.join('\n')}</code>
        </pre>
      );
      continue;
    }

    // 空行
    if (!line.trim()) {
      i += 1;
      continue;
    }

    // 标题 # ~ ####
    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    if (heading) {
      const level = heading[1].length;
      const Tag = `h${level}` as keyof React.JSX.IntrinsicElements;
      blocks.push(
        React.createElement(
          Tag,
          { key: `b${blockKey}`, style: HEADING_STYLE[level] },
          renderInline(heading[2], `h${blockKey}`)
        )
      );
      blockKey += 1;
      i += 1;
      continue;
    }

    // GFM 表格：标题行后必须紧跟 --- 分隔行，避免把普通管道文本误判为表格。
    if (isTableStart(lines, i)) {
      const headers = splitTableRow(lines[i]);
      const alignments = tableAlignments(lines[i + 1]) ?? headers.map(() => 'left' as const);
      const rows: string[][] = [];
      i += 2;
      while (i < lines.length && lines[i].trim() && lines[i].includes('|')) {
        const row = splitTableRow(lines[i]);
        if (row.length !== headers.length) break;
        rows.push(row);
        i += 1;
      }
      const key = `b${blockKey++}`;
      blocks.push(
        <div key={key} style={TABLE_WRAP_STYLE}>
          <table style={TABLE_STYLE}>
            <thead>
              <tr>
                {headers.map((header, index) => (
                  <th key={`${key}-h${index}`} scope="col" style={{ ...TABLE_CELL_STYLE, textAlign: alignments[index], background: '#fafafa' }}>
                    {renderInline(header, `${key}-h${index}`)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, rowIndex) => (
                <tr key={`${key}-r${rowIndex}`}>
                  {row.map((cell, cellIndex) => (
                    <td key={`${key}-r${rowIndex}c${cellIndex}`} style={{ ...TABLE_CELL_STYLE, textAlign: alignments[cellIndex] }}>
                      {renderInline(cell, `${key}-r${rowIndex}c${cellIndex}`)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
      continue;
    }

    // 列表（- / * / 1. ）
    if (isListLine(line)) {
      const ordered = /^\d+\.\s+/.test(line);
      const items: React.ReactNode[] = [];
      while (i < lines.length && isListLine(lines[i])) {
        const bullet = lines[i].match(/^(?:[-*]|\d+\.)\s+(.*)$/);
        items.push(
          <li key={`li${items.length}`} style={{ marginBottom: 2 }}>
            {renderInline(bullet ? bullet[1] : lines[i], `li${items.length}`)}
          </li>
        );
        i += 1;
      }
      blocks.push(
        React.createElement(
          ordered ? 'ol' : 'ul',
          { key: `b${blockKey++}`, style: LIST_STYLE },
          items
        )
      );
      continue;
    }

    // 普通段落：收集到空行或下一个块起始
    const paraLines: string[] = [line];
    i += 1;
    while (
      i < lines.length &&
      lines[i].trim() &&
      !/^```/.test(lines[i]) &&
      !/^(#{1,4})\s+/.test(lines[i]) &&
      !isListLine(lines[i]) &&
      !isTableStart(lines, i)
    ) {
      paraLines.push(lines[i]);
      i += 1;
    }
    blocks.push(
      <p key={`b${blockKey++}`} style={{ margin: '0 0 8px' }}>
        {renderInline(paraLines.join('\n'), `p${blockKey}`)}
      </p>
    );
  }

  return <>{blocks}</>;
}
