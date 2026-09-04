// Write-only secret control: saved values are never revealed, copied, or restored.
import React from "react";
import { Button, Input, Space, Tag, Typography } from "antd";

interface SecretInputProps {
  hasSecret: boolean;
  value: string;
  onChange: (value: string) => void;
  onClear: () => void | Promise<void>;
  placeholder: string;
  clearing?: boolean;
  manageable?: boolean;
  source?: string;
}

const SecretInput: React.FC<SecretInputProps> = ({
  hasSecret, value, onChange, onClear, placeholder, clearing,
  manageable = true, source = "none",
}) => (
  <Space direction="vertical" size={2} style={{ width: "100%" }}>
    <Space.Compact style={{ width: "100%" }}>
      <Input
        type="password"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        autoComplete="new-password"
        aria-label="输入新的密钥以整体替换"
        disabled={!manageable}
        suffix={source === "environment"
          ? <Tag color="blue">环境变量</Tag>
          : hasSecret ? <Tag color="green">已保存</Tag> : <Tag>未配置</Tag>}
      />
      <Button danger disabled={!hasSecret || !manageable} loading={clearing} onClick={onClear}>
        清除
      </Button>
    </Space.Compact>
    {!manageable && source === "environment" ? (
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        由环境变量管理；请在运行环境中替换或清除。
      </Typography.Text>
    ) : null}
  </Space>
);

export default SecretInput;
