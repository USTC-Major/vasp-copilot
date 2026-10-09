import { useEffect, useState } from 'react';
import { Input } from 'antd';
import { parseNumericDraft } from './viewState';

/** Keep unfinished input visible; never coerce an empty field into zero. */
export default function NumericDraftInput({ label, value, onChange }: { label: string; value: number | undefined; onChange: (value: number) => void }) {
  const [text, setText] = useState(() => Number.isFinite(value) ? String(value) : '');
  useEffect(() => {
    if (Number.isFinite(value) && parseNumericDraft(text) !== value) setText(String(value));
  }, [value, text]);
  return <Input aria-label={label} inputMode="decimal" value={text} onChange={event => {
    const next = event.target.value;
    setText(next);
    onChange(parseNumericDraft(next));
  }} />;
}
