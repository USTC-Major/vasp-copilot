// 密钥输入框 — 已保存时只显示掩码（不显示为空），点眼睛才按需取回原文（仅本机展示，不落本地存储）。
import React, { useState } from "react";
import { Button, Input, message } from "antd";
import { EyeOutlined, LoadingOutlined } from "@ant-design/icons";

const MASK_DOTS = "••••••••";

interface SecretInputProps {
  hasSecret: boolean;
  value: string;
  onChange: (v: string) => void;
  onReveal: () => Promise<string>;
  placeholder: string;
}

const SecretInput: React.FC<SecretInputProps> = ({ hasSecret, value, onChange, onReveal, placeholder }) => {
  const [revealed, setRevealed] = useState<boolean>(!hasSecret);
  const [shown, setShown] = useState<boolean>(false);
  const [revealing, setRevealing] = useState<boolean>(false);

  const handleReveal = async () => {
    if (revealing) return;
    setRevealing(true);
    try {
      const plain = await onReveal();
      onChange(plain);
      setRevealed(true);
      setShown(true);
    } catch (err) {
      message.error(err instanceof Error ? err.message : "无法读取已保存的密钥");
    } finally {
      setRevealing(false);
    }
  };

  // 未配置：普通密码输入框，直接填写。
  if (!hasSecret) {
    return (
      <Input.Password
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        autoComplete="new-password"
      />
    );
  }

  // 已配置但未“点眼睛”：只显示掩码，只读，不能直接编辑。
  if (!revealed) {
    return (
      <Input
        value={MASK_DOTS}
        readOnly
        suffix={
          <Button
            type="text"
            size="small"
            aria-label="显示原文"
            icon={revealing ? <LoadingOutlined /> : <EyeOutlined />}
            onClick={handleReveal}
          />
        }
        title="已保存；点击右侧眼睛临时查看原文"
      />
    );
  }

  // 已点眼睛：显示原文并按需编辑（编辑内容会在保存时写回）。
  return (
    <Input.Password
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      visibilityToggle={{ visible: shown, onVisibleChange: setShown }}
      autoComplete="new-password"
    />
  );
};

export default SecretInput;